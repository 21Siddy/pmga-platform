import pytest
from app.services.bpmn_verifier import BPMNVerifier

def test_extract_atomic_actions_from_messy_free_text():
    messy_notes = """
    portal times out constantly -> citizens complain on phone
    we tell them just email CAD blueprints to bauamt@stadt.local;
    we download to local desktop C:\\Downloads folder
    fire marshal in building B has no network -> burn to DVD or unencrypted USB stick
    stamped paper copy put in metal file cabinet
    """
    actions = BPMNVerifier.extract_atomic_actions(messy_notes)
    assert len(actions) >= 4
    assert any("email" in a.lower() for a in actions)
    assert any("usb" in a.lower() for a in actions)

def test_structural_soundness_catches_defects():
    # Graph with missing end event and orphan node
    nodes = [
        {"id": "Start_1", "name": "Start", "type": "startEvent"},
        {"id": "Task_1", "name": "Do step 1", "type": "userTask"},
        {"id": "Task_Orphan", "name": "Orphan step", "type": "userTask"}
    ]
    edges = [
        {"id": "Flow_1", "sourceRef": "Start_1", "targetRef": "Task_1"}
    ]

    telemetry = BPMNVerifier.verify_graph(nodes, edges, "Sample process")
    assert telemetry.is_valid is False
    assert telemetry.structural_soundness_score < 0.8
    assert any("endEvent" in d for d in telemetry.structural_defects)
    assert any("orphan" in d.lower() for d in telemetry.structural_defects)

def test_sound_graph_passes_verification():
    nodes = [
        {"id": "Start_1", "name": "Application Received", "type": "startEvent"},
        {"id": "Task_1", "name": "Download CAD Blueprints to local desktop", "type": "userTask"},
        {"id": "Task_2", "name": "Copy plans to USB stick for fire marshal", "type": "userTask"},
        {"id": "End_1", "name": "Permit Case Archived", "type": "endEvent"}
    ]
    edges = [
        {"id": "Flow_1", "sourceRef": "Start_1", "targetRef": "Task_1"},
        {"id": "Flow_2", "sourceRef": "Task_1", "targetRef": "Task_2"},
        {"id": "Flow_3", "sourceRef": "Task_2", "targetRef": "End_1"}
    ]
    source_text = "download blueprints -> copy to usb stick -> archive"
    telemetry = BPMNVerifier.verify_graph(nodes, edges, source_text)

    assert telemetry.structural_soundness_score == 1.0
    assert len(telemetry.structural_defects) == 0
    assert telemetry.is_valid is True
