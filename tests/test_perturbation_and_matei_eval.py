import pytest
from app.services.bpmn_parser import BPMNParser
from app.services.perturbation_engine import PerturbationEngine
from app.services.matei_evaluator import MateiEvaluator
from app.seed.corpus_builder import CORPUS_MODELS
from app.services.bpmn_engine import BPMNProcessGraph, BPMNNode, BPMNEdge


def test_perturbation_engine_generates_all_four_styles():
    """Verify that PerturbationEngine produces 4 non-empty, distinct styles for any corpus model."""
    for model_meta in CORPUS_MODELS[:4]:
        graph = BPMNParser.parse_xml_to_graph(model_meta["bpmn_xml"], fallback_title=model_meta["title"])
        styles = PerturbationEngine.generate_all_styles(graph)

        assert "formal" in styles
        assert "shorthand" in styles
        assert "out_of_order" in styles
        assert "parallel_and_loop" in styles

        assert len(styles["formal"]) > 20
        assert len(styles["shorthand"]) > 10
        assert len(styles["out_of_order"]) > 20
        assert len(styles["parallel_and_loop"]) > 20

        # Shorthand should contain arrows
        assert "->" in styles["shorthand"]


def test_input_style_classifier_and_playbook_matching():
    """Verify zero-cost input style classifier maps distinct linguistic structures accurately."""
    # 1. Shorthand
    shorthand_text = "clerk: inspect passport -> it: issue certificate -> supervisor: sign deed"
    style_res = PerturbationEngine.detect_input_style(shorthand_text)
    assert style_res["detected_style"] == "shorthand"
    assert style_res["confidence_score"] >= 0.7
    assert "playbook_rule" in style_res

    # 2. Concurrency / Parallelism
    parallel_text = "HR initiates onboarding. Meanwhile, IT simultaneously prepares accounts and facilities issues the badge."
    parallel_res = PerturbationEngine.detect_input_style(parallel_text)
    assert parallel_res["detected_style"] == "parallel_and_loops"
    assert any("simultaneously" in c or "meanwhile" in c for c in parallel_res["matched_cues"])

    # 3. Out of Order
    ooo_text = "Prior to closing the case with final certificate issuance, the clerk must verify the land deed. This occurs only after intake begins."
    ooo_res = PerturbationEngine.detect_input_style(ooo_text)
    assert ooo_res["detected_style"] == "out_of_order"
    assert any("prior to" in c or "only after" in c for c in ooo_res["matched_cues"])


def test_matei_evaluator_exact_match_score():
    """Verify that comparing a ground-truth graph against itself yields 1.0 across all dimensions."""
    g_star = BPMNParser.parse_xml_to_graph(CORPUS_MODELS[0]["bpmn_xml"])
    metrics = MateiEvaluator.evaluate(ground_truth=g_star, predicted=g_star)

    assert metrics.structural_similarity == 1.0
    assert metrics.type_distribution_alignment == 1.0
    assert metrics.semantic_similarity == 1.0
    assert metrics.total_reconstruction_score == 1.0
    assert metrics.gateway_accuracy == 1.0
    assert len(metrics.failure_mode_diagnostics) == 0


def test_matei_evaluator_penalizes_gateway_confusion():
    """Verify that confusing parallelGateway (AND) with exclusiveGateway (XOR) degrades scores with diagnostics."""
    # Model 06 is Parallel Employee Onboarding
    g_star = BPMNParser.parse_xml_to_graph(CORPUS_MODELS[5]["bpmn_xml"])

    # Corrupt predicted graph by mutating parallelGateway to exclusiveGateway
    mutated_nodes = []
    for n in g_star.nodes:
        if n.type == "parallelGateway":
            mutated_nodes.append(n.model_copy(update={"type": "exclusiveGateway"}))
        else:
            mutated_nodes.append(n.model_copy())

    g_pred = BPMNProcessGraph(
        process_title=g_star.process_title,
        lanes=g_star.lanes,
        nodes=mutated_nodes,
        edges=g_star.edges
    )

    metrics = MateiEvaluator.evaluate(ground_truth=g_star, predicted=g_pred)

    assert metrics.type_distribution_alignment < 1.0
    assert metrics.gateway_accuracy < 1.0
    assert metrics.total_reconstruction_score < 1.0
    assert any("parallelGateway" in diag or "exclusiveGateway" in diag for diag in metrics.failure_mode_diagnostics)
