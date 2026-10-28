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
