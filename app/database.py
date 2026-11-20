"""Unified SQLite persistence layer for PMGA v2.

Consolidates legacy tables (for full backwards compatibility) alongside the
v2 single-source-of-truth project graph:
- process_models: multi-process BPMN IR + XML + narrative per entity/project
- session_turns: durable multi-session conversation history
- evidence_items: quote-backed provenance ledger (DOCUMENT_CLAIM / USER_CONFIRMED / CONTRADICTED)
- remediation_gaps: BSI/ISO gaps bound to specific BPMN node IDs and action steps
- brand_profiles: customizable logo/colors/font/footer for Jinja2 & WeasyPrint exports
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict

from app.config import settings
from app.errors import PersistenceError

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(settings.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn
    except sqlite3.Error as exc:
        logger.error("Failed to open SQLite connection at %s: %s", settings.db_path, exc, exc_info=True)
        raise PersistenceError(f"Database connection failed: {exc}") from exc


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except sqlite3.Error as rb_exc:
            logger.error("SQLite rollback failed: %s", rb_exc)
        if isinstance(exc, sqlite3.Error):
            logger.error("SQLite transaction error: %s", exc, exc_info=True)
        raise
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_ddl: str) -> None:
    """Non-destructive migration guard: adds a column if an older DB lacks it."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
        logger.info("Migrated table %s: added missing column %s", table, column)


def init_db() -> None:
    """Initializes legacy and v2 unified tables, indexes, and migration guards."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            target_framework TEXT DEFAULT 'ISO/IEC 27002:2022',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT,
            doc_type TEXT NOT NULL, -- 'ACTUAL_PROCESS', 'FORMAL_POLICY', 'EVIDENCE_JOURNAL'
            extracted_text TEXT NOT NULL,
            redacted_text TEXT,
            pii_detected_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS bpmn_models (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            bpmn_xml TEXT NOT NULL,
            graph_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS gap_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            control_id TEXT NOT NULL,
            control_title TEXT NOT NULL,
            chapter TEXT NOT NULL,
            status TEXT NOT NULL, -- 'COMPLIANT', 'PARTIAL_GAP', 'CRITICAL_GAP', 'COMPENSATING_CONTROL'
            policy_claim TEXT,
            operational_reality TEXT,
            plain_english_gap TEXT NOT NULL,
            actionable_todos_json TEXT NOT NULL,
            roles_to_consult_json TEXT NOT NULL,
            compensating_controls_json TEXT,
            user_decision TEXT,
            user_decision_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS process_survey_rows (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            sheet_name TEXT DEFAULT 'General',
            row_order INTEGER DEFAULT 1,
            q01_raw_entry TEXT NOT NULL,
            q02_entry_kind TEXT DEFAULT 'UNKNOWN',
            q03_process_name TEXT,
            q04_owner_role TEXT,
            q05_trigger_event TEXT,
            q06_in_handbook TEXT,
            q07_lived_reality_notes TEXT,
            q08_input_data_mandatory TEXT,
            q09_input_data_optional TEXT,
            q10_output_data TEXT,
            q11_storage_location TEXT,
            q12_exception_path TEXT,
            q13_is_repeatable INTEGER DEFAULT 0,
            q14_raw_criticality TEXT,
            q15_cia_confidentiality TEXT,
            q16_cia_integrity TEXT,
            q17_cia_availability TEXT,
            q18_bia_mtpd_rto TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS accepted_concepts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            concept_type TEXT NOT NULL,
            source_text TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT DEFAULT 'ACCEPTED',
            session_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_survey_project_order ON process_survey_rows(project_id, row_order);
        CREATE INDEX IF NOT EXISTS idx_concepts_lookup ON accepted_concepts(project_id, concept_type, status);

        CREATE TABLE IF NOT EXISTS standards_requirements (
            req_id TEXT PRIMARY KEY,
            standard_code TEXT NOT NULL,
            clause_ref TEXT NOT NULL,
            title TEXT NOT NULL,
            target_scope TEXT NOT NULL,
            phase_category TEXT NOT NULL,
            formal_requirement TEXT NOT NULL,
            plain_english_requirement TEXT NOT NULL,
            question_text TEXT NOT NULL,
            validation_rule TEXT NOT NULL,
            evidence_needed TEXT,
            common_gaps TEXT,
            compensating_controls_template TEXT,
            concept_type TEXT,
            allowed_options_json TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS survey_questions (
            question_id TEXT PRIMARY KEY,
            req_id TEXT NOT NULL UNIQUE,
            sequence_order INTEGER NOT NULL,
            phase_category TEXT NOT NULL,
            target_scope TEXT NOT NULL,
            question_text TEXT NOT NULL,
            allowed_options_json TEXT NOT NULL DEFAULT '[]',
            validation_rule TEXT NOT NULL,
            concept_type TEXT,
            standard_code TEXT NOT NULL,
            clause_ref TEXT NOT NULL,
            plain_english_requirement TEXT NOT NULL,
            FOREIGN KEY (req_id) REFERENCES standards_requirements(req_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS org_entities (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            parent_id TEXT,
            owner_role TEXT,
            source_survey_row_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES org_entities(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS entity_dependencies (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            from_entity_id TEXT NOT NULL,
            to_entity_id TEXT NOT NULL,
            dependency_type TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (from_entity_id) REFERENCES org_entities(id) ON DELETE CASCADE,
            FOREIGN KEY (to_entity_id) REFERENCES org_entities(id) ON DELETE CASCADE,
            UNIQUE (from_entity_id, to_entity_id, dependency_type)
        );

        CREATE TABLE IF NOT EXISTS entity_requirement_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            req_id TEXT NOT NULL,
            answer_value TEXT,
            answer_numeric REAL,
            justification TEXT,
            status TEXT NOT NULL DEFAULT 'NEEDS_USER_INPUT',
            inherited_from_entities_json TEXT,
            evidence_json TEXT,
            answered_by TEXT,
            answered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (entity_id) REFERENCES org_entities(id) ON DELETE CASCADE,
            FOREIGN KEY (req_id) REFERENCES standards_requirements(req_id) ON DELETE CASCADE,
            UNIQUE (entity_id, req_id)
        );

        -- ------------------------------------------------------------------
        -- PMGA v2 Unified Single-Source-of-Truth Tables (Milestone 1)
        -- ------------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS process_models (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            entity_id TEXT,
            title TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            ir_json TEXT NOT NULL DEFAULT '{}',
            bpmn_xml TEXT NOT NULL DEFAULT '',
            description_text TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (entity_id) REFERENCES org_entities(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS session_turns (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            role TEXT NOT NULL, -- 'user' | 'coach' | 'system'
            content TEXT NOT NULL,
            input_kind TEXT NOT NULL DEFAULT 'chat', -- 'chat' | 'excel' | 'document' | 'bpmn_edit'
            patch_summary_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS evidence_items (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            entity_id TEXT,
            document_id TEXT,
            req_id TEXT,
            claim_text TEXT NOT NULL,
            source_quote TEXT NOT NULL,
            source_location TEXT,
            status TEXT NOT NULL DEFAULT 'DOCUMENT_CLAIM', -- 'DOCUMENT_CLAIM' | 'USER_CONFIRMED' | 'CONTRADICTED'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (entity_id) REFERENCES org_entities(id) ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS remediation_gaps (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            process_model_id TEXT,
            entity_id TEXT,
            req_id TEXT NOT NULL,
            standard_clause TEXT NOT NULL,
            affected_node_ids_json TEXT NOT NULL DEFAULT '[]',
            severity TEXT NOT NULL DEFAULT 'MODERATE', -- 'CRITICAL' | 'HIGH' | 'MODERATE' | 'INFO'
            priority_bucket TEXT NOT NULL DEFAULT 'BEFORE_DIGITALIZATION', -- 'QUICK_WIN' | 'BEFORE_DIGITALIZATION' | 'ONGOING'
            gap_category TEXT NOT NULL DEFAULT 'GOVERNANCE',
            warm_summary TEXT NOT NULL,
            policy_vs_reality TEXT,
            action_steps_json TEXT NOT NULL DEFAULT '[]',
            roles_to_involve_json TEXT NOT NULL DEFAULT '[]',
            compensating_options_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'OPEN', -- 'OPEN' | 'IN_PROGRESS' | 'ACCEPTED_RISK' | 'RESOLVED'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (process_model_id) REFERENCES process_models(id) ON DELETE SET NULL,
            FOREIGN KEY (entity_id) REFERENCES org_entities(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS brand_profiles (
            project_id TEXT PRIMARY KEY,
            org_name TEXT NOT NULL DEFAULT 'Municipal Administration',
            logo_data_uri TEXT,
            primary_color TEXT NOT NULL DEFAULT '#1E4E68',
            accent_color TEXT NOT NULL DEFAULT '#C86D44',
            font_family TEXT NOT NULL DEFAULT 'Plus Jakarta Sans',
            footer_text TEXT DEFAULT 'Prepared with PMGA Resilience & Process Workbench',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_requirements_standard ON standards_requirements(standard_code, req_id);
        CREATE INDEX IF NOT EXISTS idx_requirements_phase ON standards_requirements(phase_category, req_id);
        CREATE INDEX IF NOT EXISTS idx_questions_sequence ON survey_questions(sequence_order);
        CREATE INDEX IF NOT EXISTS idx_entities_project_type ON org_entities(project_id, entity_type, name);
        CREATE INDEX IF NOT EXISTS idx_entities_parent ON org_entities(parent_id);
        CREATE INDEX IF NOT EXISTS idx_dependencies_project_from ON entity_dependencies(project_id, from_entity_id, dependency_type);
        CREATE INDEX IF NOT EXISTS idx_dependencies_project_to ON entity_dependencies(project_id, to_entity_id, dependency_type);
        CREATE INDEX IF NOT EXISTS idx_assessments_project_status ON entity_requirement_assessments(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_assessments_entity ON entity_requirement_assessments(entity_id, req_id);
        CREATE INDEX IF NOT EXISTS idx_process_models_project ON process_models(project_id, is_primary);
        CREATE INDEX IF NOT EXISTS idx_session_turns_project ON session_turns(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence_items(project_id, status, req_id);
        CREATE INDEX IF NOT EXISTS idx_remediation_gaps_project ON remediation_gaps(project_id, severity, status);
        """)

        # Non-destructive column guards for existing SQLite databases
        _ensure_column(conn, "remediation_gaps", "priority_bucket", "priority_bucket TEXT NOT NULL DEFAULT 'BEFORE_DIGITALIZATION'")
        _ensure_column(conn, "org_entities", "metadata_json", "metadata_json TEXT NOT NULL DEFAULT '{}'")


def purge_project_data(project_id: str) -> Dict[str, Any]:
    """Expunges all legacy, v2, uploaded file, and LangGraph checkpoint data for a project (GDPR Art. 17)."""
    removed_files = 0
    failed_files = 0
    with get_db() as conn:
        docs = conn.execute("SELECT file_path FROM documents WHERE project_id = ?", (project_id,)).fetchall()
        for d in docs:
            fp = d["file_path"]
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                    removed_files += 1
                except OSError as exc:
                    failed_files += 1
                    logger.warning("Failed to remove uploaded file %s during GDPR purge: %s", fp, exc)

        # Delete child tables explicitly as defense-in-depth alongside ON DELETE CASCADE
        for table in (
            "remediation_gaps",
            "evidence_items",
            "session_turns",
            "process_models",
            "brand_profiles",
            "entity_requirement_assessments",
            "entity_dependencies",
            "org_entities",
            "gap_assessments",
            "bpmn_models",
            "process_survey_rows",
            "accepted_concepts",
            "documents",
        ):
            conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
        res = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        project_deleted = res.rowcount > 0

    # Also purge LangGraph SqliteSaver checkpoint state for thread_id = project_id
    cp_path = settings.checkpoint_db_path
    checkpoints_purged = 0
    if os.path.exists(cp_path):
        try:
            cp_conn = sqlite3.connect(cp_path, timeout=30.0)
            try:
                tables = {
                    r[0]
                    for r in cp_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                for cp_table in ("checkpoints", "writes", "checkpoint_blobs"):
                    if cp_table in tables:
                        r = cp_conn.execute(f"DELETE FROM {cp_table} WHERE thread_id = ?", (project_id,))
                        checkpoints_purged += r.rowcount
                cp_conn.commit()
            finally:
                cp_conn.close()
        except sqlite3.Error as exc:
            logger.error("Failed to purge LangGraph checkpoint DB for project %s: %s", project_id, exc, exc_info=True)
            raise PersistenceError(f"Failed to purge workflow checkpoints: {exc}") from exc

    logger.info(
        "GDPR purge completed for project=%s deleted=%s removed_files=%d failed_files=%d checkpoints_purged=%d",
        project_id,
        project_deleted,
        removed_files,
        failed_files,
        checkpoints_purged,
    )
    return {
        "project_deleted": project_deleted,
        "removed_files": removed_files,
        "failed_files": failed_files,
        "checkpoints_purged": checkpoints_purged,
    }
