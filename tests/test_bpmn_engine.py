import pytest
import xml.etree.ElementTree as ET
from app.services.bpmn_engine import BPMNEngine, BPMNProcessGraph, BPMNNode, BPMNEdge

def test_compile_graph_to_valid_bpmn_xml():
    nodes = [
        BPMNNode(id="Start_1", name="Applicant Submits", type="startEvent", lane="Citizen"),
        BPMNNode(id="Task_1", name="Review Application", type="userTask", lane="Clerk"),
        BPMNNode(id="End_1", name="Permit Issued", type="endEvent", lane="Clerk")
    ]
    edges = [
        BPMNEdge(id="Flow_1", sourceRef="Start_1", targetRef="Task_1"),
        BPMNEdge(id="Flow_2", sourceRef="Task_1", targetRef="End_1")
    ]
    graph = BPMNProcessGraph(
        process_title="Citizen Application Review",
        lanes=["Citizen", "Clerk"],
        nodes=nodes,
        edges=edges
    )

    xml_str = BPMNEngine.compile_graph_to_bpmn_xml(graph)
    assert "<?xml" in xml_str
    assert "<bpmn:definitions" in xml_str
    assert "<bpmndi:BPMNDiagram" in xml_str
    assert "Task_1_di" in xml_str

    # Parse XML to guarantee strict standard compliance
    root = ET.fromstring(xml_str)
    assert root.tag.endswith("definitions")
