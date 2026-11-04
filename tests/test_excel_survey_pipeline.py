import os
import io
import openpyxl
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db, get_db
from app.services.survey_service import SurveyService
from app.services.document_service import DocumentService

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()

def create_sample_kritis_excel_bytes():
    """Builds an in-memory workbook mimicking Frank's multi-sheet organization Excel."""
    wb = openpyxl.Workbook()
    # Sheet 1: Geschäftsprozesse (with applications labeled 'kritisch')
    ws1 = wb.active
    ws1.title = "Geschäftsprozesse"
    ws1.append(["Prozess / System", "Kritikalität", "Bereich"])
    ws1.append(["HR System", "kritisch", "Personal"])
    ws1.append(["Gutscheinsystem", "kritisch", "Marketing"])
    ws1.append(["Rechnungssystem", "kritisch", "Finanzen"])
    ws1.append(["Urlaubsantrag erfassen und prüfen", "normal", "Personal"])

    # Sheet 2: IT-Systeme
    ws2 = wb.create_sheet(title="IT-Infrastruktur")
    ws2.append(["Server Name", "IP-Bereich", "Standort"])
    ws2.append(["SRV-DB-01", "10.0.1.5", "RZ Frankfurt"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()

def test_multisheet_excel_ingest_and_survey():
    # 1. Create a project
    proj_resp = client.post("/api/v1/projects", json={"title": "Frank Kritis Review", "description": "Municipal test"})
    assert proj_resp.status_code == 200
    proj_id = proj_resp.json()["id"]

    # 2. Upload the multi-sheet Excel
    excel_bytes = create_sample_kritis_excel_bytes()
    upload_resp = client.post(
        "/api/v1/documents/upload",
        data={"project_id": proj_id, "doc_type": "ACTUAL_PROCESS"},
        files={"file": ("organisationshandbuch.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    )
    assert upload_resp.status_code == 200
    data = upload_resp.json()
    assert data["survey_rows_created"] >= 4

    # 3. Query survey state via GET /api/v1/projects/{project_id}/survey
    survey_resp = client.get(f"/api/v1/projects/{proj_id}/survey")
    assert survey_resp.status_code == 200
    s_state = survey_resp.json()
    assert s_state["total_rows"] >= 4
    
    # Verify Gutscheinsystem is classified as APPLICATION with raw criticality 'kritisch'
    gutschein_row = next(r for r in s_state["rows"] if "Gutscheinsystem" in r["q01_raw_entry"])
    assert gutschein_row["q02_entry_kind"] == "APPLICATION"
    assert gutschein_row["q14_raw_criticality"] == "kritisch"

    # Verify Urlaubsantrag is classified as PROCESS
    urlaub_row = next(r for r in s_state["rows"] if "Urlaubsantrag" in r["q01_raw_entry"])
    assert urlaub_row["q02_entry_kind"] == "PROCESS"

    # 4. Check that next question for Gutscheinsystem asks which business process uses it
    next_q = gutschein_row["next_question"]
    assert next_q is not None
    assert next_q["question_code"] == "q03_process_name"
    assert "Gutscheinsystem" in next_q["question_text"]

def test_answer_survey_and_prelead_option_reuse():
    # 1. Create project and manually ingest 2 rows
    proj_resp = client.post("/api/v1/projects", json={"title": "Prelead Concept Test"})
    proj_id = proj_resp.json()["id"]

    row_ids = SurveyService.ingest_rows_into_survey(proj_id, [
        {"raw_entry": "Gutscheinsystem", "raw_criticality": "kritisch"},
        {"raw_entry": "Rechnungssystem", "raw_criticality": "kritisch"}
    ])
    row1_id, row2_id = row_ids[0], row_ids[1]

    # 2. Answer q03_process_name on Row 1 (Gutscheinsystem)
    ans1_resp = client.post(
        f"/api/v1/projects/{proj_id}/survey/{row1_id}/answer",
        json={"question_code": "q03_process_name", "answer": "Gutscheinausstellung und Einlösung", "accept_as_concept": True}
    )
    assert ans1_resp.status_code == 200
    r1_state = ans1_resp.json()
    assert r1_state["status"] == "success"
    assert "bpmn_xml" in r1_state

    # 3. Answer q11_storage_location on Row 1 with 'DATEV Cloud Archive'
    ans_storage = client.post(
        f"/api/v1/projects/{proj_id}/survey/{row1_id}/answer",
        json={"question_code": "q11_storage_location", "answer": "DATEV Cloud Archive", "accept_as_concept": True}
    )
    assert ans_storage.status_code == 200

    # 4. Query survey state and verify Row 2 (Rechnungssystem) sees 'DATEV Cloud Archive' under dynamic system concepts
    s_state = client.get(f"/api/v1/projects/{proj_id}/survey").json()
    accepted = s_state["accepted_concepts"]
    assert "DATEV Cloud Archive" in accepted.get("SYSTEM", [])

    # 5. Answer q13_is_repeatable = YES on Row 1 and assert BPMN exclusiveGateway loop
    ans_rep = client.post(
        f"/api/v1/projects/{proj_id}/survey/{row1_id}/answer",
        json={"question_code": "q13_is_repeatable", "answer": "YES"}
    )
    assert ans_rep.status_code == 200
    bpmn_xml = ans_rep.json()["bpmn_xml"]
    assert "Gate_Repeat_1" in bpmn_xml
    assert "Flow_Loop_1" in bpmn_xml

def test_multisheet_excel_export_contains_survey_sheet():
    proj_resp = client.post("/api/v1/projects", json={"title": "Export Test"})
    proj_id = proj_resp.json()["id"]

    SurveyService.ingest_rows_into_survey(proj_id, [
        {"raw_entry": "Gutscheinsystem", "raw_criticality": "kritisch"}
    ])

    export_resp = client.get(f"/api/v1/export/excel/{proj_id}")
    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    # Read the returned bytes into openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(export_resp.content))
    assert "ISO 27002 Gap Assessment" in wb.sheetnames
    assert "Prozesserhebung & Struktur" in wb.sheetnames

    ws_survey = wb["Prozesserhebung & Struktur"]
    assert ws_survey.cell(row=2, column=2).value == "Gutscheinsystem"
    assert ws_survey.cell(row=2, column=3).value == "APPLICATION"
