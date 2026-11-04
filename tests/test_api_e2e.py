import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_database():
    init_db()

def test_health_check():
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

def test_load_benchmark_and_run_analysis_flow():
    # 1. Fetch benchmarks
    res_bm = client.get("/api/v1/datasets/benchmarks")
    assert res_bm.status_code == 200
    benchmarks = res_bm.json()
    assert len(benchmarks) >= 3

    # 2. Load IT Access benchmark into a project
    bm_id = benchmarks[0]["id"]
    res_load = client.post("/api/v1/datasets/benchmarks/load", data={"benchmark_id": bm_id})
    assert res_load.status_code == 200
    proj_id = res_load.json()["project_id"]

    # 3. Verify project details
    res_proj = client.get(f"/api/v1/projects/{proj_id}")
    assert res_proj.status_code == 200
    pdata = res_proj.json()
    assert len(pdata["documents"]) == 2
    assert pdata["bpmn_model"] is not None

    # 4. Run Gap Analysis
    res_gaps = client.post("/api/v1/analysis/gaps", json={"project_id": proj_id})
    assert res_gaps.status_code == 200
    gap_result = res_gaps.json()
    assert "findings" in gap_result
    assert len(gap_result["findings"]) > 0

    # 5. Export annotated Excel report
    res_excel = client.get(f"/api/v1/export/excel/{proj_id}")
    assert res_excel.status_code == 200
    assert "spreadsheetml" in res_excel.headers["content-type"]

    # 6. Export BPMN
    res_bpmn = client.get(f"/api/v1/export/bpmn/{proj_id}")
    assert res_bpmn.status_code == 200
    assert "xml" in res_bpmn.headers["content-type"]

    # 7. GDPR 1-Click Purge
    res_purge = client.delete(f"/api/v1/gdpr/purge/{proj_id}")
    assert res_purge.status_code == 200

    # Verify project is expunged
    res_check = client.get(f"/api/v1/projects/{proj_id}")
    assert res_check.status_code == 404

def test_corpus_manifest_and_detail_endpoints():
    # 1. Fetch corpus manifest
    res = client.get("/api/v1/corpus")
    assert res.status_code == 200
    manifest = res.json()
    assert len(manifest) == 12
    first_id = manifest[0]["id"]

    # 2. Fetch single model detail with 4 text styles
    res_detail = client.get(f"/api/v1/corpus/{first_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["id"] == first_id
    assert "bpmn_xml" in detail
    assert "graph" in detail
    assert "text_styles" in detail
    assert "formal" in detail["text_styles"]
    assert "shorthand" in detail["text_styles"]

def test_corpus_parse_and_evaluate_endpoints():
    # 1. Get model 06 (parallel onboarding)
    res_detail = client.get("/api/v1/corpus/06_parallel_employee_onboarding")
    assert res_detail.status_code == 200
    data = res_detail.json()

    # 2. Parse BPMN XML via parse-bpmn endpoint
    res_parse = client.post("/api/v1/corpus/parse-bpmn", json={"bpmn_xml": data["bpmn_xml"]})
    assert res_parse.status_code == 200
    parsed = res_parse.json()
    assert parsed["node_count"] >= 7
    assert len(parsed["text_styles"]["parallel_and_loop"]) > 10

    # 3. Evaluate exact match
    res_eval = client.post(
        "/api/v1/corpus/evaluate",
        json={
            "ground_truth_model_id": "06_parallel_employee_onboarding",
            "predicted_graph": parsed["graph"]
        }
    )
    assert res_eval.status_code == 200
    eval_res = res_eval.json()
    assert eval_res["total_reconstruction_score"] == 1.0
    assert eval_res["gateway_accuracy"] == 1.0

