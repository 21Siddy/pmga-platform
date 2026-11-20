import os
import re
import json
import time
import uuid
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.database import init_db, get_db, purge_project_data
from app.errors import (
    PMGAError,
    PayloadTooLargeError,
    UnsupportedMediaError,
    ValidationError,
)
from app.logging_config import configure_logging, log_context, request_id_var
from app.seed import ensure_initial_datasets
from app.services.document_service import DocumentService
from app.services.iso_excel_service import ISOExcelService
from app.services.llm_client import LLMClient
from app.services.bpmn_engine import BPMNEngine, BPMNProcessGraph
from app.services.bpmn_parser import BPMNParser
from app.services.perturbation_engine import PerturbationEngine
from app.services.matei_evaluator import MateiEvaluator, EvaluationMetrics
from app.seed.corpus_builder import CORPUS_MODELS
from app.services.gap_analyzer import GapAnalyzer
from app.services.survey_service import SurveyService
from app.services.bpmn_verifier import BPMNVerifier

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Automatic application lifecycle manager."""
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    init_db()
    ensure_initial_datasets()
    logger.info("PMGA v2 application startup complete (env=%s)", settings.APP_ENV)
    yield


app = FastAPI(
    title="PMGA — Process Management and Gap Analysis",
    description="GovTech & Critical Infrastructure Process Modeling and Compliance Gap Workbench",
    version="2.0.0",
    lifespan=lifespan,
)


@app.exception_handler(PMGAError)
async def pmga_domain_exception_handler(request: Request, exc: PMGAError):
    req_id = request_id_var.get()
    logger.warning(
        "Domain error [%s] on %s: %s",
        exc.error_code,
        request.url.path,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_payload(request_id=req_id, path=request.url.path),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = request_id_var.get()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "error_code": f"HTTP_{exc.status_code}",
            "error_type": "HTTPException",
            "request_id": req_id,
            "path": request.url.path,
        },
    )


# Global Structured JSON Error Handler (Prevents raw HTML 500s from reaching browser)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = request_id_var.get()
    logger.error("Global unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "error_code": "PMGA_INTERNAL_ERROR",
            "error_type": exc.__class__.__name__,
            "request_id": req_id,
            "path": request.url.path,
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_and_cache_middleware(request: Request, call_next):
    raw_req_id = request.headers.get("X-Request-ID") or ""
    clean_req_id = re.sub(r"[^A-Za-z0-9_\-]", "", raw_req_id)[:48] or str(uuid.uuid4())[:12]
    started = time.perf_counter()
    with log_context(request_id=clean_req_id):
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = clean_req_id
        if request.url.path.startswith("/static") or request.url.path in ("/", "/studio", "/research"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        if not request.url.path.startswith("/static"):
            logger.info(
                "HTTP %s %s -> %s (%.2fms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
        return response

# Health & LLM Status
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/api/v1/health")
def health_check():
    return {
        "status": "healthy",
        "app_env": settings.APP_ENV,
        "database": "sqlite_ready",
        "version": "1.1.0"
    }

@app.get("/api/v1/llm/status")
def get_llm_status():
    return LLMClient.get_provider_status()

@app.get("/api/v1/llm/providers")
def list_llm_providers():
    """The providers the UI may offer, without any secrets."""
    return {"providers": LLMClient.list_providers()}

class LLMTestRequest(BaseModel):
    provider: str
    model_name: Optional[str] = None
    base_url: Optional[str] = None

@app.post("/api/v1/llm/test")
def test_llm_connection(
    req: LLMTestRequest,
    x_llm_api_key: Optional[str] = Header(None),
    x_gemini_api_key: Optional[str] = Header(None),
):
    """One real, cheap call. Reports success or a plain failure reason."""
    key = x_llm_api_key or x_gemini_api_key
    return LLMClient.test_connection(
        provider=req.provider,
        api_key=key,
        model_name=req.model_name,
        base_url=req.base_url,
    )

# 1. Projects API
class ProjectCreateRequest(BaseModel):
    title: str
    description: Optional[str] = ""

@app.get("/api/v1/projects")
def list_projects():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

@app.post("/api/v1/projects")
def create_project(req: ProjectCreateRequest):
    proj_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, description) VALUES (?, ?, ?)",
            (proj_id, req.title, req.description)
        )
    return {"id": proj_id, "title": req.title, "description": req.description}

@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: str):
    with get_db() as conn:
        p = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        docs = conn.execute("SELECT * FROM documents WHERE project_id = ?", (project_id,)).fetchall()
        bpmn = conn.execute("SELECT * FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        gaps = conn.execute("SELECT * FROM gap_assessments WHERE project_id = ?", (project_id,)).fetchall()

        return {
            "project": dict(p),
            "documents": [dict(d) for d in docs],
            "bpmn_model": dict(bpmn) if bpmn else None,
            "gaps": [dict(g) for g in gaps]
        }

# 2. Public Benchmarks API
@app.get("/api/v1/datasets/benchmarks")
def get_benchmarks():
    bm_file = os.path.join(settings.DATA_DIR, "public_benchmarks", "benchmarks_index.json")
    if os.path.exists(bm_file):
        with open(bm_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

@app.post("/api/v1/datasets/benchmarks/load")
def load_benchmark_into_project(
    benchmark_id: str = Form(...),
    project_id: Optional[str] = Form(None)
):
    bm_file = os.path.join(settings.DATA_DIR, "public_benchmarks", "benchmarks_index.json")
    if not os.path.exists(bm_file):
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    
    with open(bm_file, "r", encoding="utf-8") as f:
        benchmarks = json.load(f)
        
    bm = next((b for b in benchmarks if b["id"] == benchmark_id), None)
    if not bm:
        raise HTTPException(status_code=404, detail="Specific benchmark not found")

    target_pid = project_id or str(uuid.uuid4())
    with get_db() as conn:
        if not project_id:
            conn.execute(
                "INSERT INTO projects (id, title, description) VALUES (?, ?, ?)",
                (target_pid, bm["title"], bm["description"])
            )
        # Add actual process
        doc_id_actual = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO documents (id, project_id, filename, doc_type, extracted_text) VALUES (?, ?, ?, ?, ?)",
            (doc_id_actual, target_pid, "operational_journal.txt", "ACTUAL_PROCESS", bm["actual_process"])
        )
        # Add formal policy
        doc_id_formal = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO documents (id, project_id, filename, doc_type, extracted_text) VALUES (?, ?, ?, ?, ?)",
            (doc_id_formal, target_pid, "formal_policy.md", "FORMAL_POLICY", bm["formal_policy"])
        )
        # Add BPMN model
        bpmn_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO bpmn_models (id, project_id, title, version, bpmn_xml) VALUES (?, ?, ?, ?, ?)",
            (bpmn_id, target_pid, bm["title"], 1, bm["bpmn_xml"])
        )

    # Extract the operational reality into the survey so the assessment
    # actually starts on load, same as a handbook upload would.
    try:
        actions = BPMNVerifier.extract_atomic_actions(bm["actual_process"])
        if actions:
            SurveyService.ingest_rows_into_survey(
                target_pid,
                [{"sheet_name": "General", "raw_entry": action} for action in actions],
            )
    except Exception as exc:
        logger.warning(f"Benchmark survey auto-ingest warning: {exc}")

    return {
        "status": "success",
        "project_id": target_pid,
        "title": bm["title"],
        "message": "Benchmark loaded successfully into project workspace."
    }

# 3. Document Ingestion & PII Pre-flight Check
@app.post("/api/v1/documents/upload")
async def upload_document(
    project_id: str = Form(...),
    doc_type: str = Form(...),
    file: UploadFile = File(...)
):
    safe_filename = os.path.basename(file.filename or "upload.bin")
    save_path = os.path.join(settings.uploads_path, f"{uuid.uuid4()}_{safe_filename}")
    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        extracted = DocumentService.extract_text_from_file(save_path, safe_filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    pii_report = DocumentService.scan_for_pii(extracted)
    doc_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute(
            """INSERT INTO documents 
               (id, project_id, filename, file_path, doc_type, extracted_text, redacted_text, pii_detected_json) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, project_id, file.filename, save_path, doc_type, extracted, pii_report["redacted_text"], json.dumps(pii_report["detections"]))
        )

    # Automatic non-blocking survey row & evidence extraction
    from app.services.intake_extractor import map_excel_sheets_to_survey_items, extract_handbook_evidence

    survey_count = 0
    evidence_summary = {"entities_touched": 0, "assessments_proven": 0, "citations": []}
    ext = os.path.splitext(safe_filename)[1].lower()
    if ext in ['.xlsx', '.xlsm', '.xltx']:
        try:
            sheets = DocumentService.extract_excel_sheets(save_path)
            items = map_excel_sheets_to_survey_items(sheets)
            if items:
                inserted_ids = SurveyService.ingest_rows_into_survey(project_id, items)
                survey_count = len(inserted_ids)
                # Non-blockingly compile initial BPMN diagram(s)
                graphs = SurveyService.compile_process_graphs(project_id)
                graph = graphs[0]
                bpmn_xml = BPMNEngine.compile_graph_to_bpmn_xml(graph)
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO bpmn_models (id, project_id, title, version, bpmn_xml, graph_json) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), project_id, graph.process_title, 1, bpmn_xml, graph.model_dump_json())
                    )
        except Exception as e:
            logger.warning(f"Excel survey auto-ingest warning: {e}")
    else:
        if doc_type == "ACTUAL_PROCESS":
            try:
                actions = BPMNVerifier.extract_atomic_actions(extracted)
                if actions:
                    items = [{"sheet_name": "General", "raw_entry": a} for a in actions]
                    inserted_ids = SurveyService.ingest_rows_into_survey(project_id, items)
                    survey_count = len(inserted_ids)
            except Exception as e:
                logger.warning(f"Text survey auto-ingest warning: {e}")
        try:
            evidence_summary = extract_handbook_evidence(project_id, extracted, file.filename or safe_filename, doc_type)
        except Exception as e:
            logger.warning(f"Handbook evidence extraction warning: {e}")

    return {
        "id": doc_id,
        "filename": file.filename,
        "doc_type": doc_type,
        "character_count": len(extracted),
        "pii_report": pii_report,
        "survey_rows_created": survey_count,
        "evidence_extracted": evidence_summary,
    }

# 4. BPMN Generation with Scientific Verification
class BPMNGenerateRequest(BaseModel):
    project_id: str
    process_text: str
    policy_context: Optional[str] = ""
    provider: Optional[str] = None
    model_name: Optional[str] = None
    base_url: Optional[str] = None

@app.post("/api/v1/bpmn/generate")
def generate_bpmn_from_text(
    req: BPMNGenerateRequest,
    x_llm_api_key: Optional[str] = Header(None),
    x_gemini_api_key: Optional[str] = Header(None),
):
    key = x_llm_api_key or x_gemini_api_key
    graph = BPMNEngine.generate_graph_from_text(
        process_text=req.process_text,
        policy_context=req.policy_context,
        api_key=key,
        provider=req.provider,
        model_name=req.model_name,
        base_url=req.base_url,
    )
    bpmn_xml = BPMNEngine.compile_graph_to_bpmn_xml(graph)
    bpmn_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute(
            "INSERT INTO bpmn_models (id, project_id, title, version, bpmn_xml, graph_json) VALUES (?, ?, ?, ?, ?, ?)",
            (bpmn_id, req.project_id, graph.process_title, 1, bpmn_xml, graph.model_dump_json())
        )

    return {
        "id": bpmn_id,
        "title": graph.process_title,
        "bpmn_xml": bpmn_xml,
        "graph": graph.model_dump(),
        "telemetry": graph.telemetry.model_dump() if graph.telemetry else None
    }

class BPMNUpdateRequest(BaseModel):
    project_id: str
    bpmn_xml: str

@app.put("/api/v1/bpmn/update")
def update_bpmn_xml(req: BPMNUpdateRequest):
    graph_json = None
    telemetry_dict = None
    title = "Updated Model"
    try:
        parsed = BPMNParser.parse_xml_to_graph(req.bpmn_xml, fallback_title="Updated Model")
        title = parsed.process_title or title
        telemetry = BPMNVerifier.verify_graph(
            graph_nodes=[n.model_dump() for n in parsed.nodes],
            graph_edges=[e.model_dump() for e in parsed.edges],
            source_text=" ".join(n.name for n in parsed.nodes),
        )
        parsed.telemetry = telemetry
        graph_json = parsed.model_dump_json()
        telemetry_dict = telemetry.model_dump()
    except Exception as exc:
        logger.warning(f"BPMN XML sync parse warning: {exc}")

    with get_db() as conn:
        latest = conn.execute(
            "SELECT version FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (req.project_id,)
        ).fetchone()
        new_version = (latest["version"] + 1) if latest else 1
        bpmn_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO bpmn_models (id, project_id, title, version, bpmn_xml, graph_json) VALUES (?, ?, ?, ?, ?, ?)",
            (bpmn_id, req.project_id, title, new_version, req.bpmn_xml, graph_json)
        )
    return {"id": bpmn_id, "version": new_version, "status": "saved", "telemetry": telemetry_dict}

# 5. ISO 27002 Controls & Excel Explorer
@app.get("/api/v1/iso/controls")
def list_iso_controls(chapter: Optional[str] = Query(None)):
    controls = ISOExcelService.load_master_checklist()
    if chapter:
        controls = [c for c in controls if chapter.lower() in c["chapter"].lower()]
    return {
        "total_controls": len(controls),
        "controls": controls
    }

# 6. Gap Analysis & Compensating Controls Engine
class RunGapAnalysisRequest(BaseModel):
    project_id: str
    selected_control_ids: Optional[List[str]] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    base_url: Optional[str] = None

@app.post("/api/v1/analysis/gaps")
def run_gap_analysis(
    req: RunGapAnalysisRequest,
    x_llm_api_key: Optional[str] = Header(None),
    x_gemini_api_key: Optional[str] = Header(None),
):
    key = x_llm_api_key or x_gemini_api_key
    with get_db() as conn:
        docs = conn.execute("SELECT * FROM documents WHERE project_id = ?", (req.project_id,)).fetchall()
        bpmn = conn.execute("SELECT * FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1", (req.project_id,)).fetchone()
        
    # Privacy-by-default: always prefer PII-redacted text before external LLM calls
    actual_texts = [(d["redacted_text"] or d["extracted_text"]) for d in docs if d["doc_type"] == "ACTUAL_PROCESS"]
    policy_texts = [(d["redacted_text"] or d["extracted_text"]) for d in docs if d["doc_type"] == "FORMAL_POLICY"]
    
    p_text = "\n\n".join(actual_texts) or "Operational staff complete tasks manually without formal verification."
    pol_text = "\n\n".join(policy_texts) or "Standard ISO 27002 administrative baseline rules."
    bpmn_xml = bpmn["bpmn_xml"] if bpmn else ""

    all_controls = ISOExcelService.load_master_checklist()
    candidate_controls = (
        [c for c in all_controls if c["control_id"] in req.selected_control_ids]
        if req.selected_control_ids else all_controls
    )

    result = GapAnalyzer.analyze_process(
        process_text=p_text,
        policy_text=pol_text,
        bpmn_xml=bpmn_xml,
        selected_controls=candidate_controls,
        api_key=key,
        provider=req.provider,
        model_name=req.model_name,
        base_url=req.base_url,
    )

    # Merge deterministic entity-graph & AST plothole findings (mapping_verifier single source of truth)
    try:
        from app.services.ir_engine import analyze_plotholes
        from app.services.gap_analyzer import GapFinding

        with get_db() as conn:
            s_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM process_survey_rows WHERE project_id = ? ORDER BY row_order ASC",
                    (req.project_id,),
                ).fetchall()
            ]
        plothole_report = analyze_plotholes(req.project_id, s_rows)
        existing_titles = {f.control_title.lower() for f in result.findings}
        for ph in plothole_report["plotholes"]:
            title = f"{ph['kind'].replace('_', ' ').title()}: {ph.get('entry') or 'Workflow'}"
            if title.lower() in existing_titles:
                continue
            existing_titles.add(title.lower())
            result.findings.append(
                GapFinding(
                    control_id=ph.get("req_id") or "IR-CHECK",
                    control_title=title[:80],
                    chapter="BSI / AST Structural Verification",
                    status="CRITICAL_GAP" if ph["severity"] == "CRITICAL" else "PARTIAL_GAP",
                    policy_claim="All processes, systems, owners, exception paths, and recovery targets (RTO < MTPD) must be verified.",
                    operational_reality=ph["message"],
                    plain_english_gap=ph["message"],
                    actionable_todos=["Resolve this open structural gap in the Guided Survey or Entity Structure panel."],
                    roles_to_consult=["Process Owner", "IT Security Officer"],
                    compensating_controls=["Document temporary manual workaround and owner until formal control is verified."],
                )
            )
    except Exception as exc:
        logger.warning(f"Deterministic plothole merge warning: {exc}")

    with get_db() as conn:
        conn.execute("DELETE FROM gap_assessments WHERE project_id = ?", (req.project_id,))
        for f in result.findings:
            conn.execute(
                """INSERT INTO gap_assessments 
                   (id, project_id, control_id, control_title, chapter, status, policy_claim, operational_reality,
                    plain_english_gap, actionable_todos_json, roles_to_consult_json, compensating_controls_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), req.project_id, f.control_id, f.control_title, f.chapter, f.status,
                    f.policy_claim, f.operational_reality, f.plain_english_gap,
                    json.dumps(f.actionable_todos), json.dumps(f.roles_to_consult),
                    json.dumps(f.compensating_controls)
                )
            )

    return result.model_dump()

# 7. Exports (.bpmn, .xlsx Gap & Survey Workbook)
@app.get("/api/v1/export/excel/{project_id}")
def export_excel_report(project_id: str):
    with get_db() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        gaps_rows = conn.execute("SELECT * FROM gap_assessments WHERE project_id = ?", (project_id,)).fetchall()
        survey_rows = conn.execute("SELECT * FROM process_survey_rows WHERE project_id = ? ORDER BY row_order ASC", (project_id,)).fetchall()
        
    gaps_list = []
    for g in gaps_rows:
        item = dict(g)
        item["actionable_todos"] = json.loads(g["actionable_todos_json"] or "[]")
        item["roles_to_consult"] = json.loads(g["roles_to_consult_json"] or "[]")
        item["compensating_controls"] = json.loads(g["compensating_controls_json"] or "[]")
        gaps_list.append(item)

    file_name = f"PMGA_ISO27002_Gap_Report_{project_id[:8]}.xlsx"
    out_path = os.path.join(settings.exports_path, file_name)
    ISOExcelService.export_annotated_gap_report(
        output_path=out_path,
        project_title=proj["title"],
        gap_assessments=gaps_list,
        survey_rows=[dict(r) for r in survey_rows]
    )
    return FileResponse(out_path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=file_name)

@app.get("/api/v1/export/bpmn/{project_id}")
def export_bpmn_xml(project_id: str):
    with get_db() as conn:
        bpmn = conn.execute("SELECT * FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        if not bpmn:
            raise HTTPException(status_code=404, detail="No BPMN model found for this project")
        file_name = f"PMGA_Process_Model_{project_id[:8]}.bpmn"
        out_path = os.path.join(settings.exports_path, file_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(bpmn["bpmn_xml"])
    return FileResponse(out_path, media_type="application/xml", filename=file_name)

# 8. Verbatim Survey & Prelead Options API
class SurveyAnswerRequest(BaseModel):
    question_code: str
    answer: str
    accept_as_concept: Optional[bool] = True

@app.get("/api/v1/projects/{project_id}/survey")
def get_project_survey(project_id: str):
    return SurveyService.get_survey_state(project_id)

@app.post("/api/v1/projects/{project_id}/survey/{row_id}/answer")
def answer_survey_question(project_id: str, row_id: str, req: SurveyAnswerRequest):
    try:
        updated_state = SurveyService.save_verbatim_answer(
            project_id=project_id,
            row_id=row_id,
            question_code=req.question_code,
            answer_text=req.answer,
            accept_as_concept=bool(req.accept_as_concept)
        )
        # Non-blockingly re-compile BPMN model
        graph = SurveyService.compile_survey_to_bpmn_graph(project_id)
        bpmn_xml = BPMNEngine.compile_graph_to_bpmn_xml(graph)
        
        with get_db() as conn:
            latest = conn.execute(
                "SELECT version FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (project_id,)
            ).fetchone()
            new_version = (latest["version"] + 1) if latest else 1
            bpmn_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO bpmn_models (id, project_id, title, version, bpmn_xml, graph_json) VALUES (?, ?, ?, ?, ?, ?)",
                (bpmn_id, project_id, graph.process_title, new_version, bpmn_xml, graph.model_dump_json())
            )

        return {
            "status": "success",
            "survey": updated_state,
            "bpmn_xml": bpmn_xml,
            "graph": graph.model_dump(),
            "bpmn_version": new_version
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

# 8b. Project Diagnostics: what is ready, what is missing, what is next
@app.get("/api/v1/diagnostics/{project_id}")
def get_project_diagnostics(project_id: str):
    """Answers, in one place, why a project is not further along yet."""
    with get_db() as conn:
        project = conn.execute("SELECT id, title, created_at FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        doc_rows = conn.execute(
            "SELECT doc_type, COUNT(*) AS cnt FROM documents WHERE project_id = ? GROUP BY doc_type",
            (project_id,)
        ).fetchall()
        bpmn_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM bpmn_models WHERE project_id = ?",
            (project_id,)
        ).fetchone()["cnt"]
        survey_state = SurveyService.get_survey_state(project_id)

    doc_counts = {row["doc_type"]: row["cnt"] for row in doc_rows}
    actual_docs = doc_counts.get("ACTUAL_PROCESS", 0)
    policy_docs = doc_counts.get("FORMAL_POLICY", 0)
    survey_rows = survey_state["total_rows"]
    filled = survey_state["filled_slots"]
    total = survey_state["total_slots"]
    percent = survey_state["completion_percent"]

    from app.services.mapping_verifier import verify_catalog

    standards = verify_catalog()

    llm_status = LLMClient.get_provider_status()
    any_key = bool(llm_status["gemini"]["configured"] or llm_status["kiconnect"]["configured"])

    if actual_docs == 0 and policy_docs == 0:
        upload_hint = "One upload per file: internal sheet first, then the handbook."
    elif actual_docs == 0:
        upload_hint = "Add the internal Excel or process notes file."
    else:
        upload_hint = "Add the handbook or policy file."

    steps = [
        {
            "id": "create_project",
            "title": "Create the assessment",
            "done": True,
            "detail": project["title"],
            "hint": "",
            "action": "none",
        },
        {
            "id": "upload_documents",
            "title": "Upload the handbook and the internal sheet",
            "done": actual_docs > 0 and policy_docs > 0,
            "detail": f"{actual_docs} operational, {policy_docs} policy",
            "hint": upload_hint,
            "action": "upload",
        },
        {
            "id": "extract_structure",
            "title": "Review what was extracted",
            "done": survey_rows > 0,
            "detail": f"{survey_rows} entries recorded",
            "hint": "Drop your Excel sheet or paste notes above, then generate.",
            "action": "upload",
        },
        {
            "id": "review_diagram",
            "title": "Check the generated diagram",
            "done": bpmn_count > 0,
            "detail": f"{bpmn_count} version(s) saved",
            "hint": "Generate the diagram from your notes to start the interview.",
            "action": "generate",
        },
        {
            "id": "answer_interview",
            "title": "Answer the open questions",
            "done": survey_rows > 0 and percent == 100,
            "detail": f"{filled}/{total} answered ({percent}%)",
            "hint": "Work the first open question below.",
            "action": "survey",
        },
        {
            "id": "set_model_key",
            "title": "Bring your own AI key",
            "done": any_key,
            "detail": "Configured on this machine." if any_key else "Not configured.",
            "hint": "Add your key for Gemini or the university connection.",
            "action": "byok",
        },
        {
            "id": "standards_check",
            "title": "Standards and survey line up",
            "done": bool(standards["ok"]),
            "detail": f"{standards['requirement_count']} requirements, "
            f"{sum(1 for c in standards['checks'] if c['ok'])}/{len(standards['checks'])} checks green",
            "hint": "Re-run at /api/v1/standards/verify-mapping.",
            "action": "none",
        },
    ]

    return {
        "project": {"id": project["id"], "title": project["title"], "created_at": project["created_at"]},
        "documents": {"actual": actual_docs, "policy": policy_docs},
        "survey": {
            "rows": survey_rows,
            "filled_slots": filled,
            "total_slots": total,
            "completion_percent": percent,
        },
        "diagram": {"versions": bpmn_count},
        "standards": {
            "ok": standards["ok"],
            "requirement_count": standards["requirement_count"],
            "question_count": standards["question_count"],
        },
        "llm": {"configured": any_key, "providers": llm_status},
        "steps": steps,
    }

# 9. GDPR Data Erasure / 1-Click Cryptographic Wipe
@app.delete("/api/v1/gdpr/purge/{project_id}")
def gdpr_purge_project(project_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM entity_requirement_assessments WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM entity_dependencies WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM org_entities WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM gap_assessments WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM bpmn_models WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM process_survey_rows WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM accepted_concepts WHERE project_id = ?", (project_id,))
        docs = conn.execute("SELECT file_path FROM documents WHERE project_id = ?", (project_id,)).fetchall()
        for d in docs:
            fp = d["file_path"]
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass
        conn.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    return {
        "status": "success",
        "message": f"Project {project_id} permanently expunged under GDPR Art. 17."
    }

# 10. Ground-Truth Corpus & Matei et al. (2026) Evaluation Workbench
class CorpusParseRequest(BaseModel):
    bpmn_xml: Optional[str] = None
    title: Optional[str] = "Uploaded BPMN Process"

class CorpusEvaluateRequest(BaseModel):
    ground_truth_model_id: Optional[str] = None
    ground_truth_xml: Optional[str] = None
    predicted_graph: BPMNProcessGraph

@app.get("/api/v1/corpus")
def get_corpus_manifest():
    manifest_path = os.path.join(settings.DATA_DIR, "corpus", "corpus_manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return [
        {
            "id": m["id"],
            "title": m["title"],
            "archetype": m["archetype"],
            "reference": m["reference"]
        }
        for m in CORPUS_MODELS
    ]

@app.get("/api/v1/corpus/{model_id}")
def get_corpus_model_detail(model_id: str):
    matched = next((m for m in CORPUS_MODELS if m["id"] == model_id), None)
    if not matched:
        raise HTTPException(status_code=404, detail=f"Corpus model '{model_id}' not found.")
    
    graph = BPMNParser.parse_xml_to_graph(matched["bpmn_xml"], fallback_title=matched["title"])
    text_styles = PerturbationEngine.generate_all_styles(graph)
    
    return {
        "id": matched["id"],
        "title": matched["title"],
        "archetype": matched["archetype"],
        "reference": matched["reference"],
        "bpmn_xml": matched["bpmn_xml"],
        "graph": graph,
        "text_styles": text_styles
    }

@app.post("/api/v1/corpus/parse-bpmn")
async def parse_external_bpmn(request: Request):
    content_type = request.headers.get("content-type", "")
    xml_content = ""
    title = "Uploaded BPMN Process"

    if "application/json" in content_type:
        body = await request.json()
        xml_content = body.get("bpmn_xml", "")
        title = body.get("title", title)
    elif "multipart/form-data" in content_type:
        form = await request.form()
        file_obj = form.get("file")
        if file_obj and hasattr(file_obj, "read"):
            raw_bytes = await file_obj.read()
            xml_content = raw_bytes.decode("utf-8", errors="replace")
            title = getattr(file_obj, "filename", title) or title
        elif "bpmn_xml" in form:
            xml_content = str(form.get("bpmn_xml"))
    else:
        try:
            body = await request.json()
            xml_content = body.get("bpmn_xml", "")
            title = body.get("title", title)
        except Exception:
            pass

    if not xml_content.strip():
        raise HTTPException(status_code=400, detail="Provide either a BPMN XML file upload or 'bpmn_xml' JSON string.")

    try:
        graph = BPMNParser.parse_xml_to_graph(xml_content, fallback_title=title)
        text_styles = PerturbationEngine.generate_all_styles(graph)
        return {
            "title": graph.process_title,
            "graph": graph,
            "text_styles": text_styles,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "lanes": graph.lanes
        }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse BPMN XML: {str(exc)}")

@app.post("/api/v1/corpus/evaluate")
def evaluate_reconstruction(req: CorpusEvaluateRequest):
    gt_graph = None
    if req.ground_truth_model_id:
        matched = next((m for m in CORPUS_MODELS if m["id"] == req.ground_truth_model_id), None)
        if not matched:
            raise HTTPException(status_code=404, detail=f"Ground-truth model '{req.ground_truth_model_id}' not found.")
        gt_graph = BPMNParser.parse_xml_to_graph(matched["bpmn_xml"], fallback_title=matched["title"])
    elif req.ground_truth_xml:
        gt_graph = BPMNParser.parse_xml_to_graph(req.ground_truth_xml, fallback_title="Ground Truth")
    else:
        raise HTTPException(status_code=400, detail="Provide 'ground_truth_model_id' or 'ground_truth_xml'.")

    metrics = MateiEvaluator.evaluate(ground_truth=gt_graph, predicted=req.predicted_graph)
    return metrics

# 11. Standards catalog & master discovery survey (req_id is the shared key)
@app.get("/api/v1/standards/requirements")
def list_standards_requirements(
    standard_code: Optional[str] = Query(None),
    phase_category: Optional[str] = Query(None),
    target_scope: Optional[str] = Query(None),
):
    from app.services.standards_service import list_requirements

    requirements = list_requirements(
        standard_code=standard_code,
        phase_category=phase_category,
        target_scope=target_scope,
    )
    return {
        "total": len(requirements),
        "requirements": requirements,
    }

@app.get("/api/v1/standards/requirements/{req_id}")
def get_standards_requirement(req_id: str):
    from app.services.standards_service import get_requirement

    requirement = get_requirement(req_id)
    if not requirement:
        raise HTTPException(status_code=404, detail=f"Requirement '{req_id}' not found")
    return requirement

@app.get("/api/v1/standards/survey")
def get_master_survey(phase_category: Optional[str] = Query(None)):
    from app.services.standards_service import list_survey_questions

    questions = list_survey_questions(phase_category=phase_category)
    return {
        "total": len(questions),
        "questions": questions,
    }

@app.get("/api/v1/standards/verify-mapping")
def verify_standards_mapping():
    from app.services.mapping_verifier import verify_catalog

    return verify_catalog()

@app.get("/api/v1/standards/download/{workbook}")
def download_standard_workbook(workbook: str):
    from app.services.standards_service import workbook_download_path

    path = workbook_download_path(workbook)
    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown or missing workbook '{workbook}'. Use one of: bsi_200_2, bsi_200_4, iso_27001, iso_27002, survey.",
        )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(path),
    )

# 12. Object-Centric Entity Structure, Plotholes IR & NIST OSCAL Export
class EntityCreateRequest(BaseModel):
    entity_type: str
    name: str
    description: Optional[str] = None
    parent_id: Optional[str] = None
    owner_role: Optional[str] = None
    confidentiality: Optional[str] = None
    integrity: Optional[str] = None
    availability: Optional[str] = None
    rto: Optional[float] = None
    mtpd: Optional[float] = None

class DependencyCreateRequest(BaseModel):
    from_entity_id: str
    to_entity_id: str
    dependency_type: str = "SUPPORTS"
    notes: Optional[str] = None

@app.get("/api/v1/projects/{project_id}/entities")
def get_project_entities(project_id: str):
    from app.services.entity_service import EntityService

    return EntityService.get_project_structure(project_id)

@app.post("/api/v1/projects/{project_id}/entities")
def create_project_entity(project_id: str, req: EntityCreateRequest):
    from app.services.entity_service import EntityService

    try:
        ent = EntityService.upsert_entity(
            project_id=project_id,
            entity_type=req.entity_type,
            name=req.name,
            description=req.description,
            parent_id=req.parent_id,
            owner_role=req.owner_role,
        )
        if req.confidentiality:
            EntityService.save_entity_assessment(
                project_id, ent["id"], "R005", answer_value=req.confidentiality, justification="Declared via entity editor"
            )
        if req.integrity:
            EntityService.save_entity_assessment(
                project_id, ent["id"], "R006", answer_value=req.integrity, justification="Declared via entity editor"
            )
        if req.availability:
            EntityService.save_entity_assessment(
                project_id, ent["id"], "R007", answer_value=req.availability, justification="Declared via entity editor"
            )
        if req.mtpd is not None:
            EntityService.save_entity_assessment(
                project_id, ent["id"], "R017", answer_value=str(req.mtpd), answer_numeric=req.mtpd
            )
        if req.rto is not None:
            EntityService.save_entity_assessment(
                project_id, ent["id"], "R018", answer_value=str(req.rto), answer_numeric=req.rto,
                context={"rto": req.rto, "mtpd": req.mtpd},
            )
        return EntityService.get_project_structure(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.delete("/api/v1/projects/{project_id}/entities/{entity_id}")
def delete_project_entity(project_id: str, entity_id: str):
    from app.services.entity_service import EntityService

    deleted = EntityService.delete_entity(project_id, entity_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return EntityService.get_project_structure(project_id)

@app.post("/api/v1/projects/{project_id}/dependencies")
def create_project_dependency(project_id: str, req: DependencyCreateRequest):
    from app.services.entity_service import EntityService

    try:
        dep = EntityService.upsert_dependency(
            project_id=project_id,
            from_entity_id=req.from_entity_id,
            to_entity_id=req.to_entity_id,
            dependency_type=req.dependency_type,
            notes=req.notes,
        )
        return {"dependency": dep, "structure": EntityService.get_project_structure(project_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.delete("/api/v1/projects/{project_id}/dependencies/{dep_id}")
def delete_project_dependency(project_id: str, dep_id: str):
    from app.services.entity_service import EntityService

    deleted = EntityService.delete_dependency(project_id, dep_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dependency not found")
    return EntityService.get_project_structure(project_id)

@app.get("/api/v1/projects/{project_id}/plotholes")
def get_project_plotholes(project_id: str):
    from app.services.ir_engine import analyze_plotholes

    with get_db() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM process_survey_rows WHERE project_id = ? ORDER BY row_order ASC",
                (project_id,),
            ).fetchall()
        ]
    return analyze_plotholes(project_id, rows)

@app.get("/api/v1/export/oscal/{project_id}")
def export_oscal_json(project_id: str):
    """Export NIST OSCAL Assessment Results JSON for audit-as-code portability."""
    from app.services.entity_service import EntityService

    with get_db() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        gaps = [dict(g) for g in conn.execute("SELECT * FROM gap_assessments WHERE project_id = ?", (project_id,)).fetchall()]

    structure = EntityService.get_project_structure(project_id)
    oscal_doc = {
        "assessment-results": {
            "uuid": project_id,
            "metadata": {
                "title": f"PMGA Assessment Results — {proj['title']}",
                "last-modified": proj["updated_at"],
                "version": "1.1.0",
                "oscal-version": "1.1.2",
            },
            "results": [
                {
                    "uuid": str(uuid.uuid4()),
                    "title": proj["title"],
                    "description": proj["description"] or "",
                    "observations": [
                        {
                            "uuid": a["id"],
                            "title": f"{a['req_id']} ({a['status']})",
                            "description": a.get("justification") or a.get("answer_value") or "",
                            "relevant-evidence": json.loads(a.get("evidence_json") or "[]"),
                        }
                        for ent in structure["entities"]
                        for a in ent.get("assessments", [])
                    ],
                    "findings": [
                        {
                            "uuid": g["id"],
                            "title": f"{g['control_id']} — {g['control_title']}",
                            "description": g["plain_english_gap"],
                            "target": {"status": {"state": "satisfied" if g["status"] == "COMPLIANT" else "not-satisfied"}},
                        }
                        for g in gaps
                    ],
                }
            ],
        }
    }
    return JSONResponse(content=oscal_doc)

# ----------------------------------------------------------------------------
# 13. PMGA v2 Multi-Session LangGraph Coach, Workspace & Bidirectional BPMN
# ----------------------------------------------------------------------------
class BPMNSyncV2Request(BaseModel):
    bpmn_xml: str
    extract_description: bool = True
    provider: Optional[str] = None
    model_name: Optional[str] = None
    base_url: Optional[str] = None


class GapStatusUpdateRequest(BaseModel):
    status: str


@app.get("/api/v1/projects/{project_id}/workspace")
def get_v2_workspace(project_id: str):
    from app.services.workspace_service import get_workspace_state

    return get_workspace_state(project_id)


@app.post("/api/v1/projects/{project_id}/turn")
async def run_v2_turn(
    project_id: str,
    request: Request,
    x_llm_api_key: Optional[str] = Header(None),
    x_gemini_api_key: Optional[str] = Header(None),
):
    from app.services.workflow_graph import run_project_turn

    key = x_llm_api_key or x_gemini_api_key
    content_type = (request.headers.get("content-type") or "").lower()

    input_kind = "chat"
    payload: Dict[str, Any] = {}
    language = "en"
    active_process_id: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    base_url: Optional[str] = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        language = str(form.get("language") or "en")
        active_process_id = str(form.get("active_process_id") or "") or None
        provider = str(form.get("provider") or "") or None
        model_name = str(form.get("model_name") or "") or None
        base_url = str(form.get("base_url") or "") or None
        doc_type = str(form.get("doc_type") or "FORMAL_POLICY")
        msg_text = str(form.get("message") or "").strip()
        file_obj = form.get("file")

        if file_obj and hasattr(file_obj, "read"):
            raw_bytes = await file_obj.read()
            if len(raw_bytes) > settings.MAX_UPLOAD_BYTES:
                raise PayloadTooLargeError(
                    f"Uploaded file exceeds maximum limit of {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                )
            safe_name = os.path.basename(getattr(file_obj, "filename", None) or "upload.bin")
            ext = os.path.splitext(safe_name)[1].lower()
            if ext in (".bpmn", ".xml"):
                input_kind = "bpmn_edit"
                payload = {
                    "bpmn_xml": raw_bytes.decode("utf-8", errors="replace"),
                    "filename": safe_name,
                    "title": os.path.splitext(safe_name)[0],
                    "process_id": active_process_id,
                }
            elif ext in (".xlsx", ".xlsm", ".xltx"):
                save_path = os.path.join(settings.uploads_path, f"{uuid.uuid4()}_{safe_name}")
                with open(save_path, "wb") as f:
                    f.write(raw_bytes)
                input_kind = "excel"
                payload = {
                    "file_path": save_path,
                    "filename": safe_name,
                    "doc_type": doc_type,
                }
            elif ext in (".pdf", ".docx", ".doc", ".csv", ".txt", ".md", ".markdown", ".json", ".yaml", ".yml"):
                save_path = os.path.join(settings.uploads_path, f"{uuid.uuid4()}_{safe_name}")
                with open(save_path, "wb") as f:
                    f.write(raw_bytes)
                try:
                    extracted = DocumentService.extract_text_from_file(save_path, safe_name)
                except Exception as exc:
                    try:
                        os.remove(save_path)
                    except OSError:
                        pass
                    raise ValidationError(f"Could not extract readable text from '{safe_name}': {exc}") from exc
                input_kind = "document"
                payload = {
                    "file_path": save_path,
                    "filename": safe_name,
                    "doc_type": doc_type,
                    "extracted_text": extracted,
                }
            else:
                raise UnsupportedMediaError(
                    f"Unsupported file format '{ext}'. Upload .xlsx, .pdf, .docx, .csv, .txt, .md, .json, .yaml, or .bpmn."
                )
        elif msg_text:
            input_kind = str(form.get("input_kind") or "chat")
            payload = {"message": msg_text}
        else:
            raise ValidationError("Provide either a message or an uploaded file.")
    else:
        try:
            body = await request.json()
        except Exception as exc:
            raise ValidationError("Request body must be valid JSON or multipart/form-data.") from exc
        if not isinstance(body, dict):
            raise ValidationError("JSON turn body must be an object.")
        input_kind = str(body.get("input_kind") or "chat")
        language = str(body.get("language") or "en")
        active_process_id = body.get("active_process_id") or body.get("process_id")
        provider = body.get("provider")
        model_name = body.get("model_name")
        base_url = body.get("base_url")
        if input_kind == "bpmn_edit":
            payload = {
                "bpmn_xml": body.get("bpmn_xml") or "",
                "title": body.get("title") or "Process Model",
                "process_id": active_process_id,
            }
        elif input_kind == "document":
            payload = {
                "filename": body.get("filename") or "notes.md",
                "doc_type": body.get("doc_type") or "FORMAL_POLICY",
                "extracted_text": body.get("text") or body.get("message") or "",
            }
        else:
            payload = {"message": body.get("message") or ""}

    return run_project_turn(
        project_id,
        input_kind=input_kind,
        payload=payload,
        language=language,
        active_process_id=active_process_id,
        api_key=key,
        provider=provider,
        model_name=model_name,
        base_url=base_url,
    )


@app.put("/api/v1/projects/{project_id}/processes/{process_id}/bpmn")
def sync_v2_process_bpmn(
    project_id: str,
    process_id: str,
    req: BPMNSyncV2Request,
    x_llm_api_key: Optional[str] = Header(None),
    x_gemini_api_key: Optional[str] = Header(None),
):
    from app.services.workspace_service import sync_bpmn_canvas_to_project

    key = x_llm_api_key or x_gemini_api_key
    return sync_bpmn_canvas_to_project(
        project_id,
        process_id,
        req.bpmn_xml,
        extract_description=req.extract_description,
        api_key=key,
        provider=req.provider,
        model_name=req.model_name,
        base_url=req.base_url,
    )


@app.patch("/api/v1/projects/{project_id}/gaps/{gap_id}")
def update_v2_gap_status(project_id: str, gap_id: str, req: GapStatusUpdateRequest):
    from app.services.workspace_service import update_gap_status

    return update_gap_status(project_id, gap_id, req.status)


class BrandProfileRequest(BaseModel):
    org_name: Optional[str] = None
    logo_data_uri: Optional[str] = None
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    font_family: Optional[str] = None
    footer_text: Optional[str] = None
    clear_logo: bool = False


@app.post("/api/v1/projects/{project_id}/brand")
def save_v2_brand_profile(project_id: str, req: BrandProfileRequest):
    from app.services.deliverable_service import save_brand_profile

    return save_brand_profile(
        project_id,
        org_name=req.org_name,
        logo_data_uri=req.logo_data_uri,
        primary_color=req.primary_color,
        accent_color=req.accent_color,
        font_family=req.font_family,
        footer_text=req.footer_text,
        clear_logo=req.clear_logo,
    )


@app.get("/api/v1/projects/{project_id}/deliverables/{doc_type}")
def get_v2_deliverable(
    project_id: str,
    doc_type: str,
    format: str = Query("html"),
    process_id: Optional[str] = Query(None),
):
    from fastapi.responses import HTMLResponse
    from app.services.deliverable_service import (
        VALID_DOC_TYPES,
        VALID_FORMATS,
        export_deliverable_bpmn,
        export_deliverable_xlsx,
        render_deliverable_html,
        render_deliverable_pdf,
    )

    fmt = (format or "html").strip().lower()
    if doc_type not in VALID_DOC_TYPES:
        raise ValidationError(f"Unknown deliverable '{doc_type}'. Choose one of {VALID_DOC_TYPES}.")
    if fmt not in VALID_FORMATS:
        raise ValidationError(f"Unsupported format '{format}'. Choose one of {VALID_FORMATS}.")

    if fmt == "html":
        html = render_deliverable_html(project_id, doc_type)
        return HTMLResponse(content=html)
    if fmt == "pdf":
        pdf_bytes = render_deliverable_pdf(project_id, doc_type)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="PMGA_{doc_type}_{project_id[:8]}.pdf"'},
        )
    if fmt == "xlsx":
        out_path = export_deliverable_xlsx(project_id)
        return FileResponse(
            out_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(out_path),
        )
    filename, xml = export_deliverable_bpmn(project_id, process_id=process_id)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Static Assets
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def get_landing():
    return FileResponse("static/landing.html")


@app.get("/studio")
def get_studio():
    return FileResponse("static/index.html")


@app.get("/research")
def get_research():
    target = "static/research.html" if os.path.exists("static/research.html") else "static/index.html"
    return FileResponse(target)

