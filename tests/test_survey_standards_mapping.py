"""Increment 1 acceptance tests: catalog, survey, SQLite mapping, verifier."""

import os

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db, init_db
from app.main import app
from app.seed.iso_27002_catalog import ISO_27002_CONTROLS
from app.seed.standards_and_survey_catalog import (
    SURVEY_COLUMNS,
    WORKBOOK_SPECS,
    build_requirement_catalog,
    build_survey_catalog,
    load_requirement_catalog,
    load_survey_catalog,
    standard_workbook_paths,
)
from app.services import standards_service
from app.services.mapping_verifier import (
    STATUS_COMPLIANT,
    STATUS_CRITICAL_GAP,
    STATUS_NEEDS_INPUT,
    STATUS_PARTIAL_GAP,
    compute_inherited_protection,
    evaluate_rule,
    verify_catalog,
)

client = TestClient(app)

EXPECTED_IDS = [f"R{index:03d}" for index in range(1, 134)]


@pytest.fixture(autouse=True)
def ready_catalog():
    init_db()
    standards_service.reset_cache()
    standards_service.ensure_catalog_ready()
    yield


# ---------------------------------------------------------------------------
# Artefacts on disk
# ---------------------------------------------------------------------------

def test_all_five_workbooks_exist_and_parse():
    paths = standard_workbook_paths(settings.DATA_DIR)
    assert set(paths) == {"bsi_200_2", "bsi_200_4", "iso_27001", "iso_27002", "survey"}
    for token, path in paths.items():
        assert os.path.exists(path), f"{token} workbook missing at {path}"
        workbook = openpyxl.load_workbook(path, read_only=True)
        try:
            assert workbook.sheetnames, f"{token} workbook has no sheets"
        finally:
            workbook.close()


def test_reference_workbooks_cover_full_catalog():
    catalog = load_requirement_catalog(os.path.join(settings.DATA_DIR, "standards"))
    assert [row["req_id"] for row in catalog] == EXPECTED_IDS


def test_survey_workbook_cover_full_catalog():
    survey = load_survey_catalog(
        os.path.join(settings.DATA_DIR, "surveys", "Master_Discovery_Survey.xlsx")
    )
    assert [row["req_id"] for row in survey] == EXPECTED_IDS
    assert [row["sequence_order"] for row in survey] == list(range(1, 134))


def test_survey_workbook_header_matches_declared_columns():
    path = os.path.join(settings.DATA_DIR, "surveys", "Master_Discovery_Survey.xlsx")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        headers = [cell.value for cell in next(workbook["Survey_Questions"].iter_rows(min_row=1, max_row=1))]
    finally:
        workbook.close()
    assert headers == [name for name, _width in SURVEY_COLUMNS]


def test_excel_round_trip_equals_python_catalog():
    python_catalog = {req["req_id"]: req for req in build_requirement_catalog()}
    excel_catalog = {
        row["req_id"]: row
        for row in load_requirement_catalog(os.path.join(settings.DATA_DIR, "standards"))
    }
    assert set(python_catalog) == set(excel_catalog)
    for req_id, source in python_catalog.items():
        stored = excel_catalog[req_id]
        for field in ("standard_code", "clause_ref", "title", "target_scope", "phase_category", "validation_rule"):
            assert stored[field] == source[field], f"{req_id}.{field} round-trip mismatch"
        assert stored["allowed_options"] == list(source["allowed_options"])


def test_iso_27002_block_is_all_93_controls_in_order():
    catalog = {row["req_id"]: row for row in load_requirement_catalog(os.path.join(settings.DATA_DIR, "standards"))}
    for offset, control in enumerate(ISO_27002_CONTROLS):
        req_id = f"R{41 + offset:03d}"
        assert catalog[req_id]["standard_code"] == "ISO_27002"
        assert catalog[req_id]["clause_ref"] == f"ISO 27002:2022 {control['control_id']}"
        assert catalog[req_id]["title"] == control["title"]


def test_standard_range_allocation():
    catalog = load_requirement_catalog(os.path.join(settings.DATA_DIR, "standards"))
    by_standard = {}
    for row in catalog:
        by_standard.setdefault(row["standard_code"], []).append(row["req_id"])
    assert by_standard["BSI_200_2"] == [f"R{i:03d}" for i in range(1, 16)]
    assert by_standard["BSI_200_4"] == [f"R{i:03d}" for i in range(16, 26)]
    assert by_standard["ISO_27001"] == [f"R{i:03d}" for i in range(26, 41)]
    assert by_standard["ISO_27002"] == [f"R{i:03d}" for i in range(41, 134)]


def test_workbook_specs_are_unique():
    assert len({spec["filename"] for spec in WORKBOOK_SPECS}) == len(WORKBOOK_SPECS)
    assert len({spec["standard_code"] for spec in WORKBOOK_SPECS}) == len(WORKBOOK_SPECS)


# ---------------------------------------------------------------------------
# SQLite mapping
# ---------------------------------------------------------------------------

def test_sqlite_requirement_count_and_key_order():
    with get_db() as conn:
        rows = conn.execute("SELECT req_id FROM standards_requirements ORDER BY req_id").fetchall()
        questions = conn.execute("SELECT req_id FROM survey_questions ORDER BY sequence_order").fetchall()
    assert [row["req_id"] for row in rows] == EXPECTED_IDS
    assert [row["req_id"] for row in questions] == EXPECTED_IDS


def test_sqlite_enforces_requirement_foreign_key():
    with pytest.raises(Exception):
        with get_db() as conn:
            conn.execute(
                "INSERT INTO entity_requirement_assessments "
                "(id, project_id, entity_id, req_id, status) VALUES (?, ?, ?, ?, ?)",
                ("bad-fk", "no-project", "no-entity", "R999", "NEEDS_USER_INPUT"),
            )


def test_survey_question_req_id_is_unique():
    with pytest.raises(Exception):
        with get_db() as conn:
            conn.execute(
                "INSERT INTO survey_questions "
                "(question_id, req_id, sequence_order, phase_category, target_scope, question_text, "
                "validation_rule, standard_code, clause_ref, plain_english_requirement) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Q_DUP", "R001", 999, "1_ARCHITECTURE", "ORGANIZATION", "dup", "NON_EMPTY", "BSI_200_2", "x", "y"),
            )


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

def test_rto_must_be_strictly_below_mtpd():
    below = evaluate_rule("RTO_LT_MTPD", answer_numeric=4, context={"mtpd": 8})
    equal = evaluate_rule("RTO_LT_MTPD", answer_numeric=8, context={"mtpd": 8})
    above = evaluate_rule("RTO_LT_MTPD", answer_numeric=12, context={"mtpd": 8})
    assert below["status"] == STATUS_COMPLIANT
    assert equal["status"] == STATUS_CRITICAL_GAP
    assert above["status"] == STATUS_CRITICAL_GAP


def test_missing_rto_with_verbatim_criticality_needs_user_input():
    result = evaluate_rule("RTO_LT_MTPD", answer_value="kritisch", answer_numeric=None)
    assert result["status"] == STATUS_NEEDS_INPUT


def test_cia_requires_justification():
    assert evaluate_rule("ENUM_CIA", answer_value="High")["status"] == STATUS_PARTIAL_GAP
    assert evaluate_rule("ENUM_CIA", answer_value="High", justification="Tax records")["status"] == STATUS_COMPLIANT
    assert evaluate_rule("ENUM_CIA", answer_value="kritisch")["status"] == STATUS_NEEDS_INPUT


def test_evidence_rule_tiers():
    assert evaluate_rule("EVIDENCE_REQUIRED", answer_value="YES")["status"] == STATUS_COMPLIANT
    assert evaluate_rule("EVIDENCE_REQUIRED", answer_value="PARTIAL")["status"] == STATUS_PARTIAL_GAP
    assert evaluate_rule("EVIDENCE_REQUIRED", answer_value="NO")["status"] == STATUS_CRITICAL_GAP
    assert evaluate_rule("EVIDENCE_REQUIRED", answer_value="")["status"] == STATUS_NEEDS_INPUT


def test_unknown_rule_is_rejected():
    with pytest.raises(ValueError):
        evaluate_rule("MADE_UP_RULE", answer_value="x")


def test_inheritance_takes_maximum_cia_and_strictest_recovery():
    inherited = compute_inherited_protection(
        [
            {"confidentiality": "Normal", "integrity": "High", "availability": "High", "rto": 12, "mtpd": 24},
            {"confidentiality": "Very High", "integrity": "Normal", "availability": "Normal", "rto": 4, "mtpd": 12},
        ]
    )
    assert inherited["confidentiality"] == "Very High"
    assert inherited["integrity"] == "High"
    assert inherited["availability"] == "High"
    assert inherited["rto"] == 4
    assert inherited["mtpd"] == 12


def test_inheritance_with_no_dependents_is_empty():
    inherited = compute_inherited_protection([])
    assert inherited["confidentiality"] is None
    assert inherited["rto"] is None


def test_verifier_reports_ok():
    result = verify_catalog()
    assert result["ok"] is True, [c for c in result["checks"] if not c["ok"]]
    assert result["requirement_count"] == 133
    assert result["question_count"] == 133


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

def test_requirements_endpoint_returns_full_catalog():
    response = client.get("/api/v1/standards/requirements")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 133
    assert [item["req_id"] for item in body["requirements"]] == EXPECTED_IDS


def test_requirements_endpoint_filters():
    response = client.get("/api/v1/standards/requirements", params={"standard_code": "BSI_200_4"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 10
    assert {item["standard_code"] for item in body["requirements"]} == {"BSI_200_4"}


def test_single_requirement_endpoint():
    response = client.get("/api/v1/standards/requirements/R017")
    assert response.status_code == 200
    assert response.json()["validation_rule"] == "POSITIVE_NUMBER"

    missing = client.get("/api/v1/standards/requirements/R999")
    assert missing.status_code == 404


def test_survey_endpoint_orders_by_phase():
    response = client.get("/api/v1/standards/survey")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 133
    orders = [item["sequence_order"] for item in body["questions"]]
    assert orders == list(range(1, 134))

    architecture = client.get("/api/v1/standards/survey", params={"phase_category": "1_ARCHITECTURE"})
    assert architecture.status_code == 200
    assert all(
        item["phase_category"] == "1_ARCHITECTURE" for item in architecture.json()["questions"]
    )


def test_verify_mapping_endpoint():
    response = client.get("/api/v1/standards/verify-mapping")
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.parametrize("token", ["bsi_200_2", "bsi_200_4", "iso_27001", "iso_27002", "survey"])
def test_download_endpoint_returns_xlsx(token):
    response = client.get(f"/api/v1/standards/download/{token}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    # XLSX is a zip container: the first two bytes are 'PK'.
    assert response.content[:2] == b"PK"
    assert len(response.content) > 1000


def test_download_endpoint_rejects_unknown_token():
    response = client.get("/api/v1/standards/download/passwd")
    assert response.status_code == 404


def test_builders_are_deterministic():
    first = build_survey_catalog(build_requirement_catalog())
    second = build_survey_catalog(build_requirement_catalog())
    assert [q["question_id"] for q in first] == [q["question_id"] for q in second]
    assert [q["question_text"] for q in first] == [q["question_text"] for q in second]
