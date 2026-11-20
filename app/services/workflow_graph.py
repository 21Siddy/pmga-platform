"""LangGraph 7-node multi-session discovery & remediation orchestrator.

Backed by SqliteSaver (`storage/pmga_checkpoints.db`) keyed by `thread_id = project_id`.
Supports `interrupt()` / `Command(resume=...)` across browser reloads, server restarts,
and multi-day engagements across all 4 entry points:
- chat turns
- multi-sheet Excel asset/process dumps
- policies / handbooks / AGBs / SBOMs (negation-safe)
- uploaded or canvas-edited `.bpmn` diagrams

Security invariant: BYOK API keys travel strictly inside a RAM-only `ContextVar`
(`_RUNTIME_LLM_VAR`) at runtime and are NEVER written into `PMGAState`, `RunnableConfig`,
or SQLite checkpoints (`pmga_checkpoints.db`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db
from app.errors import BPMNParseError, ValidationError, WorkflowExecutionError
from app.logging_config import log_context
from app.services.bpmn_ir_service import (
    BPMNDataObject,
    BPMNFlow,
    BPMNProcessIR,
    BPMNStep,
    apply_ir_patch,
    compile_ir_to_bpmn_xml,
    describe_bpmn_for_user,
    parse_bpmn_xml_to_ir,
)
from app.services.bpmn_verifier import BPMNVerifier
from app.services.decision_engine import DecisionEngine
from app.services.document_service import DocumentService
from app.services.entity_service import EntityService, _parse_cia_and_reason, _parse_rto_mtpd
from app.services.intake_extractor import (
    extract_handbook_evidence,
    map_excel_sheets_to_survey_items,
    record_evidence_item,
)
from app.services.llm_client import LLMClient, ProviderError
from app.services.survey_service import APP_KEYWORDS, SurveyService
from app.services.workspace_service import (
    get_project_or_404,
    get_workspace_state,
    recompute_gaps_and_agenda,
    upsert_process_model,
)

logger = logging.getLogger(__name__)

_LOCKS_MUTEX = threading.Lock()
_PROJECT_LOCKS: Dict[str, threading.Lock] = {}


def _get_project_lock(project_id: str) -> threading.Lock:
    with _LOCKS_MUTEX:
        lock = _PROJECT_LOCKS.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[project_id] = lock
        return lock


# ---------------------------------------------------------------------------
# LangGraph Persistent State & Structured LLM Patch Models
# ---------------------------------------------------------------------------

class PMGAState(TypedDict):
    project_id: str
    language: Literal["en", "de"]
    messages: Annotated[list, add_messages]
    active_process_id: Optional[str]
    latest_input_kind: Literal["chat", "excel", "document", "bpmn_edit"]
    latest_input_payload: Dict[str, Any]
    extracted_patch: Dict[str, Any]
    dmn_results: Dict[str, Any]
    agenda_queue: List[Dict[str, Any]]
    coach_reply: Dict[str, Any]


class ExtractedSystemLink(BaseModel):
    system_name: str
    process_name: str
    owner_role: Optional[str] = None


class ExtractedStepSpec(BaseModel):
    name: str
    lane: str = "Department Staff"
    type: Literal["userTask", "serviceTask", "exclusiveGateway", "parallelGateway"] = "userTask"
    system_name: Optional[str] = None


class ExtractedProcessSpec(BaseModel):
    name: str
    owner_role: Optional[str] = None
    trigger: Optional[str] = None
    steps: List[ExtractedStepSpec] = Field(default_factory=list)
    data_in: List[str] = Field(default_factory=list)
    data_out: List[str] = Field(default_factory=list)
    exception_path: Optional[str] = None


class ChatGraphPatch(BaseModel):
    processes: List[ExtractedProcessSpec] = Field(default_factory=list)
    system_links: List[ExtractedSystemLink] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    data_assets: List[str] = Field(default_factory=list)
    confidentiality: Optional[Literal["Normal", "High", "Very High"]] = None
    integrity: Optional[Literal["Normal", "High", "Very High"]] = None
    availability: Optional[Literal["Normal", "High", "Very High"]] = None
    cia_reason: Optional[str] = None
    rto_hours: Optional[float] = None
    mtpd_hours: Optional[float] = None
    confirm_doc_claims: Optional[bool] = None
    contradict_doc_claims: Optional[bool] = None
    contradiction_note: Optional[str] = None


class DocumentLLMClaim(BaseModel):
    req_id: str
    claim_text: str
    source_quote: str
    negated_or_missing: bool = False


class DocumentExtractionPatch(BaseModel):
    process_names: List[str] = Field(default_factory=list)
    system_names: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    claims: List[DocumentLLMClaim] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers: Runtime Config (RAM-only BYOK ContextVar) & Deterministic Parsers
# ---------------------------------------------------------------------------

_RUNTIME_LLM_VAR: ContextVar[Optional[Dict[str, Optional[str]]]] = ContextVar("_RUNTIME_LLM_VAR", default=None)


def _runtime_llm_opts(config: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    ctx_opts = _RUNTIME_LLM_VAR.get()
    if ctx_opts:
        return dict(ctx_opts)
    cfg = (config or {}).get("configurable") or {}
    return {
        "api_key": cfg.get("api_key"),
        "provider": cfg.get("provider"),
        "model_name": cfg.get("model_name"),
        "base_url": cfg.get("base_url"),
    }


def _has_usable_llm(opts: Dict[str, Optional[str]]) -> bool:
    try:
        LLMClient.resolve(opts.get("provider"), opts.get("api_key"), opts.get("model_name"), opts.get("base_url"))
        return True
    except ProviderError:
        return False


def _deterministic_extract_chat_patch(
    message: str,
    project_id: str,
    agenda_queue: List[Dict[str, Any]],
    active_process_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract structured graph updates from natural chat text without requiring an LLM."""
    patch: Dict[str, Any] = {
        "processes": [],
        "system_links": [],
        "roles": [],
        "data_assets": [],
        "exception_notes": [],
        "cia": {},
        "rto_hours": None,
        "mtpd_hours": None,
        "confirm_doc_claims": None,
        "contradiction_note": None,
    }
    text = (message or "").strip()
    if not text:
        return patch

    structure = EntityService.get_project_structure(project_id)
    known_systems = [e["name"] for e in structure["entities"] if e["entity_type"] == "SYSTEM"]
    known_procs = [e["name"] for e in structure["entities"] if e["entity_type"] == "PROCESS"]

    # 1. Explicit CIA / BIA parsing
    for dim, pat in (
        ("confidentiality", r"(?:confidentiality|vertraulichkeit|conf)\s*[:=]\s*(very\s+high|sehr\s+hoch|high|hoch|normal)"),
        ("integrity", r"(?:integrity|integrität|integ)\s*[:=]\s*(very\s+high|sehr\s+hoch|high|hoch|normal)"),
        ("availability", r"(?:availability|verfügbarkeit|avail)\s*[:=]\s*(very\s+high|sehr\s+hoch|high|hoch|normal)"),
    ):
        m = re.search(pat, text, re.I)
        if m:
            lvl, _ = _parse_cia_and_reason(m.group(1))
            if lvl:
                patch["cia"][dim] = lvl

    if not patch["cia"] and re.search(r"\b(high\s+cia|sehr\s+hoch|very\s+high)\b", text, re.I):
        lvl = "Very High" if re.search(r"very\s+high|sehr\s+hoch", text, re.I) else "High"
        patch["cia"] = {"confidentiality": lvl, "integrity": lvl, "availability": lvl}

    rto_val, mtpd_val = _parse_rto_mtpd(text)
    if rto_val is not None:
        patch["rto_hours"] = rto_val
    if mtpd_val is not None:
        patch["mtpd_hours"] = mtpd_val

    # 2. Explicit owner/role & data cues
    for rm in re.finditer(r"(?:owner|verantwortlich|zuständig|role|team|department|abteilung)\s*[:=]\s*([^\n.;,]{2,45})", text, re.I):
        role = rm.group(1).strip()
        if role not in patch["roles"]:
            patch["roles"].append(role)

    for dm in re.finditer(r"(?:data|daten|input|output|dokument|record|akte)\s*[:=]\s*([^\n.;]{2,80})", text, re.I):
        for part in re.split(r"[,;/]", dm.group(1)):
            clean_d = part.strip()
            if clean_d and clean_d not in patch["data_assets"]:
                patch["data_assets"].append(clean_d)

    # 3. Document claim confirmation vs contradiction
    low = text.lower()
    if any(k in low for k in ("confirmed in daily", "yes, staff actually", "bestätigt", "confirmed")):
        patch["confirm_doc_claims"] = True
    elif any(k in low for k in ("not enforced in reality", "not happening", "nicht umgesetzt", "stimmt nicht", "contradicted", "no mfa", "without mfa")):
        patch["confirm_doc_claims"] = False
        patch["contradiction_note"] = text[:240]

    # 4. System-to-process disambiguation clauses:
    link_patterns = [
        re.compile(r"([A-Za-zÄÖÜäöüß0-9_\-\s]{2,40}?)\s+(?:supports|used\s+(?:by|for)|belongs\s+to|für|unterstützt)\s+([^\n.;]+)", re.I),
    ]
    for clause in re.split(r"[;\n]+", text):
        c = clause.strip()
        if not c:
            continue
        matched_link = False
        for pat in link_patterns:
            lm = pat.search(c)
            if lm:
                left = lm.group(1).strip()
                right_raw = lm.group(2).strip()
                sys_match = next((s for s in known_systems if s.lower() in left.lower()), None)
                if sys_match or any(k in left.lower() for k in APP_KEYWORDS):
                    sys_name = sys_match or left
                    owner_m = re.search(r"\(([^)]+)\)", right_raw)
                    owner_hint = None
                    if owner_m:
                        inner = owner_m.group(1)
                        first_part = inner.split(",")[0].strip()
                        if first_part and not re.search(r"\b(rto|mtpd|cia|high|normal)\b", first_part, re.I):
                            owner_hint = first_part
                    proc_clean = re.sub(r"\([^)]*\)", "", right_raw).strip()
                    for p_part in re.split(r"\s+(?:and|und)\s+|,", proc_clean):
                        p_name = p_part.strip(" .")
                        if p_name and len(p_name) >= 3:
                            patch["system_links"].append({
                                "system_name": sys_name,
                                "process_name": p_name,
                                "owner_role": owner_hint,
                            })
                            matched_link = True
                if matched_link:
                    break

    top_agenda = agenda_queue[0] if agenda_queue else None
    if not patch["system_links"] and top_agenda and top_agenda.get("agenda_key") == "LINK_SYSTEM_TO_PROCESS":
        target_sys = top_agenda.get("target_name")
        if target_sys and target_sys in known_systems and not re.search(r"->", text) and len(text) <= 160:
            for p_part in re.split(r"[,;\n]|\s+(?:and|und)\s+", text):
                p_clean = re.sub(r"\([^)]*\)", "", p_part).strip(" .")
                if p_clean and len(p_clean) >= 3 and p_clean.lower() != target_sys.lower():
                    patch["system_links"].append({
                        "system_name": target_sys,
                        "process_name": p_clean,
                        "owner_role": patch["roles"][0] if patch["roles"] else None,
                    })

    # 5. Exception branch cues
    if re.search(r"\b(if\s+.*missing|if\s+.*rejected|if\s+.*down|exception|fallback|wenn\s+.*fehlt|ausnahme|rückfrage|loop\s+back|paper\s+form)\b", text, re.I):
        exc_m = re.search(r"(?:if|wenn|exception|fallback)\s*[:,-]?\s*([^\n.]+)", text, re.I)
        if exc_m:
            patch["exception_notes"].append(exc_m.group(0).strip()[:100])

    # 6. Process workflow steps from arrow chains or multi-sentence operational notes
    actions = BPMNVerifier.extract_atomic_actions(text)
    if not actions and len(text) >= 15 and not patch["system_links"] and not patch["cia"]:
        actions = [s.strip() for s in re.split(r"[\.\n]+", text) if len(s.strip()) >= 10][:6]

    mentioned_proc = next((p for p in known_procs if p.lower() in low), None)
    target_proc_name = mentioned_proc or active_process_name or (known_procs[0] if known_procs else None)

    is_pure_meta = bool(patch["cia"] or patch["rto_hours"] is not None or patch["confirm_doc_claims"] is not None) and "->" not in text and len(actions) <= 1 and not patch["exception_notes"]
    if (actions or patch["exception_notes"]) and not patch["system_links"] and not is_pure_meta:
        title_prefix_m = re.match(r"^\s*([A-Za-zÄÖÜäöüß0-9\s\-/]{3,42}):\s*(.+)", text)
        prefix_cand = title_prefix_m.group(1).strip() if title_prefix_m else ""
        if prefix_cand.lower() in {"owner", "role", "team", "data", "step", "confidentiality", "integrity", "availability", "rto", "mtpd", "for"}:
            prefix_cand = ""
        proc_name = target_proc_name or prefix_cand or (actions[0][:48] if len(actions) == 1 else "Citizen Service Workflow")
        steps: List[Dict[str, Any]] = []
        default_lane = patch["roles"][0] if patch["roles"] else "Department Staff"
        for idx, act in enumerate(actions[:8]):
            lane = default_lane
            step_name = act
            if ":" in act and not act.lower().startswith("http"):
                pfx, rem = act.split(":", 1)
                if idx == 0 and prefix_cand and pfx.strip().lower() == prefix_cand.lower() and len(rem.strip()) >= 3:
                    step_name = rem.strip()
                elif 2 <= len(pfx.strip()) <= 28 and len(rem.strip()) >= 3:
                    lane = pfx.strip().title()
                    step_name = rem.strip()
                    if lane not in patch["roles"]:
                        patch["roles"].append(lane)
            used_sys = next((s for s in known_systems if s.lower() in step_name.lower()), None)
            steps.append({
                "name": step_name[:60],
                "lane": lane,
                "type": "serviceTask" if used_sys else "userTask",
                "system_name": used_sys,
            })
        patch["processes"].append({
            "name": proc_name,
            "owner_role": patch["roles"][0] if patch["roles"] else None,
            "steps": steps,
            "data_in": patch["data_assets"][:],
            "exception_path": patch["exception_notes"][0] if patch["exception_notes"] else None,
        })

    return patch


# ---------------------------------------------------------------------------
# 7 LangGraph Nodes + Interrupt Wait Node
# ---------------------------------------------------------------------------

def node_ingest_and_redact(state: PMGAState) -> Dict[str, Any]:
    started = time.perf_counter()
    project_id = state["project_id"]
    kind = state.get("latest_input_kind", "chat")
    payload = dict(state.get("latest_input_payload") or {})

    redacted_payload = dict(payload)
    user_turn_text = ""

    if kind == "chat":
        raw_msg = str(payload.get("message") or "").strip()
        pii = DocumentService.scan_for_pii(raw_msg)
        redacted_payload["redacted_message"] = pii["redacted_text"]
        redacted_payload["has_pii"] = pii["has_pii"]
        user_turn_text = pii["redacted_text"] or raw_msg

    elif kind == "excel":
        filename = os.path.basename(payload.get("filename") or "workbook.xlsx")
        file_path = payload.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise ValidationError(f"Uploaded Excel file '{filename}' could not be located on disk.")
        extracted = DocumentService.extract_text_from_file(file_path, filename)
        pii = DocumentService.scan_for_pii(extracted)
        sheets = DocumentService.extract_excel_sheets(file_path)
        doc_id = payload.get("document_id") or str(uuid.uuid4())
        with get_db() as conn:
            existing_doc = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not existing_doc:
                conn.execute(
                    """INSERT INTO documents
                       (id, project_id, filename, file_path, doc_type, extracted_text, redacted_text, pii_detected_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, project_id, filename, file_path, payload.get("doc_type") or "ACTUAL_PROCESS", extracted, pii["redacted_text"], json.dumps(pii["detections"])),
                )
        redacted_payload["document_id"] = doc_id
        redacted_payload["sheets"] = sheets
        redacted_payload["redacted_text"] = pii["redacted_text"]
        user_turn_text = f"Uploaded Excel workbook '{filename}' ({len(sheets)} sheet(s))."

    elif kind == "document":
        filename = os.path.basename(payload.get("filename") or "document.txt")
        doc_type = payload.get("doc_type") or "FORMAL_POLICY"
        extracted = payload.get("extracted_text") or ""
        file_path = payload.get("file_path")
        if not extracted and file_path and os.path.exists(file_path):
            extracted = DocumentService.extract_text_from_file(file_path, filename)
        if not extracted.strip():
            raise ValidationError(f"Uploaded document '{filename}' contained no readable text.")
        pii = DocumentService.scan_for_pii(extracted)
        doc_id = payload.get("document_id") or str(uuid.uuid4())
        with get_db() as conn:
            existing_doc = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not existing_doc:
                conn.execute(
                    """INSERT INTO documents
                       (id, project_id, filename, file_path, doc_type, extracted_text, redacted_text, pii_detected_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, project_id, filename, file_path, doc_type, extracted, pii["redacted_text"], json.dumps(pii["detections"])),
                )
        redacted_payload["document_id"] = doc_id
        redacted_payload["extracted_text"] = extracted
        redacted_payload["redacted_text"] = pii["redacted_text"]
        user_turn_text = f"Uploaded {doc_type.lower().replace('_', ' ')} '{filename}' ({len(extracted)} chars)."

    elif kind == "bpmn_edit":
        xml_text = str(payload.get("bpmn_xml") or "").strip()
        if not xml_text:
            raise ValidationError("bpmn_edit turn requires non-empty 'bpmn_xml'.")
        # Validate XML cleanly before advancing
        parse_bpmn_xml_to_ir(xml_text, fallback_title=payload.get("title") or "Uploaded BPMN Process")
        redacted_payload["bpmn_xml"] = xml_text
        user_turn_text = f"Updated/uploaded BPMN diagram '{payload.get('title') or payload.get('filename') or 'Process Model'}'."

    else:
        raise ValidationError(f"Unsupported input_kind '{kind}'.")

    # Persist user turn
    with get_db() as conn:
        conn.execute(
            """INSERT INTO session_turns (id, project_id, thread_id, role, content, input_kind, patch_summary_json)
               VALUES (?, ?, ?, 'user', ?, ?, '{}')""",
            (str(uuid.uuid4()), project_id, project_id, user_turn_text, kind),
        )

    logger.info("Node ingest_and_redact complete kind=%s duration_ms=%.2f", kind, (time.perf_counter() - started) * 1000)
    return {
        "latest_input_payload": redacted_payload,
        "messages": [{"role": "user", "content": user_turn_text}],
    }


def node_extract_graph_patch(state: PMGAState, config: RunnableConfig) -> Dict[str, Any]:
    started = time.perf_counter()
    project_id = state["project_id"]
    kind = state["latest_input_kind"]
    payload = state["latest_input_payload"]
    opts = _runtime_llm_opts(config)
    use_llm = _has_usable_llm(opts)

    patch: Dict[str, Any] = {"kind": kind, "llm_used": False}

    if kind == "excel":
        sheets = payload.get("sheets") or []
        items = map_excel_sheets_to_survey_items(sheets)
        classified_items: List[Dict[str, Any]] = []
        for it in items:
            raw_entry = it["raw_entry"]
            low = raw_entry.lower()
            has_app_kw = any(k in low for k in APP_KEYWORDS)
            has_verb = bool(re.search(r"\b(erfassen|prüfen|freigeben|genehmigen|versenden|archivieren|bearbeiten|buchen|check|process|approve|send|archive|verify|beantragen|ausstellen|einlösen|abrechnen)\b", low))
            wc = len([w for w in low.split() if w])
            dmn_res = DecisionEngine.evaluate(
                "01_entry_classifier",
                {
                    "explicit_kind_col": it.get("raw_kind_label") or (it.get("entry_kind") or ""),
                    "has_app_keyword": has_app_kw,
                    "has_action_verb": has_verb,
                    "word_count": wc,
                    "raw_criticality": it.get("raw_criticality") or "",
                },
            )[0]
            classified_items.append({**it, "dmn_classification": dmn_res})
        patch["excel_items"] = classified_items

    elif kind == "document":
        filename = payload.get("filename") or "document.txt"
        doc_type = payload.get("doc_type") or "FORMAL_POLICY"
        doc_id = payload.get("document_id")
        raw_text = payload.get("extracted_text") or ""
        ev_summary = extract_handbook_evidence(
            project_id,
            raw_text,
            filename,
            doc_type=doc_type,
            document_id=doc_id,
        )
        patch["evidence_summary"] = ev_summary

        if use_llm:
            safe_text = (payload.get("redacted_text") or "")[:10000]
            try:
                llm_doc = LLMClient.generate_structured(
                    prompt=(
                        f"Extract entities and security/process claims from this {doc_type} document ({filename}). "
                        f"CRITICAL RULE: If the text states a safeguard is NOT enforced, missing, bypassed, or disabled, set negated_or_missing=true. "
                        f"Always copy an exact short verbatim quote.\n\n{safe_text}"
                    ),
                    response_model=DocumentExtractionPatch,
                    system_instruction="You extract verified facts and negations with exact quotes. Never mark negated rules as compliant.",
                    **opts,
                )
                patch["llm_doc_patch"] = llm_doc.model_dump()
                patch["llm_used"] = True
            except Exception as exc:
                logger.warning("LLM document extraction failed, keeping deterministic evidence: %s", exc)

    elif kind == "bpmn_edit":
        xml_text = payload["bpmn_xml"]
        title = payload.get("title") or payload.get("filename") or "Uploaded BPMN Process"
        ir = parse_bpmn_xml_to_ir(xml_text, fallback_title=title)
        desc = describe_bpmn_for_user(ir, **opts)
        patch["bpmn_ir"] = ir.model_dump()
        patch["bpmn_description"] = desc
        patch["process_model_id"] = payload.get("process_id") or state.get("active_process_id")
        patch["llm_used"] = bool(desc.get("llm_enhanced"))

    elif kind == "chat":
        redacted_msg = payload.get("redacted_message") or payload.get("message") or ""
        active_pname = None
        if state.get("active_process_id"):
            with get_db() as conn:
                pm_r = conn.execute("SELECT title FROM process_models WHERE id = ? AND project_id = ?", (state["active_process_id"], project_id)).fetchone()
                if pm_r:
                    active_pname = pm_r["title"]
        det_patch = _deterministic_extract_chat_patch(
            redacted_msg,
            project_id,
            state.get("agenda_queue") or [],
            active_process_name=active_pname,
        )
        patch["chat_patch"] = det_patch

        if use_llm:
            structure = EntityService.get_project_structure(project_id)
            entity_summary = ", ".join(f"{e['entity_type']}:{e['name']}" for e in structure["entities"][:25]) or "None yet"
            top_goal = (state.get("agenda_queue") or [{}])[0].get("goal") or "Initial process discovery"
            try:
                llm_chat = LLMClient.generate_structured(
                    prompt=(
                        f"Current project entities: {entity_summary}\n"
                        f"Current discovery goal: {top_goal}\n"
                        f"User turn:\n{redacted_msg}\n\n"
                        "Extract only facts explicitly stated in this user turn: new/updated processes with steps, "
                        "system->process links, roles, data assets, CIA levels, RTO/MTPD hours, or whether document claims were confirmed/contradicted."
                    ),
                    response_model=ChatGraphPatch,
                    system_instruction="Extract structured process/security updates strictly grounded in the user's message. Never invent unstated facts.",
                    **opts,
                )
                patch["llm_chat_patch"] = llm_chat.model_dump()
                patch["llm_used"] = True
            except Exception as exc:
                logger.warning("LLM chat patch extraction failed, falling back to deterministic patch: %s", exc)

    logger.info("Node extract_graph_patch complete kind=%s llm_used=%s duration_ms=%.2f", kind, patch["llm_used"], (time.perf_counter() - started) * 1000)
    return {"extracted_patch": patch}


def node_apply_and_verify_graph(state: PMGAState) -> Dict[str, Any]:
    started = time.perf_counter()
    project_id = state["project_id"]
    patch = state.get("extracted_patch") or {}
    kind = patch.get("kind") or state["latest_input_kind"]
    changes: List[str] = []
    active_proc_id = state.get("active_process_id")

    if kind == "excel":
        items = patch.get("excel_items") or []
        if items:
            SurveyService.ingest_rows_into_survey(project_id, items)
            sys_cnt = 0
            proc_cnt = 0
            for it in items:
                cls = it["dmn_classification"]
                etype = cls["entity_type"]
                if etype == "SYSTEM":
                    sys_ent = EntityService.upsert_entity(
                        project_id,
                        "SYSTEM",
                        it["raw_entry"],
                        owner_role=it.get("owner_role"),
                    )
                    sys_cnt += 1
                    if it.get("process_name"):
                        p_ent = EntityService.upsert_entity(project_id, "PROCESS", it["process_name"], owner_role=it.get("owner_role"))
                        EntityService.upsert_dependency(project_id, sys_ent["id"], p_ent["id"], "SUPPORTS")
                        proc_cnt += 1
                        ir = _build_initial_ir_for_process(it["process_name"], owner=it.get("owner_role"), system_name=it["raw_entry"])
                        pm = upsert_process_model(project_id, title=it["process_name"], ir=ir, bpmn_xml=compile_ir_to_bpmn_xml(ir), entity_id=p_ent["id"])
                        active_proc_id = active_proc_id or pm["id"]
                else:
                    p_name = it.get("process_name") or it["raw_entry"]
                    p_ent = EntityService.upsert_entity(project_id, etype, p_name, owner_role=it.get("owner_role"))
                    proc_cnt += 1
                    if etype == "PROCESS":
                        ir = _build_initial_ir_for_process(p_name, owner=it.get("owner_role"), trigger=it.get("trigger_event"))
                        pm = upsert_process_model(project_id, title=p_name, ir=ir, bpmn_xml=compile_ir_to_bpmn_xml(ir), entity_id=p_ent["id"])
                        active_proc_id = active_proc_id or pm["id"]
            if sys_cnt:
                changes.append(f"+{sys_cnt} System(s)")
            if proc_cnt:
                changes.append(f"+{proc_cnt} Process(es)")

    elif kind == "document":
        ev_sum = patch.get("evidence_summary") or {}
        doc_id = (state.get("latest_input_payload") or {}).get("document_id")
        llm_doc = patch.get("llm_doc_patch")
        extra_claims = 0
        if llm_doc:
            for pname in llm_doc.get("process_names", []):
                if pname.strip():
                    EntityService.upsert_entity(project_id, "PROCESS", pname.strip())
            for sname in llm_doc.get("system_names", []):
                if sname.strip():
                    EntityService.upsert_entity(project_id, "SYSTEM", sname.strip())
            for rname in llm_doc.get("roles", []):
                if rname.strip():
                    EntityService.upsert_entity(project_id, "ROLE", rname.strip())
            anchor_id = ev_sum.get("anchor_entity_id")
            for cl in llm_doc.get("claims", []):
                status = "CONTRADICTED" if cl.get("negated_or_missing") else "DOCUMENT_CLAIM"
                record_evidence_item(
                    project_id,
                    entity_id=anchor_id,
                    document_id=doc_id,
                    req_id=cl.get("req_id"),
                    claim_text=cl.get("claim_text") or "Extracted document statement",
                    source_quote=cl.get("source_quote") or cl.get("claim_text") or "",
                    source_location=(state.get("latest_input_payload") or {}).get("filename") or "Uploaded document",
                    status=status,
                )
                extra_claims += 1
        proven = ev_sum.get("assessments_proven", 0) + extra_claims
        contras = len(ev_sum.get("contradictions", []))
        if proven:
            changes.append(f"+{proven} Evidence item(s)")
        if contras:
            changes.append(f"Flagged {contras} contradiction(s)")

    elif kind == "bpmn_edit":
        ir = BPMNProcessIR.model_validate(patch["bpmn_ir"])
        desc = patch["bpmn_description"]
        proc_ent = EntityService.upsert_entity(
            project_id,
            "PROCESS",
            ir.process_title,
            description=desc.get("plain_english_description") or None,
        )
        for actor in desc.get("actors", []):
            clean_a = actor.replace(" (External)", "").strip()
            if clean_a and clean_a.lower() not in ("general process", "default lane"):
                r_ent = EntityService.upsert_entity(project_id, "ROLE", clean_a)
                EntityService.upsert_dependency(project_id, proc_ent["id"], r_ent["id"], "EXECUTED_BY")
        for sname in desc.get("systems", []):
            s_ent = EntityService.upsert_entity(project_id, "SYSTEM", sname)
            EntityService.upsert_dependency(project_id, s_ent["id"], proc_ent["id"], "SUPPORTS")
        for d in ir.data_objects:
            d_ent = EntityService.upsert_entity(project_id, "DATA_ASSET", d.name)
            EntityService.upsert_dependency(project_id, proc_ent["id"], d_ent["id"], "USES_DATA")

        pm = upsert_process_model(
            project_id,
            title=ir.process_title,
            ir=ir,
            bpmn_xml=compile_ir_to_bpmn_xml(ir),
            entity_id=proc_ent["id"],
            description_text=desc.get("plain_english_description"),
            process_model_id=patch.get("process_model_id"),
        )
        active_proc_id = pm["id"]
        changes.append(f"Synced BPMN '{pm['title']}' v{pm['version']}")

    elif kind == "chat":
        det = patch.get("chat_patch") or {}
        llm_c = patch.get("llm_chat_patch") or {}

        # Merge deterministic + LLM structured patch cleanly
        sys_links = llm_c.get("system_links") or det.get("system_links") or []
        processes_spec = llm_c.get("processes") or det.get("processes") or []
        roles = list(dict.fromkeys((llm_c.get("roles") or []) + (det.get("roles") or [])))
        data_assets = list(dict.fromkeys((llm_c.get("data_assets") or []) + (det.get("data_assets") or [])))

        for rname in roles:
            EntityService.upsert_entity(project_id, "ROLE", rname)

        # 1. System -> Process links (creating one separate process_model per process!)
        linked_cnt = 0
        for lk in sys_links:
            sname = (lk.get("system_name") or "").strip()
            pname = (lk.get("process_name") or "").strip()
            orole = (lk.get("owner_role") or "").strip() or (roles[0] if roles else None)
            if not sname or not pname:
                continue
            sys_ent = EntityService.upsert_entity(project_id, "SYSTEM", sname, owner_role=orole)
            proc_ent = EntityService.upsert_entity(project_id, "PROCESS", pname, owner_role=orole)
            EntityService.upsert_dependency(project_id, sys_ent["id"], proc_ent["id"], "SUPPORTS")
            EntityService.save_entity_assessment(project_id, sys_ent["id"], "R002", answer_value=pname, context={"links": 1}, answered_by="coach_turn")
            if orole:
                role_ent = EntityService.upsert_entity(project_id, "ROLE", orole)
                EntityService.upsert_dependency(project_id, proc_ent["id"], role_ent["id"], "EXECUTED_BY")
                EntityService.save_entity_assessment(project_id, proc_ent["id"], "R003", answer_value=orole, context={"owner_role": orole}, answered_by="coach_turn")
            # Ensure a dedicated BPMN process_model exists for this process
            with get_db() as conn:
                existing_pm = conn.execute(
                    "SELECT * FROM process_models WHERE project_id = ? AND (entity_id = ? OR lower(title) = lower(?))",
                    (project_id, proc_ent["id"], pname),
                ).fetchone()
            if existing_pm:
                ir = BPMNProcessIR.model_validate_json(existing_pm["ir_json"])
                first_task = next((s for s in ir.steps if s.type in ("userTask", "serviceTask")), None)
                if first_task and not first_task.system_name:
                    ir = apply_ir_patch(ir, [{"op": "attach_system", "step_id": first_task.id, "system_name": sname}])
                pm = upsert_process_model(project_id, title=pname, ir=ir, bpmn_xml=compile_ir_to_bpmn_xml(ir), entity_id=proc_ent["id"], process_model_id=existing_pm["id"])
            else:
                ir = _build_initial_ir_for_process(pname, owner=orole, system_name=sname)
                pm = upsert_process_model(project_id, title=pname, ir=ir, bpmn_xml=compile_ir_to_bpmn_xml(ir), entity_id=proc_ent["id"])
            active_proc_id = pm["id"]
            linked_cnt += 1
        if linked_cnt:
            changes.append(f"Linked {linked_cnt} system-process flow(s)")

        # 2. Extracted processes & steps
        for pspec in processes_spec:
            pname = (pspec.get("name") or "").strip()
            if not pname:
                continue
            orole = (pspec.get("owner_role") or "").strip() or (roles[0] if roles else None)
            proc_ent = EntityService.upsert_entity(project_id, "PROCESS", pname, owner_role=orole)
            if orole:
                role_ent = EntityService.upsert_entity(project_id, "ROLE", orole)
                EntityService.upsert_dependency(project_id, proc_ent["id"], role_ent["id"], "EXECUTED_BY")
                EntityService.save_entity_assessment(project_id, proc_ent["id"], "R003", answer_value=orole, context={"owner_role": orole}, answered_by="coach_turn")

            steps_spec = pspec.get("steps") or []
            with get_db() as conn:
                existing_pm = conn.execute(
                    "SELECT * FROM process_models WHERE project_id = ? AND (entity_id = ? OR lower(title) = lower(?))",
                    (project_id, proc_ent["id"], pname),
                ).fetchone()

            if existing_pm:
                ir = BPMNProcessIR.model_validate_json(existing_pm["ir_json"])
                # Patch new steps onto existing diagram without wiping prior steps if new steps aren't already there
                existing_names = {s.name.lower() for s in ir.steps}
                ops: List[Dict[str, Any]] = []
                last_non_end = next((s.id for s in reversed(ir.steps) if s.type != "endEvent"), None)
                for st in steps_spec:
                    st_name = (st.get("name") or "").strip()
                    if st_name and st_name.lower() not in existing_names:
                        ops.append({
                            "op": "add_step",
                            "after_step_id": last_non_end,
                            "step": {
                                "name": st_name,
                                "lane": st.get("lane") or orole or "Department Staff",
                                "type": st.get("type") or "userTask",
                                "system_name": st.get("system_name"),
                            },
                        })
                if pspec.get("exception_path") and not any(s.type == "exclusiveGateway" for s in ir.steps):
                    anchor = next((s.id for s in ir.steps if s.type in ("userTask", "serviceTask")), None)
                    if anchor:
                        ops.append({
                            "op": "add_exception_branch",
                            "after_step_id": anchor,
                            "exception_name": str(pspec["exception_path"])[:60],
                            "condition_label": "Exception / Missing",
                            "rejoin_step_id": anchor,
                        })
                if ops:
                    ir = apply_ir_patch(ir, ops)
                    pm = upsert_process_model(project_id, title=pname, ir=ir, bpmn_xml=compile_ir_to_bpmn_xml(ir), entity_id=proc_ent["id"], process_model_id=existing_pm["id"])
                    active_proc_id = pm["id"]
                    changes.append(f"Updated diagram '{pname}'")
            else:
                ir = _build_ir_from_steps(
                    title=pname,
                    trigger=pspec.get("trigger") or "Request Received",
                    steps_spec=steps_spec,
                    data_in=pspec.get("data_in") or data_assets,
                    data_out=pspec.get("data_out") or [],
                    exception_path=pspec.get("exception_path"),
                    default_lane=orole or "Department Staff",
                )
                pm = upsert_process_model(project_id, title=pname, ir=ir, bpmn_xml=compile_ir_to_bpmn_xml(ir), entity_id=proc_ent["id"])
                active_proc_id = pm["id"]
                changes.append(f"+Diagram '{pname}'")

        # 3. Data assets
        if data_assets:
            proc_ents = [e for e in EntityService.get_project_structure(project_id)["entities"] if e["entity_type"] == "PROCESS"]
            for dname in data_assets:
                d_ent = EntityService.upsert_entity(project_id, "DATA_ASSET", dname)
                if proc_ents:
                    EntityService.upsert_dependency(project_id, proc_ents[0]["id"], d_ent["id"], "USES_DATA")
            changes.append(f"+{len(data_assets)} Data Asset(s)")

        # 4. CIA & BIA ratings
        cia_updates = dict(det.get("cia") or {})
        for k in ("confidentiality", "integrity", "availability"):
            if llm_c.get(k):
                cia_updates[k] = llm_c[k]
        rto_h = llm_c.get("rto_hours") if llm_c.get("rto_hours") is not None else det.get("rto_hours")
        mtpd_h = llm_c.get("mtpd_hours") if llm_c.get("mtpd_hours") is not None else det.get("mtpd_hours")

        if cia_updates or rto_h is not None or mtpd_h is not None:
            struct = EntityService.get_project_structure(project_id)
            target_procs = [e for e in struct["entities"] if e["entity_type"] == "PROCESS"] or struct["entities"][:1]
            for pe in target_procs[:1]:
                reason = llm_c.get("cia_reason") or "Confirmed by department coordinator"
                if cia_updates.get("confidentiality"):
                    EntityService.save_entity_assessment(project_id, pe["id"], "R005", answer_value=cia_updates["confidentiality"], justification=reason, answered_by="coach_turn")
                if cia_updates.get("integrity"):
                    EntityService.save_entity_assessment(project_id, pe["id"], "R006", answer_value=cia_updates["integrity"], justification=reason, answered_by="coach_turn")
                if cia_updates.get("availability"):
                    EntityService.save_entity_assessment(project_id, pe["id"], "R007", answer_value=cia_updates["availability"], justification=reason, answered_by="coach_turn")
                if mtpd_h is not None:
                    EntityService.save_entity_assessment(project_id, pe["id"], "R017", answer_value=f"{mtpd_h:g} h", answer_numeric=float(mtpd_h), answered_by="coach_turn")
                if rto_h is not None:
                    EntityService.save_entity_assessment(
                        project_id, pe["id"], "R018",
                        answer_value=f"{rto_h:g} h", answer_numeric=float(rto_h),
                        context={"rto": float(rto_h), "mtpd": float(mtpd_h) if mtpd_h is not None else None},
                        answered_by="coach_turn",
                    )
            changes.append("Updated CIA/BIA targets")

        # 5. Confirm or contradict document claims
        confirm_flag = llm_c.get("confirm_doc_claims") if llm_c.get("confirm_doc_claims") is not None else det.get("confirm_doc_claims")
        if confirm_flag is True:
            with get_db() as conn:
                res = conn.execute(
                    "UPDATE evidence_items SET status = 'USER_CONFIRMED' WHERE project_id = ? AND status = 'DOCUMENT_CLAIM'",
                    (project_id,),
                )
                if res.rowcount:
                    changes.append(f"Confirmed {res.rowcount} policy claim(s)")
        elif confirm_flag is False:
            with get_db() as conn:
                res = conn.execute(
                    "UPDATE evidence_items SET status = 'CONTRADICTED' WHERE project_id = ? AND status = 'DOCUMENT_CLAIM'",
                    (project_id,),
                )
                if res.rowcount:
                    changes.append(f"Marked {res.rowcount} claim(s) as contradicted in practice")

    if not changes:
        changes.append("Recorded turn context")

    logger.info("Node apply_and_verify_graph complete changes=%s duration_ms=%.2f", changes, (time.perf_counter() - started) * 1000)
    return {
        "active_process_id": active_proc_id,
        "extracted_patch": {**patch, "what_changed_summary": changes},
    }


def _build_initial_ir_for_process(
    title: str,
    *,
    owner: Optional[str] = None,
    system_name: Optional[str] = None,
    trigger: Optional[str] = None,
) -> BPMNProcessIR:
    lane = owner or "Department Staff"
    steps = [
        BPMNStep(id="Start_1", name=trigger or f"{title} Requested", type="startEvent", lane=lane),
        BPMNStep(
            id="Task_1",
            name=f"Execute {title}"[:55],
            type="serviceTask" if system_name else "userTask",
            lane=lane,
            system_name=system_name,
        ),
        BPMNStep(id="End_1", name=f"{title} Completed"[:50], type="endEvent", lane=lane),
    ]
    flows = [
        BPMNFlow(id="Flow_1", sourceRef="Start_1", targetRef="Task_1"),
        BPMNFlow(id="Flow_2", sourceRef="Task_1", targetRef="End_1"),
    ]
    return BPMNProcessIR(process_title=title, lanes=[lane], steps=steps, flows=flows)


def _build_ir_from_steps(
    *,
    title: str,
    trigger: str,
    steps_spec: List[Dict[str, Any]],
    data_in: List[str],
    data_out: List[str],
    exception_path: Optional[str],
    default_lane: str,
) -> BPMNProcessIR:
    lanes: List[str] = []
    for st in steps_spec:
        l = st.get("lane") or default_lane
        if l not in lanes:
            lanes.append(l)
    if not lanes:
        lanes = [default_lane]

    steps: List[BPMNStep] = [BPMNStep(id="Start_1", name=trigger[:48], type="startEvent", lane=lanes[0])]
    flows: List[BPMNFlow] = []
    data_objects: List[BPMNDataObject] = []

    for idx_d, dname in enumerate(data_in[:4], start=1):
        data_objects.append(BPMNDataObject(id=f"DataObj_{idx_d}", name=dname, kind="dataObject", linked_step_ids=["Task_1"] if steps_spec else []))

    prev_id = "Start_1"
    for idx, st in enumerate(steps_spec, start=1):
        sid = f"Task_{idx}"
        steps.append(
            BPMNStep(
                id=sid,
                name=(st.get("name") or f"Step {idx}")[:55],
                type=st.get("type") or ("serviceTask" if st.get("system_name") else "userTask"),
                lane=st.get("lane") or lanes[0],
                system_name=st.get("system_name"),
                data_in=[d.id for d in data_objects] if idx == 1 else [],
            )
        )
        flows.append(BPMNFlow(id=f"Flow_{idx}", sourceRef=prev_id, targetRef=sid))
        prev_id = sid

    if not steps_spec:
        steps.append(BPMNStep(id="Task_1", name=title[:55], type="userTask", lane=lanes[0], data_in=[d.id for d in data_objects]))
        flows.append(BPMNFlow(id="Flow_1", sourceRef="Start_1", targetRef="Task_1"))
        prev_id = "Task_1"

    end_lane = steps[-1].lane
    steps.append(BPMNStep(id="End_1", name="Case Completed", type="endEvent", lane=end_lane))
    flows.append(BPMNFlow(id="Flow_End", sourceRef=prev_id, targetRef="End_1"))

    ir = BPMNProcessIR(process_title=title, lanes=lanes, steps=steps, flows=flows, data_objects=data_objects)
    if exception_path and len(steps) >= 3:
        anchor = next((s.id for s in ir.steps if s.type in ("userTask", "serviceTask")), None)
        if anchor:
            ir = apply_ir_patch(
                ir,
                [{
                    "op": "add_exception_branch",
                    "after_step_id": anchor,
                    "exception_name": exception_path[:55],
                    "condition_label": "Missing / Exception",
                    "rejoin_step_id": anchor,
                }],
            )
    return ir


def node_run_dmn_decision_models(state: PMGAState) -> Dict[str, Any]:
    started = time.perf_counter()
    project_id = state["project_id"]
    recompute = recompute_gaps_and_agenda(project_id)
    logger.info(
        "Node run_dmn_decision_models complete gaps=%d agenda=%d duration_ms=%.2f",
        len(recompute["gaps"]),
        len(recompute["agenda_queue"]),
        (time.perf_counter() - started) * 1000,
    )
    return {
        "dmn_results": {
            "gaps_count": len(recompute["gaps"]),
            "agenda_metrics": recompute["agenda_metrics"],
            "maturity_progress": recompute["maturity_progress"],
        },
        "agenda_queue": recompute["agenda_queue"],
    }


def node_synthesize_remediation_signals(state: PMGAState, config: RunnableConfig) -> Dict[str, Any]:
    started = time.perf_counter()
    project_id = state["project_id"]
    opts = _runtime_llm_opts(config)

    # Optionally polish open top-3 gaps warmly when a live LLM key is configured
    if _has_usable_llm(opts):
        with get_db() as conn:
            open_gaps = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, standard_clause, warm_summary, policy_vs_reality FROM remediation_gaps WHERE project_id = ? AND status = 'OPEN' ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END LIMIT 3",
                    (project_id,),
                ).fetchall()
            ]
        if open_gaps:
            class _GapPolishItem(BaseModel):
                gap_id: str
                warm_summary: str
                first_concrete_step: str

            class _GapPolishBatch(BaseModel):
                items: List[_GapPolishItem]

            safe_input = DocumentService.scan_for_pii(json.dumps(open_gaps, ensure_ascii=False))["redacted_text"]
            try:
                polished = LLMClient.generate_structured(
                    prompt=f"Make these municipal remediation summaries warm, supportive, and plain-English (never punitive audit jargon):\n{safe_input}",
                    response_model=_GapPolishBatch,
                    system_instruction="You write supportive, practical BSI/ISO remediation advice for municipal staff.",
                    **opts,
                )
                with get_db() as conn:
                    for item in polished.items:
                        row = conn.execute(
                            "SELECT action_steps_json FROM remediation_gaps WHERE id = ? AND project_id = ?",
                            (item.gap_id, project_id),
                        ).fetchone()
                        if row:
                            steps = json.loads(row["action_steps_json"] or "[]")
                            if item.first_concrete_step and item.first_concrete_step not in steps:
                                steps.insert(0, item.first_concrete_step)
                            conn.execute(
                                "UPDATE remediation_gaps SET warm_summary = ?, action_steps_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (item.warm_summary, json.dumps(steps[:5], ensure_ascii=False), item.gap_id),
                            )
            except Exception as exc:
                logger.warning("LLM gap synthesis enrichment skipped (deterministic playbooks kept): %s", exc)

    logger.debug("Node synthesize_remediation_signals complete duration_ms=%.2f", (time.perf_counter() - started) * 1000)
    return {}


def node_compute_next_agenda(state: PMGAState) -> Dict[str, Any]:
    # Agenda was computed via 02_question_agenda.json in run_dmn_decision_models;
    # ensure top items are clean & bounded.
    agenda = state.get("agenda_queue") or []
    return {"agenda_queue": agenda[:5]}


def node_compose_coach_reply(state: PMGAState, config: RunnableConfig) -> Dict[str, Any]:
    started = time.perf_counter()
    project_id = state["project_id"]
    lang = state.get("language", "en")
    agenda = state.get("agenda_queue") or []
    dmn = state.get("dmn_results") or {}
    patch = state.get("extracted_patch") or {}
    changes = patch.get("what_changed_summary") or []
    maturity = dmn.get("maturity_progress") or {}
    gaps_count = dmn.get("gaps_count", 0)
    opts = _runtime_llm_opts(config)
    has_llm = _has_usable_llm(opts)

    top = agenda[0] if agenda else None
    quick_choices: List[str] = []

    if not top:
        if lang == "de":
            msg = f"Großartig — die Struktur, Zuständigkeiten, Schutzbedarf und BIA sind erfasst ({gaps_count} offene Hinweise im Maßnahmenplan). Sie können jetzt den Prozesssteckbrief, die Strukturanalyse oder den Maßnahmenplan exportieren."
            quick_choices = ["Maßnahmenplan prüfen", "Dokumente exportieren"]
        else:
            msg = f"Great progress — your process structure, ownership, protection needs, and BIA targets are captured ({gaps_count} active remediation guidance item(s) ready). You can inspect the diagram badges or preview and export your documents anytime."
            quick_choices = ["Review remediation plan", "Preview & export deliverables"]
    else:
        key = top["agenda_key"]
        target = top.get("target_name") or "this item"
        if key == "LINK_SYSTEM_TO_PROCESS":
            msg = (
                f"I see '{target}' listed as an IT system. Which business process(es) depend on '{target}' in daily work (and which team owns them)?"
                if lang != "de"
                else f"Ich sehe '{target}' als IT-System. Welche Geschäftsprozesse nutzen '{target}' im Alltag (und wer ist verantwortlich)?"
            )
            quick_choices = [
                f"{target} supports Citizen Registration (Bürgeramt)",
                f"{target} supports Voucher Issuing (Kämmerei)",
                f"{target} supports Staff Onboarding (HR)",
            ]
        elif key == "ELICIT_PROCESS_STEPS":
            msg = (
                f"How does '{target}' work step-by-step in real life — what triggers it, what happens first, and where does it finish?"
                if lang != "de"
                else f"Wie läuft '{target}' im Alltag Schritt für Schritt ab — was löst ihn aus und womit endet er?"
            )
            quick_choices = [f"Clerk receives request -> verifies data in system -> approves & archives"]
        elif key == "ASSIGN_STEP_ACTORS":
            msg = (
                f"Some steps in '{target}' don't have a specific department or role lane yet. Which team handles each step?"
                if lang != "de"
                else f"Einige Schritte in '{target}' haben noch keine Zuständigkeit. Welches Team führt diese aus?"
            )
            quick_choices = ["Owner: Bürgeramt", "Owner: Kämmerei", "Owner: IT Support"]
        elif key == "IDENTIFY_DATA_ASSETS":
            msg = (
                f"Which citizen forms, documents, or records are read and produced during '{target}'?"
                if lang != "de"
                else f"Welche Dokumente, Anträge oder Datensätze werden bei '{target}' verarbeitet?"
            )
            quick_choices = ["Data: Citizen Application Form, Identity Record, Approval Notice"]
        elif key == "CLARIFY_EXCEPTIONS":
            msg = (
                f"In '{target}', what happens in practice if required citizen data is missing, an approval is rejected, or the system goes offline?"
                if lang != "de"
                else f"Was passiert bei '{target}' in der Praxis, wenn Unterlagen fehlen oder ein System ausfällt?"
            )
            quick_choices = [
                "If docs are missing, clerk emails citizen to resubmit",
                "Manual paper fallback form when system is down",
            ]
        elif key == "UNPACK_CIA_CRITICALITY":
            msg = (
                f"Let's unpack the protection needs for '{target}': how sensitive is Confidentiality (leaks), Integrity (errors/tampering), and Availability (downtime) — Normal, High, or Very High?"
                if lang != "de"
                else f"Bitte teilen Sie den Schutzbedarf für '{target}' in Vertraulichkeit, Integrität und Verfügbarkeit ein (Normal, High, Very High)."
            )
            quick_choices = [
                "Confidentiality: High, Integrity: High, Availability: High",
                "Confidentiality: Very High, Integrity: High, Availability: Very High",
            ]
        elif key == "CAPTURE_BIA_RTO_MTPD":
            msg = (
                f"For '{target}', how long could the team tolerate an outage before serious harm occurs (MTPD), and how quickly does IT need to restore it (RTO < MTPD)?"
                if lang != "de"
                else f"Wie lange darf '{target}' maximal ausfallen (MTPD) und wie schnell muss die Wiederherstellung erfolgen (RTO < MTPD)?"
            )
            quick_choices = ["RTO: 4h, MTPD: 24h", "RTO: 12h, MTPD: 48h"]
        elif key == "VERIFY_DOCUMENT_CLAIMS":
            msg = (
                f"An uploaded document mentions '{target}'. Does that safeguard actually happen consistently in daily operations?"
                if lang != "de"
                else f"Ein hochgeladenes Dokument nennt '{target}'. Wird das im Alltag tatsächlich so gelebt?"
            )
            quick_choices = ["Confirmed in daily practice", "Not enforced in reality (flag as gap)"]
        else:
            msg = (
                f"We flagged {gaps_count} practical improvement item(s) across your processes (click any badge on the diagram to inspect). Would you like to adjust any workflow step or review the remediation plan?"
                if lang != "de"
                else f"Es liegen {gaps_count} praktische Verbesserungs-Hinweise vor (klicken Sie auf die Badges im Diagramm). Möchten Sie Schritte anpassen oder den Maßnahmenplan prüfen?"
            )
            quick_choices = ["Review remediation plan", "Preview & export deliverables"]

    if has_llm and top:
        try:
            class _CoachMsg(BaseModel):
                message: str

            safe_ctx = DocumentService.scan_for_pii(
                f"Changes: {', '.join(changes)}. Goal: {top.get('goal')}. Draft question: {msg}"
            )["redacted_text"]
            polished = LLMClient.generate_structured(
                prompt=f"Write 2 warm, supportive sentences in {lang.upper()} acknowledging what changed and asking the single clearest question:\n{safe_ctx}",
                response_model=_CoachMsg,
                system_instruction="You are a supportive municipal process coach. Never sound like a punitive auditor.",
                **opts,
            )
            if polished.message.strip():
                msg = polished.message.strip()
        except Exception as exc:
            logger.debug("Using deterministic coach message (LLM polish skipped: %s)", exc)

    reply = {
        "message": msg,
        "quick_choices": quick_choices,
        "what_changed_summary": changes,
        "maturity_progress": maturity,
        "llm_mode": "live" if has_llm else "offline",
        "llm_notice": None if has_llm else "Deterministic offline mode active — add an AI key anytime for natural-language dialogue.",
    }

    with get_db() as conn:
        conn.execute(
            """INSERT INTO session_turns (id, project_id, thread_id, role, content, input_kind, patch_summary_json)
               VALUES (?, ?, ?, 'coach', ?, 'chat', ?)""",
            (str(uuid.uuid4()), project_id, project_id, msg, json.dumps({"what_changed": changes, "quick_choices": quick_choices}, ensure_ascii=False)),
        )

    logger.info("Node compose_coach_reply complete llm_mode=%s duration_ms=%.2f", reply["llm_mode"], (time.perf_counter() - started) * 1000)
    return {
        "coach_reply": reply,
        "messages": [{"role": "assistant", "content": msg}],
    }


def node_await_next_turn(state: PMGAState) -> Dict[str, Any]:
    """LangGraph interrupt() checkpoint pausing cleanly across days/weeks/server restarts."""
    resumed = interrupt({
        "coach_reply": state.get("coach_reply") or {},
        "agenda_queue": state.get("agenda_queue") or [],
    })
    return {
        "latest_input_kind": resumed.get("input_kind", "chat"),
        "latest_input_payload": resumed.get("payload", {}),
        "language": resumed.get("language") or state.get("language", "en"),
        "active_process_id": resumed.get("active_process_id") or state.get("active_process_id"),
    }


def build_workflow_graph(checkpointer: SqliteSaver):
    """Compile the 7-node + interrupt StateGraph against the supplied SqliteSaver."""
    builder = StateGraph(PMGAState)
    builder.add_node("ingest_and_redact", node_ingest_and_redact)
    builder.add_node("extract_graph_patch", node_extract_graph_patch)
    builder.add_node("apply_and_verify_graph", node_apply_and_verify_graph)
    builder.add_node("run_dmn_decision_models", node_run_dmn_decision_models)
    builder.add_node("synthesize_remediation_signals", node_synthesize_remediation_signals)
    builder.add_node("compute_next_agenda", node_compute_next_agenda)
    builder.add_node("compose_coach_reply", node_compose_coach_reply)
    builder.add_node("await_next_turn", node_await_next_turn)

    builder.add_edge(START, "ingest_and_redact")
    builder.add_edge("ingest_and_redact", "extract_graph_patch")
    builder.add_edge("extract_graph_patch", "apply_and_verify_graph")
    builder.add_edge("apply_and_verify_graph", "run_dmn_decision_models")
    builder.add_edge("run_dmn_decision_models", "synthesize_remediation_signals")
    builder.add_edge("synthesize_remediation_signals", "compute_next_agenda")
    builder.add_edge("compute_next_agenda", "compose_coach_reply")
    builder.add_edge("compose_coach_reply", "await_next_turn")
    builder.add_edge("await_next_turn", "ingest_and_redact")

    return builder.compile(checkpointer=checkpointer)


def _validate_turn_input(input_kind: str, payload: Dict[str, Any]) -> None:
    if input_kind not in ("chat", "excel", "document", "bpmn_edit"):
        raise ValidationError(f"Unsupported input_kind '{input_kind}'. Must be chat, excel, document, or bpmn_edit.")
    if input_kind == "chat":
        if not str((payload or {}).get("message") or "").strip():
            raise ValidationError("Chat message must not be empty.")
    elif input_kind == "bpmn_edit":
        xml_text = str((payload or {}).get("bpmn_xml") or "").strip()
        if not xml_text:
            raise ValidationError("bpmn_edit turn requires non-empty 'bpmn_xml'.")
        parse_bpmn_xml_to_ir(xml_text, fallback_title=str((payload or {}).get("title") or "Process"))
    elif input_kind == "excel":
        fp = (payload or {}).get("file_path")
        if not fp or not os.path.exists(fp):
            raise ValidationError("Excel turn requires a valid uploaded workbook file.")
    elif input_kind == "document":
        fp = (payload or {}).get("file_path")
        txt = str((payload or {}).get("extracted_text") or "").strip()
        if not txt and (not fp or not os.path.exists(fp)):
            raise ValidationError("Document turn requires readable text or an uploaded file.")


def run_project_turn(
    project_id: str,
    *,
    input_kind: str,
    payload: Dict[str, Any],
    language: str = "en",
    active_process_id: Optional[str] = None,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute one multi-session turn for `project_id`, starting or resuming its checkpointed LangGraph thread."""
    get_project_or_404(project_id)
    _validate_turn_input(input_kind, payload)
    turn_id = str(uuid.uuid4())[:12]
    lang: Literal["en", "de"] = "de" if str(language).lower().startswith("de") else "en"

    with log_context(project_id=project_id, turn_id=turn_id):
        lock = _get_project_lock(project_id)
        with lock:
            started = time.perf_counter()
            run_config = {"configurable": {"thread_id": project_id}}
            llm_token = _RUNTIME_LLM_VAR.set({
                "api_key": api_key,
                "provider": provider,
                "model_name": model_name,
                "base_url": base_url,
            })
            try:
                with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as checkpointer:
                    compiled = build_workflow_graph(checkpointer)
                    existing_state = compiled.get_state({"configurable": {"thread_id": project_id}})

                    if existing_state and existing_state.next and "await_next_turn" in existing_state.next:
                        logger.info("Resuming LangGraph thread=%s at %s", project_id, existing_state.next)
                        result_state = compiled.invoke(
                            Command(resume={
                                "input_kind": input_kind,
                                "payload": payload,
                                "language": lang,
                                "active_process_id": active_process_id,
                            }),
                            config=run_config,
                        )
                    else:
                        logger.info("Starting new LangGraph thread=%s", project_id)
                        init_state: PMGAState = {
                            "project_id": project_id,
                            "language": lang,
                            "messages": [],
                            "active_process_id": active_process_id,
                            "latest_input_kind": input_kind,  # type: ignore[typeddict-item]
                            "latest_input_payload": payload,
                            "extracted_patch": {},
                            "dmn_results": {},
                            "agenda_queue": [],
                            "coach_reply": {},
                        }
                        result_state = compiled.invoke(init_state, config=run_config)
            except (ValidationError, BPMNParseError):
                raise
            except Exception as exc:
                logger.error("LangGraph turn execution failed: %s", exc, exc_info=True)
                raise WorkflowExecutionError(f"Workflow turn failed: {exc}") from exc
            finally:
                _RUNTIME_LLM_VAR.reset(llm_token)

            workspace = get_workspace_state(project_id)
            logger.info("Turn finished in %.2fms", (time.perf_counter() - started) * 1000)
            return {
                "status": "ok",
                "turn_id": turn_id,
                "project_id": project_id,
                "coach_reply": result_state.get("coach_reply") or {},
                "agenda_queue": result_state.get("agenda_queue") or [],
                "workspace": workspace,
            }
