"""Rigorous tests for Milestone 4: Brandable Deliverables (Jinja2 HTML, real WeasyPrint PDF, XLSX, BPMN) & XSS/Validation guards."""

from __future__ import annotations

import base64
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import init_db
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    init_db()
    yield


def _seed_rich_project() -> str:
    pid = client.post("/api/v1/projects", json={"title": "Stadt Bayreuth Portal"}).json()["id"]
    client.post(
        f"/api/v1/projects/{pid}/turn",
        json={
            "input_kind": "chat",
            "message": "Bürgeramt: Citizen applies via email attachment -> Clerk reviews in Anmeldesystem -> Approve & send certificate",
        },
    )
    client.post(
        f"/api/v1/projects/{pid}/turn",
        json={
            "input_kind": "chat",
            "message": "Confidentiality: Very High, Integrity: High, Availability: High, RTO: 4h, MTPD: 24h",
        },
    )
    return pid


def test_branding_validation_xss_protection_and_deliverable_html_and_pdf():
    pid = _seed_rich_project()

    # Valid PNG 1x1 pixel data URI
    png_1x1 = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    # Apply branding with attempted XSS in org_name and footer_text -> must be escaped in HTML!
    b_res = client.post(
        f"/api/v1/projects/{pid}/brand",
        json={
            "org_name": "Stadt Bayreuth <script>alert(1)</script>",
            "primary_color": "#0F4C5C",
            "accent_color": "#D97706",
            "font_family": "Georgia",
            "footer_text": "Confidential <b>Draft</b>",
            "logo_data_uri": png_1x1,
        },
    )
    assert b_res.status_code == 200
    assert b_res.json()["primary_color"] == "#0F4C5C"

    # Reject invalid hex color / CSS injection attempt
    bad_color = client.post(
        f"/api/v1/projects/{pid}/brand",
        json={"primary_color": "#111111; } body { display: none; }"},
    )
    assert bad_color.status_code == 400
    assert bad_color.json()["error_code"] == "PMGA_VALIDATION_ERROR"

    # Reject javascript: URI or malicious SVG with <script>
    bad_logo = client.post(
        f"/api/v1/projects/{pid}/brand",
        json={"logo_data_uri": "javascript:alert(1)"},
    )
    assert bad_logo.status_code == 400

    evil_svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').decode()
    bad_svg = client.post(
        f"/api/v1/projects/{pid}/brand",
        json={"logo_data_uri": f"data:image/svg+xml;base64,{evil_svg}"},
    )
    assert bad_svg.status_code == 400

    # Render all 4 doc types as HTML and real WeasyPrint PDF
    for doc_type in ("remediation_plan", "prozesssteckbrief", "strukturanalyse", "complete_pack"):
        r_html = client.get(f"/api/v1/projects/{pid}/deliverables/{doc_type}?format=html")
        assert r_html.status_code == 200
        html = r_html.text
        assert "#0F4C5C" in html
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "data:image/png;base64," in html

        r_pdf = client.get(f"/api/v1/projects/{pid}/deliverables/{doc_type}?format=pdf")
        assert r_pdf.status_code == 200
        assert r_pdf.headers["content-type"] == "application/pdf"
        assert r_pdf.content.startswith(b"%PDF-")
        assert len(r_pdf.content) > 5000

    # Excel deliverable export reflects v2 remediation gaps
    r_xlsx = client.get(f"/api/v1/projects/{pid}/deliverables/remediation_plan?format=xlsx")
    assert r_xlsx.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r_xlsx.content))
    ws = wb["ISO 27002 Gap Assessment"]
    assert ws.max_row >= 2

    # BPMN deliverable export returns valid XML
    r_bpmn = client.get(f"/api/v1/projects/{pid}/deliverables/prozesssteckbrief?format=bpmn")
    assert r_bpmn.status_code == 200
    assert "<bpmn:definitions" in r_bpmn.text


def test_empty_project_deliverables_do_not_crash():
    pid = client.post("/api/v1/projects", json={"title": "Empty Project"}).json()["id"]
    for doc_type in ("remediation_plan", "prozesssteckbrief", "strukturanalyse", "complete_pack"):
        res = client.get(f"/api/v1/projects/{pid}/deliverables/{doc_type}?format=html")
        assert res.status_code == 200
        assert "Empty Project" in res.text

    # Invalid doc_type and format return crisp 400 validation errors
    assert client.get(f"/api/v1/projects/{pid}/deliverables/bogus?format=html").status_code == 400
    assert client.get(f"/api/v1/projects/{pid}/deliverables/remediation_plan?format=doc").status_code == 400
