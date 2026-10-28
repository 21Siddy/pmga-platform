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
        """)

