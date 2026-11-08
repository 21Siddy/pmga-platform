"""Reference-catalog persistence and query layer.

``ensure_catalog_ready`` is idempotent: it writes the four reference
workbooks plus the master survey if they are missing, then upserts the
rows into SQLite. It is safe to call on every request and from tests that
never run the application lifespan.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_db, init_db
from app.seed.standards_and_survey_catalog import (
    SURVEY_COLUMNS,
    ensure_standards_and_survey,
    load_requirement_catalog,
    load_survey_catalog,
    standard_workbook_paths,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_ready = False

REQUIREMENT_INSERT_ORDER = (
    "req_id",
    "standard_code",
    "clause_ref",
    "title",
    "target_scope",
    "phase_category",
    "formal_requirement",
    "plain_english_requirement",
    "question_text",
    "validation_rule",
    "evidence_needed",
    "common_gaps",
    "compensating_controls_template",
    "concept_type",
)

QUESTION_INSERT_ORDER = (
    "question_id",
    "req_id",
    "sequence_order",
    "phase_category",
    "target_scope",
    "question_text",
    "allowed_options_json",
    "validation_rule",
    "concept_type",
    "standard_code",
    "clause_ref",
    "plain_english_requirement",
)


def _options_json(options: Any) -> str:
    if isinstance(options, str):
        return options
    return json.dumps(list(options or []), ensure_ascii=False)


def _sync_rows(catalog: List[Dict[str, Any]], questions: List[Dict[str, Any]]) -> None:
    """Upsert the catalog and survey into SQLite inside one transaction."""
    req_placeholders = ", ".join("?" for _ in REQUIREMENT_INSERT_ORDER)
    req_columns = ", ".join(REQUIREMENT_INSERT_ORDER)
    req_updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in REQUIREMENT_INSERT_ORDER
        if column != "req_id"
    )
    q_placeholders = ", ".join("?" for _ in QUESTION_INSERT_ORDER)
    q_columns = ", ".join(QUESTION_INSERT_ORDER)
    q_updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in QUESTION_INSERT_ORDER
        if column != "question_id"
    )

    with get_db() as conn:
        for req in catalog:
            conn.execute(
                f"INSERT INTO standards_requirements ({req_columns}) VALUES ({req_placeholders}) "
                f"ON CONFLICT(req_id) DO UPDATE SET {req_updates}",
                tuple(
                    _options_json(req.get(column, "")) if column == "allowed_options" else req.get(column, "")
                    for column in REQUIREMENT_INSERT_ORDER
                ),
            )
        for question in questions:
            conn.execute(
                f"INSERT INTO survey_questions ({q_columns}) VALUES ({q_placeholders}) "
                f"ON CONFLICT(question_id) DO UPDATE SET {q_updates}",
                tuple(
                    _options_json(question.get(column, ""))
                    if column == "allowed_options_json"
                    else question.get(column, "")
                    for column in QUESTION_INSERT_ORDER
                ),
            )


def ensure_catalog_ready(force: bool = False) -> Dict[str, Any]:
    """Create artefacts and rows if needed. Idempotent and thread-safe."""
    global _ready
    if _ready and not force:
        return _state()

    with _lock:
        if _ready and not force:
            return _state()

        init_db()
        written = ensure_standards_and_survey(settings.DATA_DIR, force=force)

        # Read the workbooks back instead of trusting the in-memory builder:
        # that is what makes the Excel files auditable, not decorative.
        catalog = load_requirement_catalog(os.path.join(settings.DATA_DIR, "standards"))
        questions = load_survey_catalog(os.path.join(settings.DATA_DIR, "surveys", "Master_Discovery_Survey.xlsx"))

        if [req["req_id"] for req in catalog] != [f"R{index:03d}" for index in range(1, 134)]:
            raise ValueError("Reference workbooks do not contain a contiguous R001..R133 catalog")

        _sync_rows(catalog, questions)
        _ready = True
        logger.info(
            "Standards catalog ready: %d requirements, %d survey questions, %d files written",
            len(catalog),
            len(questions),
            len(written.get("written", [])),
        )
        return _state()


def _state() -> Dict[str, Any]:
    with get_db() as conn:
        requirement_count = conn.execute("SELECT COUNT(*) AS c FROM standards_requirements").fetchone()["c"]
        question_count = conn.execute("SELECT COUNT(*) AS c FROM survey_questions").fetchone()["c"]
    return {
        "requirement_count": requirement_count,
        "question_count": question_count,
        "workbook_paths": standard_workbook_paths(settings.DATA_DIR),
    }


def reset_cache() -> None:
    """Test helper: force the next ``ensure_catalog_ready`` call to re-run."""
    global _ready
    _ready = False


def list_requirements(
    standard_code: Optional[str] = None,
    phase_category: Optional[str] = None,
    target_scope: Optional[str] = None,
) -> List[Dict[str, Any]]:
    ensure_catalog_ready()
    query = "SELECT * FROM standards_requirements"
    clauses: List[str] = []
    params: List[Any] = []
    if standard_code:
        clauses.append("standard_code = ?")
        params.append(standard_code)
    if phase_category:
        clauses.append("phase_category = ?")
        params.append(phase_category)
    if target_scope:
        clauses.append("target_scope = ?")
        params.append(target_scope)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY req_id"

    with get_db() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_decode_requirement(dict(row)) for row in rows]


def get_requirement(req_id: str) -> Optional[Dict[str, Any]]:
    ensure_catalog_ready()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM standards_requirements WHERE req_id = ?", (req_id,)).fetchone()
    return _decode_requirement(dict(row)) if row else None


def list_survey_questions(phase_category: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_catalog_ready()
    query = "SELECT * FROM survey_questions"
    params: List[Any] = []
    if phase_category:
        query += " WHERE phase_category = ?"
        params.append(phase_category)
    query += " ORDER BY sequence_order"

    with get_db() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_decode_question(dict(row)) for row in rows]


def _decode_requirement(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("allowed_options_json") or "[]"
    try:
        row["allowed_options"] = json.loads(raw)
    except (TypeError, ValueError):
        row["allowed_options"] = []
    row.pop("allowed_options_json", None)
    return row


def _decode_question(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("allowed_options_json") or "[]"
    try:
        row["allowed_options"] = json.loads(raw)
    except (TypeError, ValueError):
        row["allowed_options"] = []
    row.pop("allowed_options_json", None)
    return row


def workbook_download_path(token: str) -> Optional[str]:
    """Resolve an allowlisted download token to an existing workbook path."""
    ensure_catalog_ready()
    path = standard_workbook_paths(settings.DATA_DIR).get(token)
    if path and os.path.exists(path):
        return path
    return None


__all__ = [
    "SURVEY_COLUMNS",
    "ensure_catalog_ready",
    "get_requirement",
    "list_requirements",
    "list_survey_questions",
    "reset_cache",
    "workbook_download_path",
]
