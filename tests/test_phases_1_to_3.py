import io
import openpyxl
from fastapi.testclient import TestClient

from app.main import app
from app.services.bpmn_engine import BPMNEngine, BPMNEdge, BPMNNode, BPMNProcessGraph
from app.services.bpmn_verifier import BPMNVerifier
from app.services.ir_engine import analyze_plotholes
from app.services.llm_client import LLMClient

client = TestClient(app)


def test_phase1_byok_providers_and_offline_generation():
    # Verify 6 providers including all 5 requested BYOK providers
    res = client.get("/api/v1/llm/providers")
    assert res.status_code == 200
    ids = [p["id"] for p in res.json()["providers"]]
    for expected in ("gemini", "openai", "anthropic", "mistral", "azure", "kiconnect"):
        assert expected in ids

    # Test connection without key fails gracefully in plain English (never 500)
    test_res = client.post("/api/v1/llm/test", json={"provider": "openai", "model_name": "gpt-4.1-mini"})
    assert test_res.status_code == 200
    body = test_res.json()
    assert body["ok"] is False
    assert body["error_kind"] == "config"
    assert "not stored" in body["message"].lower()

    # Generating BPMN without an API key must use offline fallback (no ProviderError NameError!)
    proj = client.post("/api/v1/projects", json={"title": "Offline BYOK Check"}).json()
    gen = client.post(
        "/api/v1/bpmn/generate",
        json={
            "project_id": proj["id"],
            "process_text": "Bürgeramt: Antrag prüfen -> Genehmigung erteilen -> Bescheid versenden",
            "provider": "openai",
        },
    )
    assert gen.status_code == 200
    assert "<bpmn:process" in gen.json()["bpmn_xml"]


def test_phase1_pii_redacted_before_llm_gap_analysis(monkeypatch):
    captured = {}

    def fake_generate(prompt, response_model, **kwargs):
        captured["prompt"] = prompt
        from app.services.gap_analyzer import GapAnalysisResult
        return GapAnalysisResult(executive_summary="Checked", compliance_score_percent=90, findings=[])

    monkeypatch.setattr(LLMClient, "resolve", staticmethod(lambda *a, **kw: {"provider": "openai", "style": "openai", "api_key": "k", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"}))
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(fake_generate))

    proj = client.post("/api/v1/projects", json={"title": "PII Redaction Test"}).json()
    secret_email = "buergermeister@example.org"
    secret_iban = "DE89 3704 0044 0532 0130 00"
    upload = client.post(
        "/api/v1/documents/upload",
        data={"project_id": proj["id"], "doc_type": "ACTUAL_PROCESS"},
        files={"file": ("notes.txt", f"Antrag von {secret_email} mit IBAN {secret_iban} prüfen.".encode("utf-8"), "text/plain")},
    )
    assert upload.status_code == 200
    assert upload.json()["pii_report"]["has_pii"] is True

    gap_res = client.post(
        "/api/v1/analysis/gaps",
        headers={"X-LLM-API-Key": "dummy-key"},
        json={"project_id": proj["id"], "provider": "openai"},
    )
    assert gap_res.status_code == 200
    assert secret_email not in captured["prompt"]
    assert secret_iban not in captured["prompt"]
    assert "[EMAIL_REDACTED]" in captured["prompt"]
    assert "[IBAN_REDACTED]" in captured["prompt"]


def test_phase2_entities_inheritance_2d_layout_and_two_way_sync():
    proj = client.post("/api/v1/projects", json={"title": "Structure & Inheritance"}).json()
    pid = proj["id"]

    # Create parent process, child activity, system, data asset, role
    p1 = client.post(f"/api/v1/projects/{pid}/entities", json={
        "entity_type": "PROCESS", "name": "Gutscheinausstellung", "owner_role": "Kämmerei",
        "confidentiality": "High", "integrity": "Very High", "availability": "High", "rto": 4, "mtpd": 12,
    }).json()
    proc_id = next(e["id"] for e in p1["entities"] if e["name"] == "Gutscheinausstellung")

    p2 = client.post(f"/api/v1/projects/{pid}/entities", json={
        "entity_type": "SUB_PROCESS", "name": "Freigabe", "parent_id": proc_id, "owner_role": "Amtsleitung"
    }).json()
    sub_id = next(e["id"] for e in p2["entities"] if e["name"] == "Freigabe")
    assert any(c["id"] == sub_id for e in p2["tree"] if e["id"] == proc_id for c in e["children"])

    s1 = client.post(f"/api/v1/projects/{pid}/entities", json={
        "entity_type": "SYSTEM", "name": "Gutscheinsystem"
    }).json()
    sys_id = next(e["id"] for e in s1["entities"] if e["name"] == "Gutscheinsystem")

    dep = client.post(f"/api/v1/projects/{pid}/dependencies", json={
        "from_entity_id": sys_id, "to_entity_id": proc_id, "dependency_type": "SUPPORTS"
    }).json()
    sys_node = next(e for e in dep["structure"]["entities"] if e["id"] == sys_id)
    assert sys_node["inherited_protection"]["confidentiality"] == "High"
    assert sys_node["inherited_protection"]["integrity"] == "Very High"
    assert sys_node["inherited_protection"]["rto"] == 4

    # 2D layered layout: parallel tasks share X coordinate and have distinct Y coordinates
    graph = BPMNProcessGraph(
        process_title="Parallel Flow",
        lanes=["Staff"],
        nodes=[
            BPMNNode(id="Start_1", name="Start", type="startEvent", lane="Staff"),
            BPMNNode(id="Gate_1", name="Split", type="parallelGateway", lane="Staff"),
            BPMNNode(id="Task_A", name="Branch A", type="userTask", lane="Staff"),
            BPMNNode(id="Task_B", name="Branch B", type="userTask", lane="Staff"),
            BPMNNode(id="End_1", name="End", type="endEvent", lane="Staff"),
        ],
        edges=[
            BPMNEdge(id="F1", sourceRef="Start_1", targetRef="Gate_1"),
            BPMNEdge(id="F2", sourceRef="Gate_1", targetRef="Task_A"),
            BPMNEdge(id="F3", sourceRef="Gate_1", targetRef="Task_B"),
            BPMNEdge(id="F4", sourceRef="Task_A", targetRef="End_1"),
            BPMNEdge(id="F5", sourceRef="Task_B", targetRef="End_1"),
        ],
    )
    xml = BPMNEngine.compile_graph_to_bpmn_xml(graph)
    assert 'id="Task_A_di"' in xml and 'id="Task_B_di"' in xml
    # Two-way XML sync on PUT /api/v1/bpmn/update
    upd = client.put("/api/v1/bpmn/update", json={"project_id": pid, "bpmn_xml": xml})
    assert upd.status_code == 200
    assert upd.json()["telemetry"]["is_valid"] is True


def test_phase3_smart_excel_dedup_handbook_citations_ast_plotholes_and_oscal():
    proj = client.post("/api/v1/projects", json={"title": "Municipality E2E"}).json()
    pid = proj["id"]

    # Create Excel workbook where Column A is ID, Column B is System/Process, Column C is Criticality
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Prozesse"
    ws.append(["ID", "System / Prozess", "Schutzbedarf", "Verantwortlich"])
    ws.append(["101", "Gutscheinsystem", "kritisch", "Kämmerei"])
    ws.append(["102", "Antrag prüfen und freigeben", "hoch", "Bürgeramt"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    up1 = client.post(
        "/api/v1/documents/upload",
        data={"project_id": pid, "doc_type": "ACTUAL_PROCESS"},
        files={"file": ("client_sheet.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up1.status_code == 200
    assert up1.json()["survey_rows_created"] == 2

    # Re-upload same Excel must NOT duplicate rows
    up2 = client.post(
        "/api/v1/documents/upload",
        data={"project_id": pid, "doc_type": "ACTUAL_PROCESS"},
        files={"file": ("client_sheet.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up2.status_code == 200
    survey = client.get(f"/api/v1/projects/{pid}/survey").json()
    assert survey["total_rows"] == 2
    entries = {r["q01_raw_entry"] for r in survey["rows"]}
    assert entries == {"Gutscheinsystem", "Antrag prüfen und freigeben"}

    # Upload formal handbook with explicit MFA/backup/RTO/MTPD facts -> proven citations
    handbook = (
        "Verantwortlich: Informationssicherheitsbeauftragter\n"
        "All admin logins enforce MFA and RBAC access control.\n"
        "Daily encrypted backup and retention procedures are verified monthly.\n"
        "RTO: 4h, MTPD: 24h for citizen voucher processing.\n"
    )
    hb_res = client.post(
        "/api/v1/documents/upload",
        data={"project_id": pid, "doc_type": "FORMAL_POLICY"},
        files={"file": ("handbook.md", handbook.encode("utf-8"), "text/markdown")},
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["evidence_extracted"]["assessments_proven"] >= 3

    # German umlaut tokenization in BPMNVerifier
    telemetry = BPMNVerifier.verify_graph(
        graph_nodes=[
            {"id": "S1", "name": "Start", "type": "startEvent", "lane": "Bürgeramt"},
            {"id": "T1", "name": "Antrag prüfen und freigeben", "type": "userTask", "lane": "Bürgeramt"},
            {"id": "E1", "name": "Ende", "type": "endEvent", "lane": "Bürgeramt"},
        ],
        graph_edges=[
            {"id": "F1", "sourceRef": "S1", "targetRef": "T1"},
            {"id": "F2", "sourceRef": "T1", "targetRef": "E1"},
        ],
        source_text="Antrag prüfen und freigeben",
    )
    assert telemetry.faithfulness_score == 1.0

    # AST Plothole detection catches missing else on "Antrag prüfen..." and unlinked "Gutscheinsystem"
    ph = client.get(f"/api/v1/projects/{pid}/plotholes").json()
    kinds = {p["kind"] for p in ph["plotholes"]}
    assert "MISSING_ELSE_BRANCH" in kinds
    assert "UNLINKED_SYSTEM" in kinds

    # NIST OSCAL JSON export works
    oscal = client.get(f"/api/v1/export/oscal/{pid}")
    assert oscal.status_code == 200
    assert "assessment-results" in oscal.json()
