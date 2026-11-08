import sqlite3
import json
from contextlib import contextmanager
from app.config import settings

def get_connection():
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging and busy timeout for safe concurrent access
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Initializes tables for assessment projects, documents, BPMN models, and gap analysis results."""
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
            -- Group 1: Scope & Strukturanalyse (Process vs. Application)
            q01_raw_entry TEXT NOT NULL,           -- Verbatim cell, e.g. 'Gutscheinsystem'
            q02_entry_kind TEXT DEFAULT 'UNKNOWN', -- 'PROCESS', 'APPLICATION', 'CATEGORY'
            q03_process_name TEXT,                 -- Actual business process if q02 is APPLICATION
            q04_owner_role TEXT,                   -- Responsible department / role
            -- Group 2: Trigger & Handbook vs. Lived Reality
            q05_trigger_event TEXT,                -- How the process starts
            q06_in_handbook TEXT,                  -- 'YES', 'NO', 'PARTIAL'
            q07_lived_reality_notes TEXT,          -- Verbatim day-to-day process steps
            -- Group 3: Data Assets & Storage (VVT)
            q08_input_data_mandatory TEXT,         -- Data required to start/complete
            q09_input_data_optional TEXT,          -- Data that can be missing while process still completes
            q10_output_data TEXT,                  -- Data produced at the end
            q11_storage_location TEXT,             -- Where data is stored (system, DB, share, paper)
            -- Group 4: Exceptions & Repeatability
            q12_exception_path TEXT,               -- What happens if data is missing or rejected
            q13_is_repeatable INTEGER DEFAULT 0,   -- 1 if activities can repeat with different outcomes
            -- Group 5: Criticality (BSI 200-2 CIA & BSI 200-4 BIA)
            q14_raw_criticality TEXT,              -- Verbatim label from Excel, e.g. 'kritisch'
            q15_cia_confidentiality TEXT,          -- 'Normal', 'High', 'Very High' + reason
            q16_cia_integrity TEXT,                -- 'Normal', 'High', 'Very High' + reason
            q17_cia_availability TEXT,             -- 'Normal', 'High', 'Very High' + reason
            q18_bia_mtpd_rto TEXT,                 -- Max tolerable downtime (MTPD) & target recovery (RTO)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS accepted_concepts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            concept_type TEXT NOT NULL, -- 'SYSTEM', 'DATA_ASSET', 'ROLE', 'TRIGGER'
            source_text TEXT NOT NULL,  -- Original verbatim text from participant
            label TEXT NOT NULL,        -- Moderated display label for checklist/radio injection
            status TEXT DEFAULT 'ACCEPTED', -- 'PENDING', 'ACCEPTED', 'REJECTED'
            session_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_survey_project_order ON process_survey_rows(project_id, row_order);
        CREATE INDEX IF NOT EXISTS idx_concepts_lookup ON accepted_concepts(project_id, concept_type, status);

        -- ------------------------------------------------------------------
        -- Multi-standard reference catalog and master discovery survey.
        -- req_id is the single shared key: standards_requirements.req_id is
        -- the primary key, survey_questions and entity_requirement_assessments
        -- hold it as a foreign key. Nothing else is joined across layers.
        -- ------------------------------------------------------------------
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

        -- Discovered organisational entities: processes, sub-processes,
        -- activities, systems, data assets and roles, in a parent hierarchy.
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

        -- Directed links used for inheritance: a system SUPPORTS a process,
        -- a process USES_DATA a data asset, and so on.
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

        -- One answer per (entity, requirement). status is produced by the
        -- deterministic rule engine, never guessed by the UI.
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

        CREATE INDEX IF NOT EXISTS idx_requirements_standard ON standards_requirements(standard_code, req_id);
        CREATE INDEX IF NOT EXISTS idx_requirements_phase ON standards_requirements(phase_category, req_id);
        CREATE INDEX IF NOT EXISTS idx_questions_sequence ON survey_questions(sequence_order);
        CREATE INDEX IF NOT EXISTS idx_entities_project_type ON org_entities(project_id, entity_type, name);
        CREATE INDEX IF NOT EXISTS idx_entities_parent ON org_entities(parent_id);
        CREATE INDEX IF NOT EXISTS idx_dependencies_project_from ON entity_dependencies(project_id, from_entity_id, dependency_type);
        CREATE INDEX IF NOT EXISTS idx_dependencies_project_to ON entity_dependencies(project_id, to_entity_id, dependency_type);
        CREATE INDEX IF NOT EXISTS idx_assessments_project_status ON entity_requirement_assessments(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_assessments_entity ON entity_requirement_assessments(entity_id, req_id);
        """)

