import pytest
from app.services.bpmn_parser import BPMNParser
from app.services.bpmn_engine import BPMNEngine
from app.seed.corpus_builder import CORPUS_MODELS


def test_parser_loads_all_corpus_models():
    """Verify BPMNParser successfully parses all 12 curated ground-truth models."""
    assert len(CORPUS_MODELS) == 12

    for model_meta in CORPUS_MODELS:
        graph = BPMNParser.parse_xml_to_graph(
            model_meta["bpmn_xml"],
            fallback_title=model_meta["title"]
        )
        assert graph.process_title == model_meta["title"]
        assert len(graph.nodes) >= 4, f"Model {model_meta['id']} has too few nodes"
        assert len(graph.edges) >= 3, f"Model {model_meta['id']} has too few edges"
        assert len(graph.lanes) >= 1, f"Model {model_meta['id']} has no lanes"


def test_parser_extracts_all_archetypes():
    """Verify specific BPMN node types are extracted accurately for each archetype."""
    # 01 Linear Multi-Lane
    g_linear = BPMNParser.parse_xml_to_graph(CORPUS_MODELS[0]["bpmn_xml"])
    assert "Citizen" in g_linear.lanes
    assert "Front Desk Clerk" in g_linear.lanes
    assert "Municipal Archivist" in g_linear.lanes
    assert any(n.lane == "Citizen" for n in g_linear.nodes)

    # 03 XOR Decision Branching
    g_xor = BPMNParser.parse_xml_to_graph(CORPUS_MODELS[2]["bpmn_xml"])
    xor_nodes = [n for n in g_xor.nodes if n.type == "exclusiveGateway"]
    assert len(xor_nodes) >= 1
    branch_labels = [e.name for e in g_xor.edges if e.name]
    assert "Yes" in branch_labels and "No" in branch_labels

    # 06 Parallel Concurrency AND
    g_and = BPMNParser.parse_xml_to_graph(CORPUS_MODELS[5]["bpmn_xml"])
    and_nodes = [n for n in g_and.nodes if n.type == "parallelGateway"]
    assert len(and_nodes) >= 2  # Split and Join

    # 09 Cyclic Rework Loop
    g_loop = BPMNParser.parse_xml_to_graph(CORPUS_MODELS[8]["bpmn_xml"])
    # Check that Task_CheckDocs has 2 incoming edges (initial start flow + rework loop)
    incoming_to_check = [e for e in g_loop.edges if e.targetRef == "Task_CheckDocs"]
    assert len(incoming_to_check) == 2
    sources = {e.sourceRef for e in incoming_to_check}
    assert "Start_Filing" in sources
    assert "Task_RequestCorrection" in sources


def test_lossless_roundtrip_conversion():
    """
    Lossless Round-Trip Verification:
    XML -> parse_xml_to_graph (G1) -> compile_graph_to_bpmn_xml -> parse_xml_to_graph (G2)
    G1 and G2 must be isomorphic: 100% identical node IDs, types, lanes, and edge flows.
    """
    for model_meta in CORPUS_MODELS:
        model_id = model_meta["id"]
        g1 = BPMNParser.parse_xml_to_graph(model_meta["bpmn_xml"], fallback_title=model_meta["title"])
        
        # Compile G1 to valid BPMN 2.0 XML with layout
        compiled_xml = BPMNEngine.compile_graph_to_bpmn_xml(g1)
        assert "<bpmn:definitions" in compiled_xml
        assert "<bpmndi:BPMNDiagram" in compiled_xml

        # Parse compiled XML back into G2
        g2 = BPMNParser.parse_xml_to_graph(compiled_xml, fallback_title=model_meta["title"])

        # Check nodes
        g1_nodes = {n.id: n.type for n in g1.nodes}
        g2_nodes = {n.id: n.type for n in g2.nodes}
        assert g1_nodes == g2_nodes, f"Node set/type mismatch in {model_id}"

        # Check edge pairs
        g1_edges = {(e.sourceRef, e.targetRef) for e in g1.edges}
        g2_edges = {(e.sourceRef, e.targetRef) for e in g2.edges}
        assert g1_edges == g2_edges, f"Edge connection mismatch in {model_id}"

        # Check lanes
        assert set(g1.lanes) == set(g2.lanes), f"Lane set mismatch in {model_id}"
