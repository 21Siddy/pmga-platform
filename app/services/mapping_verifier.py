"""Deterministic requirement evaluation and cross-layer mapping verification.

Two jobs, both pure and testable without a network or an LLM:

1. ``evaluate_rule`` / ``evaluate_assessment`` turn a stored survey answer
   into a status using a small fixed rule vocabulary.
2. ``verify_catalog`` proves that the reference workbooks, the survey and
   SQLite are the same catalog joined on ``req_id``, and that the rule
   engine produces the expected status for the canonical cases.

Nothing here guesses. If an input is missing the result is
``NEEDS_USER_INPUT``, never a silent pass.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.config import settings

STATUS_COMPLIANT = "COMPLIANT"
STATUS_PARTIAL_GAP = "PARTIAL_GAP"
STATUS_CRITICAL_GAP = "CRITICAL_GAP"
STATUS_COMPENSATING = "COMPENSATING_CONTROL"
STATUS_NEEDS_INPUT = "NEEDS_USER_INPUT"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

ALL_STATUSES: Tuple[str, ...] = (
    STATUS_COMPLIANT,
    STATUS_PARTIAL_GAP,
    STATUS_CRITICAL_GAP,
    STATUS_COMPENSATING,
    STATUS_NEEDS_INPUT,
    STATUS_NOT_APPLICABLE,
)

CIA_LEVELS: Dict[str, int] = {"Normal": 1, "High": 2, "Very High": 3}
YES_NO_PARTIAL: Tuple[str, ...] = ("YES", "NO", "PARTIAL")

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


# ---------------------------------------------------------------------------
# Scalar parsing
# ---------------------------------------------------------------------------

def parse_number(value: Any) -> Optional[float]:
    """Parse a survey numeric cell.

    Accepts ``"24"``, ``"24.5"``, ``"24,5"``, ``"24 h"`` and ``"2 days"``
    (days are converted to hours). Returns ``None`` when no number can be
    read, so the caller can escalate instead of inventing a value.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    number = float(match.group(0).replace(",", "."))
    if "tag" in text or "day" in text:
        return number * 24.0
    if "woche" in text or "week" in text:
        return number * 168.0
    return number


def parse_cia(value: Any) -> Optional[str]:
    """Normalise a CIA answer to one of ``Normal``/``High``/``Very High``."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith("sehr") or "very high" in text or text == "sehr hoch":
        return "Very High"
    if text.startswith("hoch") or text == "high":
        return "High"
    if text.startswith("normal") or text == "normal":
        return "Normal"
    return None


def parse_yes_no_partial(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in YES_NO_PARTIAL:
        return text
    if text in ("JA", "TRUE", "1"):
        return "YES"
    if text in ("NEIN", "FALSE", "0"):
        return "NO"
    if text.startswith("TEIL") or text == "PARTLY":
        return "PARTIAL"
    return None


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def _result(status: str, reason: str) -> Dict[str, str]:
    return {"status": status, "reason": reason}


def evaluate_rule(
    rule: str,
    answer_value: Any = None,
    answer_numeric: Any = None,
    justification: Any = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Evaluate one requirement rule and return ``{status, reason}``.

    Every branch is total: an unusable input yields ``NEEDS_USER_INPUT``.
    """
    context = context or {}
    text = "" if answer_value is None else str(answer_value).strip()

    if rule == "NON_EMPTY":
        if not text:
            return _result(STATUS_NEEDS_INPUT, "No answer recorded.")
        return _result(STATUS_COMPLIANT, "Answer recorded.")

    if rule == "ENUM_CIA":
        level = parse_cia(text)
        if level is None:
            return _result(STATUS_NEEDS_INPUT, "Confidentiality/integrity/availability level not stated.")
        if not (justification or "").strip():
            return _result(STATUS_PARTIAL_GAP, f"Level '{level}' stated without a written justification.")
        return _result(STATUS_COMPLIANT, f"Level '{level}' with justification.")

    if rule == "POSITIVE_NUMBER":
        number = parse_number(answer_numeric if answer_numeric is not None else text)
        if number is None:
            return _result(STATUS_NEEDS_INPUT, "No numeric value recorded.")
        if number <= 0:
            return _result(STATUS_CRITICAL_GAP, "Value must be greater than zero.")
        return _result(STATUS_COMPLIANT, f"Positive value {number:g} recorded.")

    if rule == "RTO_LT_MTPD":
        rto = parse_number(context.get("rto") if context.get("rto") is not None else answer_numeric)
        mtpd = parse_number(context.get("mtpd") if context.get("mtpd") is not None else answer_value)
        if rto is None:
            return _result(STATUS_NEEDS_INPUT, "No RTO recorded.")
        if mtpd is None:
            return _result(STATUS_NEEDS_INPUT, "No MTPD recorded, so RTO cannot be validated.")
        if rto < mtpd:
            return _result(STATUS_COMPLIANT, f"RTO {rto:g} is below MTPD {mtpd:g}.")
        # RTO equal to or above MTPD means the plan accepts unacceptable damage.
        return _result(STATUS_CRITICAL_GAP, f"RTO {rto:g} is not below MTPD {mtpd:g}.")

    if rule == "CIA_INHERITANCE_MAX":
        declared = parse_cia(context.get("declared") if context.get("declared") is not None else answer_value)
        inherited = parse_cia(context.get("inherited") if context.get("inherited") is not None else answer_numeric)
        if declared is None:
            return _result(STATUS_NEEDS_INPUT, "Declared protection need not stated.")
        if inherited is None:
            return _result(STATUS_NEEDS_INPUT, "No dependent process to inherit from.")
        if CIA_LEVELS[declared] >= CIA_LEVELS[inherited]:
            return _result(STATUS_COMPLIANT, f"Declared '{declared}' meets inherited maximum '{inherited}'.")
        return _result(
            STATUS_CRITICAL_GAP,
            f"Declared '{declared}' is below the inherited maximum '{inherited}' from dependent processes.",
        )

    if rule == "MUST_LINK_PROCESS":
        links = context.get("links")
        if links is None:
            links = context.get("dependency_count")
        if links is None:
            return _result(STATUS_NEEDS_INPUT, "No process link recorded.")
        try:
            count = int(links)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return _result(STATUS_CRITICAL_GAP, "Entity is not linked to any process.")
        return _result(STATUS_COMPLIANT, f"{count} process link(s) recorded.")

    if rule == "ROLE_ASSIGNED":
        role = (context.get("owner_role") or text or "").strip()
        if not role:
            return _result(STATUS_CRITICAL_GAP, "No owner role assigned.")
        return _result(STATUS_COMPLIANT, f"Owner role '{role}' assigned.")

    if rule == "EVIDENCE_REQUIRED":
        flag = parse_yes_no_partial(text)
        if flag is None:
            return _result(STATUS_NEEDS_INPUT, "No evidence status recorded.")
        if flag == "YES":
            return _result(STATUS_COMPLIANT, "Evidence exists and was inspected.")
        if flag == "PARTIAL":
            return _result(STATUS_PARTIAL_GAP, "Evidence is incomplete or informal.")
        return _result(STATUS_CRITICAL_GAP, "Requirement is not implemented; no evidence exists.")

    raise ValueError(f"Unknown validation rule: {rule!r}")


def evaluate_assessment(assessment: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Evaluate a stored ``entity_requirement_assessments`` row."""
    rule = assessment.get("validation_rule") or ""
    return evaluate_rule(
        rule,
        answer_value=assessment.get("answer_value"),
        answer_numeric=assessment.get("answer_numeric"),
        justification=assessment.get("justification"),
        context=context,
    )


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------

def compute_inherited_protection(
    process_answers: Sequence[Dict[str, Any]],
    levels: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compute the maximum-protection inheritance for a shared system.

    ``process_answers`` is one dict per dependent process with optional
    ``confidentiality``, ``integrity``, ``availability``, ``rto`` and
    ``mtpd`` keys. CIA takes the spread maximum (BSI Maximumprinzip);
    RTO takes the minimum and MTPD the minimum, because the most demanding
    process sets the recovery deadline.
    """
    fields = list(levels or ("confidentiality", "integrity", "availability"))
    result: Dict[str, Any] = {
        "confidentiality": None,
        "integrity": None,
        "availability": None,
        "rto": None,
        "mtpd": None,
        "process_count": len(process_answers),
    }

    for field_name in fields:
        best_level: Optional[str] = None
        for answer in process_answers:
            level = parse_cia(answer.get(field_name))
            if level and (best_level is None or CIA_LEVELS[level] > CIA_LEVELS[best_level]):
                best_level = level
        result[field_name] = best_level

    rtos = [number for number in (parse_number(a.get("rto")) for a in process_answers) if number is not None]
    mtpds = [number for number in (parse_number(a.get("mtpd")) for a in process_answers) if number is not None]
    result["rto"] = min(rtos) if rtos else None
    result["mtpd"] = min(mtpds) if mtpds else None
    return result


# ---------------------------------------------------------------------------
# Full catalog verification
# ---------------------------------------------------------------------------

def _load_workbook_catalog() -> List[Dict[str, Any]]:
    from app.seed.standards_and_survey_catalog import load_requirement_catalog

    return load_requirement_catalog(os.path.join(settings.DATA_DIR, "standards"))


def _load_workbook_survey() -> List[Dict[str, Any]]:
    from app.seed.standards_and_survey_catalog import load_survey_catalog

    return load_survey_catalog(os.path.join(settings.DATA_DIR, "surveys", "Master_Discovery_Survey.xlsx"))


def _load_db_rows(table: str, columns: str, order_by: str) -> List[Dict[str, Any]]:
    from app.database import get_db

    with get_db() as conn:
        rows = conn.execute(f"SELECT {columns} FROM {table} ORDER BY {order_by}").fetchall()
    return [dict(row) for row in rows]


def verify_catalog() -> Dict[str, Any]:
    """Prove that workbooks, survey and SQLite are one catalog on ``req_id``."""
    from app.services.standards_service import ensure_catalog_ready

    ensure_catalog_ready()

    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    workbook_requirements = _load_workbook_catalog()
    workbook_survey = _load_workbook_survey()
    db_requirements = _load_db_rows(
        "standards_requirements",
        "req_id, standard_code, clause_ref, title, target_scope, phase_category, validation_rule",
        "req_id",
    )
    db_questions = _load_db_rows("survey_questions", "question_id, req_id, sequence_order", "sequence_order")

    expected_ids = [f"R{index:03d}" for index in range(1, 134)]

    wb_ids = [row["req_id"] for row in workbook_requirements]
    check(
        "workbooks_contiguous",
        wb_ids == expected_ids,
        f"{len(wb_ids)} requirements, expected 133 contiguous ids",
    )

    db_ids = [row["req_id"] for row in db_requirements]
    check("db_matches_workbooks", db_ids == wb_ids, f"{len(db_ids)} DB rows vs {len(wb_ids)} workbook rows")

    survey_req_ids = [row["req_id"] for row in workbook_survey]
    check(
        "survey_bijection",
        len(survey_req_ids) == len(set(survey_req_ids)) and set(survey_req_ids) == set(wb_ids),
        f"{len(set(survey_req_ids))} unique survey req_ids against {len(set(wb_ids))} unique requirement ids",
    )
    check(
        "survey_sequence",
        [row["sequence_order"] for row in workbook_survey] == list(range(1, len(workbook_survey) + 1)),
        "Sequence order is 1..N without gaps",
    )
    check(
        "db_questions_match_survey",
        [row["req_id"] for row in db_questions] == survey_req_ids,
        f"{len(db_questions)} DB questions vs {len(survey_req_ids)} workbook questions",
    )

    counts: Dict[str, int] = {}
    for row in workbook_requirements:
        counts[row["standard_code"]] = counts.get(row["standard_code"], 0) + 1
    check(
        "standard_allocation",
        counts == {"BSI_200_2": 15, "BSI_200_4": 10, "ISO_27001": 15, "ISO_27002": 93},
        f"allocation {counts}",
    )

    iso_27002 = [row for row in workbook_requirements if row["standard_code"] == "ISO_27002"]
    from app.seed.iso_27002_catalog import ISO_27002_CONTROLS

    expected_controls = [f"ISO 27002:2022 {control['control_id']}" for control in ISO_27002_CONTROLS]
    check(
        "iso_27002_all_93_in_order",
        [row["clause_ref"] for row in iso_27002] == expected_controls,
        f"{len(iso_27002)} ISO 27002 rows in Annex A order",
    )

    # End-to-end rule evaluation on canonical cases.
    rule_cases = [
        ("RTO_LT_MTPD", {"answer_numeric": 4, "context": {"mtpd": 8}}, STATUS_COMPLIANT),
        ("RTO_LT_MTPD", {"answer_numeric": 8, "context": {"mtpd": 8}}, STATUS_CRITICAL_GAP),
        ("RTO_LT_MTPD", {"answer_numeric": 12, "context": {"mtpd": 8}}, STATUS_CRITICAL_GAP),
        ("RTO_LT_MTPD", {"answer_value": "kritisch", "answer_numeric": None, "context": {}}, STATUS_NEEDS_INPUT),
        ("ENUM_CIA", {"answer_value": "High", "justification": ""}, STATUS_PARTIAL_GAP),
        ("ENUM_CIA", {"answer_value": "High", "justification": "Legal duty on tax records."}, STATUS_COMPLIANT),
        ("ENUM_CIA", {"answer_value": "kritisch"}, STATUS_NEEDS_INPUT),
        ("CIA_INHERITANCE_MAX", {"answer_value": "Normal", "answer_numeric": "High"}, STATUS_CRITICAL_GAP),
        ("CIA_INHERITANCE_MAX", {"answer_value": "Very High", "answer_numeric": "High"}, STATUS_COMPLIANT),
        ("MUST_LINK_PROCESS", {"context": {"links": 2}}, STATUS_COMPLIANT),
        ("MUST_LINK_PROCESS", {"context": {"links": 0}}, STATUS_CRITICAL_GAP),
        ("ROLE_ASSIGNED", {"context": {"owner_role": "Bürgermeisteramt"}}, STATUS_COMPLIANT),
        ("ROLE_ASSIGNED", {"context": {}}, STATUS_CRITICAL_GAP),
        ("POSITIVE_NUMBER", {"answer_numeric": 0}, STATUS_CRITICAL_GAP),
        ("POSITIVE_NUMBER", {"answer_numeric": 24}, STATUS_COMPLIANT),
        ("NON_EMPTY", {"answer_value": "Process register kept by the office"}, STATUS_COMPLIANT),
        ("NON_EMPTY", {"answer_value": "   "}, STATUS_NEEDS_INPUT),
        ("EVIDENCE_REQUIRED", {"answer_value": "YES"}, STATUS_COMPLIANT),
        ("EVIDENCE_REQUIRED", {"answer_value": "PARTIAL"}, STATUS_PARTIAL_GAP),
        ("EVIDENCE_REQUIRED", {"answer_value": "NO"}, STATUS_CRITICAL_GAP),
        ("EVIDENCE_REQUIRED", {"answer_value": ""}, STATUS_NEEDS_INPUT),
    ]
    rule_failures: List[str] = []
    for rule, inputs, expected in rule_cases:
        result = evaluate_rule(
            rule,
            answer_value=inputs.get("answer_value"),
            answer_numeric=inputs.get("answer_numeric"),
            justification=inputs.get("justification"),
            context=inputs.get("context"),
        )
        if result["status"] != expected:
            rule_failures.append(f"{rule} with {inputs} -> {result['status']} (expected {expected})")
    check("rule_engine_canonical_cases", not rule_failures, "; ".join(rule_failures) or f"{len(rule_cases)} cases passed")

    inheritance = compute_inherited_protection(
        [
            {"confidentiality": "Normal", "integrity": "High", "availability": "High", "rto": 12, "mtpd": 24},
            {"confidentiality": "Very High", "integrity": "Normal", "availability": "Normal", "rto": 4, "mtpd": 12},
        ]
    )
    check(
        "inheritance_maximum",
        inheritance["confidentiality"] == "Very High"
        and inheritance["integrity"] == "High"
        and inheritance["availability"] == "High"
        and inheritance["rto"] == 4
        and inheritance["mtpd"] == 12,
        f"inherited {inheritance}",
    )

    # All declared rules must be known to the engine and used by the catalog.
    from app.seed.standards_and_survey_catalog import VALIDATION_RULES

    used_rules = {row["validation_rule"] for row in workbook_requirements}
    check(
        "rules_declared",
        used_rules <= set(VALIDATION_RULES),
        f"used rules {sorted(used_rules)}",
    )

    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "requirement_count": len(workbook_requirements),
        "question_count": len(workbook_survey),
    }


__all__ = [
    "ALL_STATUSES",
    "STATUS_COMPLIANT",
    "STATUS_COMPENSATING",
    "STATUS_CRITICAL_GAP",
    "STATUS_NEEDS_INPUT",
    "STATUS_NOT_APPLICABLE",
    "STATUS_PARTIAL_GAP",
    "compute_inherited_protection",
    "evaluate_assessment",
    "evaluate_rule",
    "parse_cia",
    "parse_number",
    "parse_yes_no_partial",
    "verify_catalog",
]
