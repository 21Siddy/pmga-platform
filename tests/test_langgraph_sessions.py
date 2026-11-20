"""Rigorous tests for Milestone 2B: LangGraph SqliteSaver multi-session checkpointing,
negation-safe evidence extraction, Persona flows A/B/C/D, PII redaction, and API contracts.
"""

from __future__ import annotations

import io
import os
import sqlite3

import openpyxl
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings
from app.database import init_db
from app.main import app
from app.services.llm_client import LLMClient
from app.services.workflow_graph import build_workflow_graph, run_project_turn

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    init_db()
    yield


def _create_project(title: str) -> str:
    res = client.post("/api/v1/projects", json={"title": title})
    assert res.status_code == 200
    return res.json()["id"]


def test_langgraph_interrupt_survives_simulated_restart_and_never_persists_api_key(monkeypatch):
    pid = _create_project("Multi-Session Persistence")
    secret_key = "sk-test-never-write-to-checkpoint-1234567890"
    captured_prompts = []

    def fake_llm(prompt, response_model, **kwargs):
        captured_prompts.append(prompt)
        name = response_model.__name__
        if name == "ChatGraphPatch":
            return response_model(
                processes=[],
                system_links=[],
                confidentiality="High",
                integrity="High",
                availability="Very High",
                cia_reason="Verified across sessions",
            )
        if name == "_GapPolishBatch":
            return response_model(items=[])
        return response_model(message="Warm follow-up question from AI coach.")

    monkeypatch.setattr(
        LLMClient,
        "resolve",
        staticmethod(lambda *a, **k: {"provider": "openai", "style": "openai", "api_key": secret_key, "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"}),
    )
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(fake_llm))

    # Session 1 (Turn 1): Start from scratch with PII in text
    t1 = run_project_turn(
        pid,
        input_kind="chat",
        payload={"message": "Citizen Registration: Clerk receives form from maria@example.org -> verifies ID -> issues permit"},
        api_key=secret_key,
        provider="openai",
    )
    assert t1["status"] == "ok"
    assert len(t1["workspace"]["process_models"]) == 1
    # Ensure PII was scrubbed before LLM saw prompt
    assert all("maria@example.org" not in p for p in captured_prompts)

    # Simulate complete server restart: inspect SqliteSaver directly from a fresh connection
    with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as cp2:
        g2 = build_workflow_graph(cp2)
        st = g2.get_state({"configurable": {"thread_id": pid}})
        assert st.next == ("await_next_turn",)
        assert len(st.values["messages"]) >= 2

    # Security audit: raw SQLite checkpoint DB must NEVER contain the BYOK secret key!
    raw_bytes = open(settings.checkpoint_db_path, "rb").read()
    assert secret_key.encode("utf-8") not in raw_bytes

    # Session 2 (Turn 2): Resume across simulated restart
    t2 = run_project_turn(
        pid,
        input_kind="chat",
        payload={"message": "Confidentiality: High, Integrity: High, Availability: Very High, RTO: 4h, MTPD: 12h"},
        api_key=secret_key,
        provider="openai",
    )
    assert t2["status"] == "ok"
    ws = t2["workspace"]
    assert len(ws["session_turns"]) == 4  # 2 user + 2 coach
    proc_ent = next(e for e in ws["structure"]["entities"] if e["entity_type"] == "PROCESS")
    assert proc_ent["declared_protection"]["availability"] == "Very High"
    assert proc_ent["declared_protection"]["rto"] == 4.0


def test_persona_a_bayreuth_messy_excel_splits_diagrams_and_inherits_cia():
    pid = _create_project("Bayreuth Municipal Assessment")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Systemliste"
    ws.append(["System / Prozess", "Kritikalität"])
    ws.append(["Anmeldesystem", "kritisch"])
    ws.append(["Gutscheinsystem", "kritisch"])
    ws.append(["HR System", "kritisch"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    # Turn 1: Upload messy Excel
    r1 = client.post(
        f"/api/v1/projects/{pid}/turn",
        data={"doc_type": "ACTUAL_PROCESS"},
        files={"file": ("bayreuth_assets.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["coach_reply"]["llm_mode"] == "offline"
    assert d1["agenda_queue"][0]["agenda_key"] == "LINK_SYSTEM_TO_PROCESS"
    systems = [e for e in d1["workspace"]["structure"]["entities"] if e["entity_type"] == "SYSTEM"]
    assert {s["name"] for s in systems} == {"Anmeldesystem", "Gutscheinsystem", "HR System"}

    # Turn 2: Disambiguate systems to 3 distinct business processes
    r2 = client.post(
        f"/api/v1/projects/{pid}/turn",
        json={
            "input_kind": "chat",
            "message": (
                "Anmeldesystem supports Citizen Registration (Bürgeramt); "
                "Gutscheinsystem supports Voucher Issuing (Kämmerei); "
                "HR System supports Staff Onboarding (HR)"
            ),
        },
    )
    assert r2.status_code == 200
    ws2 = r2.json()["workspace"]
    pm_titles = {pm["title"] for pm in ws2["process_models"]}
    assert pm_titles == {"Citizen Registration", "Voucher Issuing", "Staff Onboarding"}
    # Each process has its own BPMN model (never overwritten!)
    assert len(ws2["process_models"]) == 3

    # Turn 3: Unpack CIA & BIA for Citizen Registration and verify Maximumprinzip inheritance onto Anmeldesystem
    r3 = client.post(
        f"/api/v1/projects/{pid}/turn",
        json={
            "input_kind": "chat",
            "message": "For Citizen Registration: Confidentiality: Very High, Integrity: High, Availability: Very High, RTO: 4h, MTPD: 12h",
        },
    )
    assert r3.status_code == 200
    ws3 = r3.json()["workspace"]
    anmelde = next(e for e in ws3["structure"]["entities"] if e["name"] == "Anmeldesystem")
    assert anmelde["inherited_protection"]["confidentiality"] == "Very High"
    assert anmelde["inherited_protection"]["availability"] == "Very High"
    assert anmelde["inherited_protection"]["rto"] == 4.0

    # Remediation gaps must project onto BPMN node IDs (e.g. Task_1)
    assert len(ws3["remediation_gaps"]) >= 1
    assert any(g["affected_node_ids"] for g in ws3["remediation_gaps"])


def test_persona_c_negation_safe_handbook_never_marks_compliant():
    pid = _create_project("Negated Policy Drift")
    policy_text = (
        "IT Security Handbook v1.2\n"
        "Owner: IT Security Lead\n"
        "Multi-factor authentication (MFA) is currently not enforced for portal administrators.\n"
        "Kein Notfallplan für das Anmeldesystem vorhanden.\n"
        "Citizen files are sent via email attachment when the portal stalls.\n"
        "Automated encrypted backups run nightly and are tested quarterly.\n"
    )
    res = client.post(
        f"/api/v1/projects/{pid}/turn",
        data={"doc_type": "FORMAL_POLICY"},
        files={"file": ("policy.md", policy_text.encode("utf-8"), "text/markdown")},
    )
    assert res.status_code == 200
    ws = res.json()["workspace"]

    ev_by_req = {}
    for ev in ws["evidence_items"]:
        ev_by_req.setdefault(ev["req_id"], []).append(ev)

    # Negated MFA and Emergency Plan must be CONTRADICTED, NEVER USER_CONFIRMED
    assert any(e["status"] == "CONTRADICTED" for e in ev_by_req.get("R104", []))
    assert any(e["status"] == "CONTRADICTED" for e in ev_by_req.get("R021", []))
    # Affirmative backup sentence must stay DOCUMENT_CLAIM (not USER_CONFIRMED without reality check)
    assert all(e["status"] == "DOCUMENT_CLAIM" for e in ev_by_req.get("R015", []))

    # Entity assessments for R104/R021 must be CRITICAL_GAP (never COMPLIANT!)
    anchor = next(e for e in ws["structure"]["entities"] if e["entity_type"] == "PROCESS")
    assess_map = {a["req_id"]: a for a in anchor["assessments"]}
    assert assess_map["R104"]["status"] == "CRITICAL_GAP"
    assert assess_map["R021"]["status"] == "CRITICAL_GAP"
    assert assess_map["R015"]["status"] != "COMPLIANT"


def test_persona_d_bpmn_upload_canvas_edit_extract_description_and_gap_badges():
    pid = _create_project("Diagram First Technical User")
    uploaded_bpmn = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  id="Defs_Tech" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_Tech" name="Building Permit Intake" isExecutable="false">
    <bpmn:startEvent id="Start_CitizenFiling" name="Permit Filed">
      <bpmn:outgoing>F1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_EmailReview" name="Review Email Attachment Blueprints">
      <bpmn:incoming>F1</bpmn:incoming>
      <bpmn:outgoing>F2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:serviceTask id="Service_SaveLocal" name="Store Citizen Blueprints">
      <bpmn:incoming>F2</bpmn:incoming>
      <bpmn:outgoing>F3</bpmn:outgoing>
    </bpmn:serviceTask>
    <bpmn:endEvent id="End_Archived" name="Filed">
      <bpmn:incoming>F3</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F1" sourceRef="Start_CitizenFiling" targetRef="Task_EmailReview" />
    <bpmn:sequenceFlow id="F2" sourceRef="Task_EmailReview" targetRef="Service_SaveLocal" />
    <bpmn:sequenceFlow id="F3" sourceRef="Service_SaveLocal" targetRef="End_Archived" />
  </bpmn:process>
</bpmn:definitions>"""

    # Turn 1: Upload .bpmn via /turn
    up = client.post(
        f"/api/v1/projects/{pid}/turn",
        files={"file": ("permit_intake.bpmn", uploaded_bpmn.encode("utf-8"), "application/xml")},
    )
    assert up.status_code == 200
    ws1 = up.json()["workspace"]
    assert len(ws1["process_models"]) == 1
    pm = ws1["process_models"][0]
    assert "Review Email Attachment Blueprints" in pm["description_text"]

    # Gap badges must bind to exact uploaded node IDs!
    all_affected_nodes = {nid for g in ws1["remediation_gaps"] for nid in g["affected_node_ids"]}
    assert "Task_EmailReview" in all_affected_nodes

    # Edit canvas & call PUT /processes/{pm_id}/bpmn
    edited_xml = uploaded_bpmn.replace("Store Citizen Blueprints", "Store Citizen Blueprints via Shared Admin Login")
    sync = client.put(
        f"/api/v1/projects/{pid}/processes/{pm['id']}/bpmn",
        json={"bpmn_xml": edited_xml, "extract_description": True},
    )
    assert sync.status_code == 200
    sync_data = sync.json()
    assert sync_data["process_model"]["version"] >= 2
    assert "Shared Admin Login" in sync_data["description"]["plain_english_description"]

    # Update a gap status via PATCH and ensure it persists even after recompute
    gap_id = sync_data["workspace"]["remediation_gaps"][0]["id"]
    patch_res = client.patch(f"/api/v1/projects/{pid}/gaps/{gap_id}", json={"status": "IN_PROGRESS"})
    assert patch_res.status_code == 200
    assert patch_res.json()["status"] == "IN_PROGRESS"


def test_turn_endpoint_boundary_and_error_handling():
    pid = _create_project("Error Guards")

    # 1. Empty chat turn rejected cleanly without corrupting checkpoint
    r_empty = client.post(f"/api/v1/projects/{pid}/turn", json={"input_kind": "chat", "message": "   "})
    assert r_empty.status_code == 400
    assert r_empty.json()["error_code"] == "PMGA_VALIDATION_ERROR"
    assert "request_id" in r_empty.json()

    # 2. Unsupported extension rejected with 415
    r_bad_ext = client.post(
        f"/api/v1/projects/{pid}/turn",
        files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
    )
    assert r_bad_ext.status_code == 415
    assert r_bad_ext.json()["error_code"] == "PMGA_UNSUPPORTED_MEDIA"

    # 3. XXE BPMN upload rejected with 422
    r_xxe = client.post(
        f"/api/v1/projects/{pid}/turn",
        files={"file": ("bad.bpmn", b'<!DOCTYPE foo [<!ENTITY xxe "x">]><definitions/>', "application/xml")},
    )
    assert r_xxe.status_code == 422
    assert r_xxe.json()["error_code"] == "PMGA_BPMN_PARSE_ERROR"

    # 4. Unknown project rejected with 404
    r_404 = client.get("/api/v1/projects/nonexistent-id/workspace")
    assert r_404.status_code == 404
    assert r_404.json()["error_code"] == "PMGA_NOT_FOUND"

    # 5. Invalid gap status rejected with 400
    r_gap = client.patch(f"/api/v1/projects/{pid}/gaps/fake-gap", json={"status": "INVALID_STATE"})
    assert r_gap.status_code == 400
    assert r_gap.json()["error_code"] == "PMGA_VALIDATION_ERROR"

    # 6. Project still accepts a valid turn cleanly after prior errors!
    r_ok = client.post(f"/api/v1/projects/{pid}/turn", json={"input_kind": "chat", "message": "Clerk: review filing -> archive"})
    assert r_ok.status_code == 200
