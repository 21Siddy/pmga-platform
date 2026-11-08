"""Diagnostics endpoint and the repaired survey answer contract."""

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app
from app.services.survey_service import SurveyService

client = TestClient(app)

EXPECTED_STEP_IDS = {
    "create_project",
    "upload_documents",
    "extract_structure",
    "review_diagram",
    "answer_interview",
    "set_model_key",
    "standards_check",
}


@pytest.fixture(autouse=True)
def prepared_db():
    init_db()
    yield


def _make_project() -> str:
    return client.post(
        "/api/v1/projects", json={"title": "Diagnostics test", "description": ""}
    ).json()["id"]


def test_diagnostics_lists_the_full_step_set():
    project_id = _make_project()
    body = client.get(f"/api/v1/diagnostics/{project_id}").json()

    assert {s["id"] for s in body["steps"]} == EXPECTED_STEP_IDS
    assert all(set(s) == {"id", "title", "done", "detail", "hint", "action"} for s in body["steps"])
    assert body["steps"][0]["id"] == "create_project" and body["steps"][0]["done"] is True


def test_diagnostics_404_for_unknown_project():
    assert client.get("/api/v1/diagnostics/no-such-project").status_code == 404


def test_diagnostics_marks_documents_and_diagram_done_after_benchmark_load():
    project_id = _make_project()
    loaded = client.post(
        "/api/v1/datasets/benchmarks/load",
        data={"benchmark_id": "citizen-building-permit", "project_id": project_id},
    )
    assert loaded.status_code == 200

    body = client.get(f"/api/v1/diagnostics/{project_id}").json()
    by_id = {s["id"]: s["done"] for s in body["steps"]}

    assert body["documents"]["actual"] >= 1
    assert body["documents"]["policy"] >= 1
    assert by_id["upload_documents"] is True
    assert by_id["extract_structure"] is True
    assert body["diagram"]["versions"] >= 1
    assert by_id["review_diagram"] is True


def test_next_question_carries_its_row_identity():
    project_id = _make_project()
    SurveyService.ingest_rows_into_survey(project_id, [{"raw_entry": "Urlaubsantrag erfassen"}])

    state = SurveyService.get_survey_state(project_id)
    assert state["total_rows"] == 1

    row = state["rows"][0]
    question = row["next_question"]
    assert question["row_id"] == row["id"]
    assert question["entry"] == "Urlaubsantrag erfassen"
    assert question["question_code"]
    assert question["question_text"]


def test_answer_flow_advances_progress_and_updates_completion():
    project_id = _make_project()
    SurveyService.ingest_rows_into_survey(project_id, [{"raw_entry": "Urlaubsantrag erfassen"}])

    state = SurveyService.get_survey_state(project_id)
    row = state["rows"][0]
    question = row["next_question"]
    start_filled = state["filled_slots"]

    result = client.post(
        f"/api/v1/projects/{project_id}/survey/{row['id']}/answer",
        json={"question_code": question["question_code"], "answer": "Personalabteilung", "accept_as_concept": False},
    )
    assert result.status_code == 200
    assert result.json()["survey"]["filled_slots"] == start_filled + 1

    body = client.get(f"/api/v1/diagnostics/{project_id}").json()
    assert body["survey"]["filled_slots"] == start_filled + 1


def test_invalid_question_code_is_rejected_with_a_clear_message():
    project_id = _make_project()
    SurveyService.ingest_rows_into_survey(project_id, [{"raw_entry": "Rechnung pruefen"}])

    state = SurveyService.get_survey_state(project_id)
    row = state["rows"][0]

    response = client.post(
        f"/api/v1/projects/{project_id}/survey/{row['id']}/answer",
        json={"question_code": "q99_made_up", "answer": "x"},
    )
    assert response.status_code == 400
    assert "q99_made_up" in response.json()["detail"]
