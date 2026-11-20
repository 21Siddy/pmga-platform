"""Rigorous tests for Milestone 2A: Bidirectional FIM-BPMN 2.0 JSON IR & XML Engine."""

from __future__ import annotations

import pytest
from lxml import etree

from app.errors import BPMNParseError, ValidationError
from app.seed.corpus_builder import CORPUS_MODELS
from app.services.bpmn_ir_service import (
    BPMNCollapsedPool,
    BPMNDataObject,
    BPMNFlow,
    BPMNProcessIR,
    BPMNStep,
    apply_ir_patch,
    compile_ir_to_bpmn_xml,
    describe_bpmn_for_user,
    parse_bpmn_xml_to_ir,
    render_ir_to_svg,
)
from app.services.bpmn_verifier import BPMNVerifier
from app.services.llm_client import LLMClient
from app.services.matei_evaluator import MateiEvaluator


def test_roundtrip_all_12_corpus_models_losslessly():
    assert len(CORPUS_MODELS) == 12
    for model in CORPUS_MODELS:
        ir1 = parse_bpmn_xml_to_ir(model["bpmn_xml"], fallback_title=model["title"])
        assert len(ir1.steps) >= 4, f"{model['id']} parsed too few steps"
        compiled_xml = compile_ir_to_bpmn_xml(ir1)
        # Must parse with strict XML parser cleanly
        etree.fromstring(compiled_xml.encode("utf-8"))
        ir2 = parse_bpmn_xml_to_ir(compiled_xml, fallback_title=model["title"])

        assert [s.id for s in ir2.steps] == [s.id for s in ir1.steps]
        assert [s.type for s in ir2.steps] == [s.type for s in ir1.steps]
        assert len(ir2.flows) == len(ir1.flows)
        metrics = MateiEvaluator.evaluate(ir1.to_legacy_graph(), ir2.to_legacy_graph())
        assert metrics.total_reconstruction_score == 1.0, f"Lossy roundtrip for {model['id']}: {metrics}"


def test_fim_bpmn_full_roundtrip_pools_data_callactivity_and_gaps():
    ir = BPMNProcessIR(
        process_id="Proc_Voucher",
        process_title="Gutscheinausstellung & Einlösung",
        pool_name="Stadtwerke & Kämmerei",
        lanes=["Bürgeramt", "Kämmerei"],
        collapsed_pools=[
            BPMNCollapsedPool(
                id="Ext_Citizen",
                name="Bürger / Antragsteller",
                message_flows=[
                    {"id": "Msg_1", "name": "Antrag", "direction": "to_process", "step_id": "Start_1"},
                    {"id": "Msg_2", "name": "Gutschein", "direction": "from_process", "step_id": "End_1"},
                ],
            )
        ],
        data_objects=[
            BPMNDataObject(id="DataObj_Antrag", name="Antragsdaten", kind="dataObject", sensitivity="High", linked_step_ids=["Step_1"]),
            BPMNDataObject(id="DataStore_VoucherDB", name="Gutscheinsystem DB", kind="dataStore", sensitivity="Very High", linked_step_ids=["Step_2"]),
        ],
        steps=[
            BPMNStep(id="Start_1", name="Antrag eingegangen", type="startEvent", lane="Bürgeramt"),
            BPMNStep(
                id="Step_1",
                name="Antrag prüfen & erfassen",
                type="userTask",
                lane="Bürgeramt",
                data_in=["DataObj_Antrag"],
                gap_ids=["gap_r054"],
            ),
            BPMNStep(
                id="Gate_1",
                name="Vollständig?",
                type="exclusiveGateway",
                lane="Bürgeramt",
            ),
            BPMNStep(
                id="Step_2",
                name="Gutschein buchen",
                type="serviceTask",
                lane="Kämmerei",
                system_name="Gutscheinsystem",
                data_out=["DataStore_VoucherDB"],
                gap_ids=["gap_r101", "gap_r104"],
                risk_annotation="Shared admin without MFA",
            ),
            BPMNStep(
                id="Sub_1",
                name="Monatsabschluss prüfen",
                type="callActivity",
                lane="Kämmerei",
                sub_process_id="proc_sub_close",
            ),
            BPMNStep(id="End_1", name="Gutschein ausgestellt", type="endEvent", lane="Kämmerei"),
        ],
        flows=[
            BPMNFlow(id="F1", sourceRef="Start_1", targetRef="Step_1"),
            BPMNFlow(id="F2", sourceRef="Step_1", targetRef="Gate_1"),
            BPMNFlow(id="F3", sourceRef="Gate_1", targetRef="Step_2", name="Ja", is_default=True),
            BPMNFlow(id="F4", sourceRef="Gate_1", targetRef="Step_1", name="Rückfrage"),  # Cycle!
            BPMNFlow(id="F5", sourceRef="Step_2", targetRef="Sub_1"),
            BPMNFlow(id="F6", sourceRef="Sub_1", targetRef="End_1"),
        ],
    )

    xml = compile_ir_to_bpmn_xml(ir)
    # Check special characters escaped properly and cycle layout didn't hang
    assert "&amp;" in xml
    assert 'dataObjectReference id="DataObj_Antrag"' in xml
    assert 'dataStoreReference id="DataStore_VoucherDB"' in xml
    assert 'callActivity id="Sub_1"' in xml

    roundtripped = parse_bpmn_xml_to_ir(xml)
    assert roundtripped.pool_name == "Stadtwerke & Kämmerei"
    assert roundtripped.lanes == ["Bürgeramt", "Kämmerei"]
    assert len(roundtripped.collapsed_pools) == 1
    assert len(roundtripped.collapsed_pools[0].message_flows) == 2

    s1 = next(s for s in roundtripped.steps if s.id == "Step_1")
    s2 = next(s for s in roundtripped.steps if s.id == "Step_2")
    sub = next(s for s in roundtripped.steps if s.id == "Sub_1")
    assert s1.data_in == ["DataObj_Antrag"]
    assert s1.gap_ids == ["gap_r054"]
    assert s2.system_name == "Gutscheinsystem"
    assert s2.data_out == ["DataStore_VoucherDB"]
    assert s2.gap_ids == ["gap_r101", "gap_r104"]
    assert s2.risk_annotation == "Shared admin without MFA"
    assert sub.type == "callActivity"
    assert sub.sub_process_id == "proc_sub_close"

    d_store = next(d for d in roundtripped.data_objects if d.id == "DataStore_VoucherDB")
    assert d_store.kind == "dataStore"
    assert d_store.sensitivity == "Very High"

    default_flow = next(f for f in roundtripped.flows if f.id == "F3")
    assert default_flow.is_default is True

    # SVG generation includes gap badges and escapes XML cleanly
    svg = render_ir_to_svg(roundtripped)
    assert "<svg" in svg
    assert ">2</text>" in svg
    etree.fromstring(svg.encode("utf-8"))


def test_atomic_patching_preserves_untouched_nodes_and_avoids_deadlocks():
    base = BPMNProcessIR(
        process_title="Permit Intake",
        lanes=["Citizen Desk"],
        steps=[
            BPMNStep(id="Start_1", name="Filing Received", type="startEvent", lane="Citizen Desk"),
            BPMNStep(id="Task_Verify", name="Verify Documents", type="userTask", lane="Citizen Desk", gap_ids=["g1"]),
            BPMNStep(id="End_1", name="Permit Issued", type="endEvent", lane="Citizen Desk"),
        ],
        flows=[
            BPMNFlow(id="F1", sourceRef="Start_1", targetRef="Task_Verify"),
            BPMNFlow(id="F2", sourceRef="Task_Verify", targetRef="End_1"),
        ],
    )

    patched = apply_ir_patch(
        base,
        [
            {"op": "attach_system", "step_id": "Task_Verify", "system_name": "Anmeldesystem"},
            {"op": "attach_data", "step_id": "Task_Verify", "data_name": "Personalausweis", "direction": "in", "sensitivity": "High"},
            {"op": "add_exception_branch", "after_step_id": "Task_Verify", "exception_name": "Request Missing Docs", "condition_label": "Incomplete", "rejoin_step_id": "Task_Verify"},
            {"op": "add_step", "after_step_id": "Task_Verify", "keep_existing_edge": True, "step": {"id": "Task_Extra", "name": "Notify Citizen", "lane": "Citizen Desk"}},
        ],
    )

    # Original base must stay untouched (copy-on-write)
    assert base.steps[1].system_name is None
    assert len(base.steps) == 3

    # Patched preserves Task_Verify ID and existing gap_id
    tv = next(s for s in patched.steps if s.id == "Task_Verify")
    assert tv.system_name == "Anmeldesystem"
    assert tv.gap_ids == ["g1"]
    assert len(tv.data_in) == 1
    assert any(s.type == "exclusiveGateway" for s in patched.steps)
    assert any(s.name == "Request Missing Docs" for s in patched.steps)

    # Delete Task_Extra and verify graph remains sound via BPMNVerifier
    cleaned = apply_ir_patch(patched, [{"op": "delete_step", "step_id": "Task_Extra"}])
    legacy = cleaned.to_legacy_graph()
    telemetry = BPMNVerifier.verify_graph(
        graph_nodes=[n.model_dump() for n in legacy.nodes],
        graph_edges=[e.model_dump() for e in legacy.edges],
        source_text="Verify Documents Request Missing Docs",
    )
    assert telemetry.structural_soundness_score == 1.0


def test_describe_bpmn_for_user_detects_missing_metadata_and_redacts_pii(monkeypatch):
    # Minimal diagram with General Process lane, no data objects, review task without XOR, unlinked serviceTask
    ir = BPMNProcessIR(
        process_title="Raw Uploaded Diagram",
        lanes=["General Process"],
        steps=[
            BPMNStep(id="S1", name="Start for alice@stadt.de", type="startEvent", lane="General Process"),
            BPMNStep(id="T1", name="Approve Citizen Permit", type="userTask", lane="General Process"),
            BPMNStep(id="T2", name="Save Record to Registry", type="serviceTask", lane="General Process"),
            BPMNStep(id="E1", name="Done", type="endEvent", lane="General Process"),
        ],
        flows=[
            BPMNFlow(id="F1", sourceRef="S1", targetRef="T1"),
            BPMNFlow(id="F2", sourceRef="T1", targetRef="T2"),
            BPMNFlow(id="F3", sourceRef="T2", targetRef="E1"),
        ],
    )
    desc_offline = describe_bpmn_for_user(ir)
    missing_kinds = {m["kind"] for m in desc_offline["missing_metadata"]}
    assert "MISSING_ACTORS" in missing_kinds
    assert "MISSING_DATA_ASSETS" in missing_kinds
    assert "MISSING_EXCEPTION_BRANCH" in missing_kinds
    assert "UNLINKED_SERVICE_TASK" in missing_kinds
    assert desc_offline["llm_enhanced"] is False

    # Verify LLM polish path scrubs PII before calling LLMClient
    captured = {}

    def fake_llm(prompt, response_model, **kwargs):
        captured["prompt"] = prompt
        return response_model(plain_english_summary="Warm polished walkthrough.")

    monkeypatch.setattr(LLMClient, "resolve", staticmethod(lambda *a, **k: {"provider": "openai", "style": "openai", "api_key": "k", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"}))
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(fake_llm))

    desc_ai = describe_bpmn_for_user(ir, api_key="dummy", provider="openai")
    assert desc_ai["llm_enhanced"] is True
    assert "alice@stadt.de" not in captured["prompt"]
    assert "[EMAIL_REDACTED]" in captured["prompt"]


def test_parse_bpmn_rejects_xxe_empty_and_malformed_xml():
    xxe_payload = """<?xml version="1.0"?>
    <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">
      <bpmn:process id="P1"><bpmn:startEvent id="S1" name="&xxe;"/></bpmn:process>
    </bpmn:definitions>"""
    with pytest.raises(BPMNParseError, match="DOCTYPE"):
        parse_bpmn_xml_to_ir(xxe_payload)

    with pytest.raises(BPMNParseError):
        parse_bpmn_xml_to_ir("")

    with pytest.raises(BPMNParseError):
        parse_bpmn_xml_to_ir("<not-closed>")

    with pytest.raises(BPMNParseError, match="No <process>"):
        parse_bpmn_xml_to_ir('<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" />')


def test_apply_ir_patch_rejects_invalid_operations():
    base = BPMNProcessIR(steps=[BPMNStep(id="S1", name="Start", type="startEvent", lane="Staff")])
    with pytest.raises(ValidationError):
        apply_ir_patch(base, [{"op": "nonexistent_op"}])
    with pytest.raises(ValidationError):
        apply_ir_patch(base, [{"op": "update_step", "step_id": "Missing"}])
