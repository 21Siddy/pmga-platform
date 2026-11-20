"""Real Headless Chromium Playwright End-to-End Tests across all 4 Personas,
live /studio, /, /research, session reloads, clickable BPMN gap overlays,
negated policy contradiction detection, and live branded deliverables & PDFs.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import openpyxl
import pytest
from playwright.sync_api import sync_playwright

SAMPLE_UPLOAD_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
  id="Defs_Upload_E2E"
  targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Process_Legacy" name="Bauantrag Legacy Import" isExecutable="false">
    <bpmn:startEvent id="StartEvent_1" name="Application Arrives">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:serviceTask id="ServiceTask_LegacyDB" name="Export to Legacy DB">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:serviceTask>
    <bpmn:endEvent id="EndEvent_1" name="Filed">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="ServiceTask_LegacyDB"/>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="ServiceTask_LegacyDB" targetRef="EndEvent_1"/>
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_Legacy">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
        <dc:Bounds x="120" y="160" width="36" height="36"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="ServiceTask_LegacyDB_di" bpmnElement="ServiceTask_LegacyDB">
        <dc:Bounds x="230" y="138" width="150" height="80"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="EndEvent_1_di" bpmnElement="EndEvent_1">
        <dc:Bounds x="450" y="160" width="36" height="36"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="156" y="178"/>
        <di:waypoint x="230" y="178"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint x="380" y="178"/>
        <di:waypoint x="450" y="178"/>
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    storage_dir = tmp_path_factory.mktemp("pmga_e2e_storage")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["STORAGE_DIR"] = str(storage_dir)
    env["LOG_LEVEL"] = "INFO"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"Uvicorn terminated early ({proc.returncode}): {out}")
            try:
                with urllib.request.urlopen(f"{base_url}/api/v1/projects", timeout=1.5) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            raise TimeoutError("Timed out waiting for live uvicorn server.")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _create_project_via_ui(page, title: str):
    page.click("#btn-new-project")
    page.fill("#input-new-project-title", title)
    page.click("#btn-confirm-new-project")
    page.wait_for_function(
        f"() => {{ const s = document.getElementById('select-project'); return s && s.options[s.selectedIndex]?.textContent.includes({title!r}); }}"
    )


def test_browser_full_personas_a_b_c_d_and_research(live_server, tmp_path):
    chromium_bin = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else None
    with sync_playwright() as p:
        launch_opts = {"headless": True, "args": ["--no-sandbox"]}
        if chromium_bin:
            launch_opts["executable_path"] = chromium_bin
        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page_errors = []
        server_5xx = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "response",
            lambda res: server_5xx.append(f"{res.status} {res.url}") if res.status >= 500 else None,
        )

        # ------------------------------------------------------------------
        # 1. Landing Page -> Studio Separation & /research Route
        # ------------------------------------------------------------------
        page.goto(live_server, wait_until="domcontentloaded")
        assert "Open Workbench Studio" in page.inner_text("body")
        page.click("a.hero-cta-link[href='/studio']")
        page.wait_for_selector("#btn-onboard-scratch")

        studio_text = page.inner_text("body")
        assert "Deterministic Civic Rules Mode" in studio_text
        assert "12-Model Ground-Truth Corpus" not in studio_text

        # Visit /research separately and verify corpus + standards catalog
        page.goto(f"{live_server}/research", wait_until="networkidle")
        page.wait_for_function("() => document.querySelectorAll('#select-corpus-model option').length === 12")
        page.click("#btn-inspect-corpus")
        page.wait_for_selector("#corpus-preview-box:not(.hidden)")
        assert len(page.inner_text("#corpus-style-text").strip()) > 10
        page.click("#btn-verify-standards")
        page.wait_for_selector("#standards-verify-banner:not(.hidden)")

        # Return to /studio
        page.goto(f"{live_server}/studio", wait_until="networkidle")
        page.wait_for_selector("#btn-new-project")

        # ------------------------------------------------------------------
        # 2. Persona A: Bayreuth Messy Excel -> 3 Processes -> Maximumprinzip -> Gap Badge Slide-over
        # ------------------------------------------------------------------
        _create_project_via_ui(page, "Persona A Bayreuth")

        xlsx_path = tmp_path / "bayreuth_assets.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Systemliste"
        ws.append(["System / Prozess", "Kritikalität"])
        ws.append(["Anmeldesystem", "kritisch"])
        ws.append(["Gutscheinsystem", "kritisch"])
        ws.append(["HR System", "kritisch"])
        wb.save(xlsx_path)

        page.select_option("#select-upload-doctype", "EXCEL_INVENTORY")
        page.set_input_files("#file-universal-upload", str(xlsx_path))
        page.wait_for_selector("#pending-file-chip:not(.hidden)")
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.querySelectorAll('#session-feed .turn-bubble').length >= 2")

        # Disambiguate into 3 distinct business processes
        page.fill(
            "#input-turn-message",
            "Anmeldesystem supports Citizen Registration (Bürgeramt); Gutscheinsystem supports Voucher Issuing (Kämmerei); HR System supports Staff Onboarding (HR)",
        )
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.querySelectorAll('#select-process-model option').length === 3")

        # Assign CIA/BIA to Citizen Registration and verify inheritance onto Anmeldesystem
        page.fill(
            "#input-turn-message",
            "For Citizen Registration: Confidentiality: Very High, Integrity: High, Availability: Very High, RTO: 4h, MTPD: 12h",
        )
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.querySelectorAll('#session-feed .turn-bubble').length >= 6")

        page.click("#tab-btn-architecture")
        arch_text = page.inner_text("#architecture-tree")
        assert "Anmeldesystem" in arch_text
        assert "Inherited (BSI Maximumprinzip)" in arch_text
        assert "Very High" in arch_text

        # Back to BPMN map and click native/fallback gap badge overlay -> slideover drawer opens
        page.click("#tab-btn-bpmn")
        page.wait_for_selector(".pmga-gap-badge")
        page.locator(".pmga-gap-badge").first.click()
        page.wait_for_selector("#gap-slideover:not(.hidden)")
        assert "Helpful Next Steps" in page.inner_text("#slideover-title")
        page.click("#btn-close-slideover")

        # ------------------------------------------------------------------
        # 3. Persona B: Zero Docs Chat + Full Browser Reload Persistence
        # ------------------------------------------------------------------
        _create_project_via_ui(page, "Persona B Scratch")
        page.click("#btn-onboard-scratch")
        page.fill(
            "#input-turn-message",
            "Citizen Permit: Citizen submits email application -> Clerk checks in PermitApp -> Certificate mailed",
        )
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.querySelectorAll('#session-feed .turn-bubble').length === 2")

        # Full browser reload across session!
        page.reload(wait_until="networkidle")
        page.wait_for_function("() => document.querySelectorAll('#session-feed .turn-bubble').length === 2")
        assert "Citizen Permit" in page.inner_text("#bpmn-description-banner")
        assert page.inner_text("#maturity-percent") != "0%"

        # Resume same thread post-reload
        page.fill(
            "#input-turn-message",
            "Confidentiality: High, Integrity: High, Availability: High, RTO: 8h, MTPD: 24h",
        )
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.querySelectorAll('#session-feed .turn-bubble').length === 4")

        # ------------------------------------------------------------------
        # 4. Persona C: Negated Policy Drift -> CONTRADICTED & Policy vs. Reality
        # ------------------------------------------------------------------
        _create_project_via_ui(page, "Persona C Negated Policy")
        md_path = tmp_path / "handbook.md"
        md_path.write_text(
            "# Municipal Policy\n"
            "Multi-factor authentication (MFA) is currently not enforced for portal administrators.\n"
            "Kein Notfallplan für das Anmeldesystem vorhanden.\n",
            encoding="utf-8",
        )
        page.select_option("#select-upload-doctype", "FORMAL_POLICY")
        page.set_input_files("#file-universal-upload", str(md_path))
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.querySelectorAll('#session-feed .turn-bubble').length >= 2")

        page.click("#tab-btn-architecture")
        assert "CONTRADICTED" in page.inner_text("#evidence-ledger")
        page.click("#tab-btn-remediation")
        assert "Policy vs. Reality" in page.inner_text("#remediation-groups")

        # ------------------------------------------------------------------
        # 5. Persona D: BPMN Upload -> Extract Description & Check Gaps -> Status Update -> Branded Deliverables & PDFs
        # ------------------------------------------------------------------
        _create_project_via_ui(page, "Persona D BPMN")
        bpmn_path = tmp_path / "legacy_import.bpmn"
        bpmn_path.write_text(SAMPLE_UPLOAD_BPMN, encoding="utf-8")

        page.set_input_files("#file-universal-upload", str(bpmn_path))
        page.click("#btn-send-turn")
        page.wait_for_function("() => document.getElementById('bpmn-description-banner').textContent.includes('Export to Legacy DB')")

        page.click("#btn-extract-canvas-gaps")
        page.wait_for_selector(".pmga-gap-badge")
        page.locator(".pmga-gap-badge").first.click()
        page.wait_for_selector("#gap-slideover:not(.hidden)")
        page.locator("#slideover-body [data-slideover-status='IN_PROGRESS']").first.click()
        page.wait_for_function("() => document.getElementById('slideover-body').textContent.includes('IN PROGRESS')")
        page.click("#btn-close-slideover")

        # Verify Inspect-on-Diagram button from Action & Remediation Plan jumps cleanly back to BPMN slide-over
        page.click("#tab-btn-remediation")
        page.wait_for_selector("[data-inspect-gap-id]")
        page.locator("[data-inspect-gap-id]").first.click()
        page.wait_for_selector("#view-bpmn.active")
        page.wait_for_selector("#gap-slideover:not(.hidden)")
        page.click("#btn-close-slideover")

        # Deliverables & Live Branding Customizer + Real PDF Byte Verification across all 4 doc types
        page.click("#tab-btn-deliverables")
        page.fill("#input-brand-org", "Stadt Teststadt Municipal")
        page.select_option("#select-brand-font", "Georgia")
        page.fill("#input-brand-primary", "#14532D")
        page.click("#btn-apply-brand")
        page.wait_for_function(
            """() => {
                const frame = document.getElementById('deliverable-preview-frame');
                const doc = frame && frame.contentDocument;
                return doc && doc.body && doc.body.textContent.includes('Stadt Teststadt Municipal');
            }"""
        )

        for doc_type in ("remediation_plan", "prozesssteckbrief", "strukturanalyse", "complete_pack"):
            page.select_option("#select-deliverable-type", doc_type)
            pdf_href = page.get_attribute("#link-download-pdf", "href")
            assert pdf_href and doc_type in pdf_href
            pdf_resp = context.request.get(f"{live_server}{pdf_href}")
            assert pdf_resp.status == 200
            assert "application/pdf" in pdf_resp.headers.get("content-type", "")
            assert pdf_resp.body().startswith(b"%PDF-")

        # Non-blocking 2-click GDPR wipe cleanup verification
        page.click("#btn-gdpr-purge")
        assert "Confirm Wipe" in page.inner_text("#btn-gdpr-purge")
        page.click("#btn-gdpr-purge")
        page.wait_for_function(
            "() => !document.getElementById('select-project').textContent.includes('Persona D BPMN')"
        )

        assert page_errors == [], f"Uncaught JS errors in browser: {page_errors}"
        assert server_5xx == [], f"Unexpected 5xx responses during E2E: {server_5xx}"

        browser.close()
