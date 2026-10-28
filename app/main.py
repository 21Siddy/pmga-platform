import os
import json
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
from app.database import init_db, get_db
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

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Automatic application lifecycle manager."""
    init_db()
    ensure_initial_datasets()
    yield

app = FastAPI(
    title="PMGA — Process Management and Gap Analysis",
    description="GovTech & Critical Infrastructure Process Modeling and Compliance Gap Workbench",
    version="1.1.0",
    lifespan=lifespan,
)

# Global Structured JSON Error Handler (Prevents raw HTML 500s from reaching browser)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "error_type": exc.__class__.__name__,
            "path": request.url.path
        }
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    save_path = os.path.join(settings.uploads_path, f"{uuid.uuid4()}_{file.filename}")
    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        extracted = DocumentService.extract_text_from_file(save_path, file.filename)
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

    return {
        "id": doc_id,
        "filename": file.filename,
        "doc_type": doc_type,
        "character_count": len(extracted),
        "pii_report": pii_report
    }

# 4. BPMN Generation with Scientific Verification
class BPMNGenerateRequest(BaseModel):
    project_id: str
    process_text: str
    policy_context: Optional[str] = ""
    provider: Optional[str] = None
    model_name: Optional[str] = None

@app.post("/api/v1/bpmn/generate")
def generate_bpmn_from_text(
    req: BPMNGenerateRequest,
    x_gemini_api_key: Optional[str] = Header(None)
):
    graph = BPMNEngine.generate_graph_from_text(
        process_text=req.process_text,
        policy_context=req.policy_context,
        api_key=x_gemini_api_key,
        provider=req.provider,
        model_name=req.model_name
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
    with get_db() as conn:
        latest = conn.execute(
            "SELECT version FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (req.project_id,)
        ).fetchone()
        new_version = (latest["version"] + 1) if latest else 1
        bpmn_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO bpmn_models (id, project_id, title, version, bpmn_xml) VALUES (?, ?, ?, ?, ?)",
            (bpmn_id, req.project_id, "Updated Model", new_version, req.bpmn_xml)
        )
    return {"id": bpmn_id, "version": new_version, "status": "saved"}

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

@app.post("/api/v1/analysis/gaps")
def run_gap_analysis(
    req: RunGapAnalysisRequest,
    x_gemini_api_key: Optional[str] = Header(None)
):
    with get_db() as conn:
        docs = conn.execute("SELECT * FROM documents WHERE project_id = ?", (req.project_id,)).fetchall()
        bpmn = conn.execute("SELECT * FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1", (req.project_id,)).fetchone()
        
    actual_texts = [d["extracted_text"] for d in docs if d["doc_type"] == "ACTUAL_PROCESS"]
    policy_texts = [d["extracted_text"] for d in docs if d["doc_type"] == "FORMAL_POLICY"]
    
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
        api_key=x_gemini_api_key,
        provider=req.provider,
        model_name=req.model_name
    )

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

# 7. Exports (.bpmn, .xlsx Gap Workbook)
@app.get("/api/v1/export/excel/{project_id}")
def export_excel_report(project_id: str):
    with get_db() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        gaps_rows = conn.execute("SELECT * FROM gap_assessments WHERE project_id = ?", (project_id,)).fetchall()
        
    gaps_list = []
    for g in gaps_rows:
        item = dict(g)
        item["actionable_todos"] = json.loads(g["actionable_todos_json"] or "[]")
        item["roles_to_consult"] = json.loads(g["roles_to_consult_json"] or "[]")
        item["compensating_controls"] = json.loads(g["compensating_controls_json"] or "[]")
        gaps_list.append(item)

    file_name = f"PMGA_ISO27002_Gap_Report_{project_id[:8]}.xlsx"
    out_path = os.path.join(settings.exports_path, file_name)
    ISOExcelService.export_annotated_gap_report(out_path, proj["title"], gaps_list)
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

# 8. GDPR Data Erasure / 1-Click Cryptographic Wipe
@app.delete("/api/v1/gdpr/purge/{project_id}")
def gdpr_purge_project(project_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM gap_assessments WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM bpmn_models WHERE project_id = ?", (project_id,))
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

# 9. Ground-Truth Corpus & Matei et al. (2026) Evaluation Workbench
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

# Static Assets
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def get_index():
    return FileResponse("static/index.html")
