"""Brandable municipal document deliverable renderer (Jinja2 HTML + WeasyPrint PDF + XLSX/BPMN).

Supports:
- `remediation_plan`: supportive, prioritized BSI + ISO 27002 remediation roadmap with embedded gap-annotated SVG diagrams
- `prozesssteckbrief`: FIM-aligned municipal Process Profile per workflow
- `strukturanalyse`: BSI 200-2 Strukturanalyse, Maximumprinzip inheritance proof & evidence ledger
- `complete_pack`: combined multi-part printable package
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.database import get_db
from app.errors import DeliverableRenderError, NotFoundError, ValidationError
from app.services.bpmn_ir_service import BPMNProcessIR, parse_bpmn_xml_to_ir, render_ir_to_svg
from app.services.iso_excel_service import ISOExcelService
from app.services.workspace_service import get_brand_profile, get_project_or_404, get_workspace_state

logger = logging.getLogger(__name__)

VALID_DOC_TYPES = ("remediation_plan", "prozesssteckbrief", "strukturanalyse", "complete_pack")
VALID_FORMATS = ("html", "pdf", "xlsx", "bpmn")
ALLOWED_FONTS = (
    "Plus Jakarta Sans",
    "Inter",
    "Source Sans 3",
    "IBM Plex Sans",
    "Georgia",
    "Merriweather",
    "system-ui",
)
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_DATA_URI_IMG_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif|svg\+xml);base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)
_SVG_DANGEROUS_RE = re.compile(r"(<\s*script\b|<\s*foreignObject\b|\bon[a-z]+\s*=|javascript\s*:)", re.IGNORECASE)

_DOC_TITLES = {
    "remediation_plan": "BSI & ISO 27002 Remediation Plan",
    "prozesssteckbrief": "FIM Prozesssteckbrief (Process Profile)",
    "strukturanalyse": "BSI 200-2 Strukturanalyse & Schutzbedarfsfeststellung",
    "complete_pack": "Complete Municipal Process & Resilience Pack",
}

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "deliverables")
_JINJA_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _validate_logo_data_uri(uri: Optional[str]) -> Optional[str]:
    if uri is None or str(uri).strip() == "":
        return None
    clean = str(uri).strip()
    if len(clean) > 700_000:
        raise ValidationError("Logo image exceeds 500 KB encoded size limit.")
    m = _DATA_URI_IMG_RE.match(clean)
    if not m:
        raise ValidationError("Logo must be a valid base64 data:image/png, jpeg, webp, gif, or svg+xml URI.")
    img_kind, b64_body = m.groups()
    try:
        decoded = base64.b64decode(b64_body.strip(), validate=True)
    except Exception as exc:
        raise ValidationError("Logo data URI contains invalid base64 payload.") from exc
    if img_kind.lower() == "svg+xml":
        svg_text = decoded.decode("utf-8", errors="replace")
        if _SVG_DANGEROUS_RE.search(svg_text):
            raise ValidationError("SVG logo contains disallowed active script or event handler attributes.")
    clean_b64 = re.sub(r"\s+", "", b64_body)
    return f"data:image/{img_kind.lower()};base64,{clean_b64}"


def save_brand_profile(
    project_id: str,
    *,
    org_name: Optional[str] = None,
    logo_data_uri: Optional[str] = None,
    primary_color: Optional[str] = None,
    accent_color: Optional[str] = None,
    font_family: Optional[str] = None,
    footer_text: Optional[str] = None,
    clear_logo: bool = False,
) -> Dict[str, Any]:
    """Validate and persist customizable municipal branding for a project."""
    get_project_or_404(project_id)
    current = get_brand_profile(project_id)

    clean_org = (org_name if org_name is not None else current["org_name"]).strip()[:120] or "Municipal Administration"
    clean_primary = (primary_color if primary_color is not None else current["primary_color"]).strip()
    clean_accent = (accent_color if accent_color is not None else current["accent_color"]).strip()
    clean_font = (font_family if font_family is not None else current["font_family"]).strip()
    clean_footer = (footer_text if footer_text is not None else (current.get("footer_text") or "")).strip()[:200]

    if not _HEX_COLOR_RE.match(clean_primary):
        raise ValidationError("primary_color must be a 6-digit hex color like #1E4E68.")
    if not _HEX_COLOR_RE.match(clean_accent):
        raise ValidationError("accent_color must be a 6-digit hex color like #C86D44.")
    if clean_font not in ALLOWED_FONTS:
        raise ValidationError(f"Unsupported font_family '{clean_font}'. Choose one of {ALLOWED_FONTS}.")

    if clear_logo:
        clean_logo = None
    elif logo_data_uri is not None:
        clean_logo = _validate_logo_data_uri(logo_data_uri)
    else:
        clean_logo = current.get("logo_data_uri")

    with get_db() as conn:
        conn.execute(
            """INSERT INTO brand_profiles
               (project_id, org_name, logo_data_uri, primary_color, accent_color, font_family, footer_text, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id) DO UPDATE SET
                 org_name = excluded.org_name,
                 logo_data_uri = excluded.logo_data_uri,
                 primary_color = excluded.primary_color,
                 accent_color = excluded.accent_color,
                 font_family = excluded.font_family,
                 footer_text = excluded.footer_text,
                 updated_at = CURRENT_TIMESTAMP""",
            (project_id, clean_org, clean_logo, clean_primary, clean_accent, clean_font, clean_footer),
        )

    logger.info("Updated brand profile for project=%s org=%s primary=%s", project_id, clean_org, clean_primary)
    return get_brand_profile(project_id)


def _build_deliverable_context(project_id: str, doc_type: str) -> Dict[str, Any]:
    if doc_type not in VALID_DOC_TYPES:
        raise ValidationError(f"Unsupported deliverable doc_type '{doc_type}'. Must be one of {VALID_DOC_TYPES}.")

    ws = get_workspace_state(project_id)
    gaps = ws["remediation_gaps"]
    structure = ws["structure"]
    entities_by_id = {e["id"]: e for e in structure["entities"]}

    grouped_gaps = {
        "QUICK_WIN": [g for g in gaps if g.get("priority_bucket") == "QUICK_WIN"],
        "BEFORE_DIGITALIZATION": [g for g in gaps if g.get("priority_bucket") == "BEFORE_DIGITALIZATION"],
        "ONGOING": [g for g in gaps if g.get("priority_bucket") not in ("QUICK_WIN", "BEFORE_DIGITALIZATION")],
    }

    sev_rank = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "INFO": 1}

    def _gap_badge_map_for_pm(pm_id: str) -> Dict[str, Tuple[int, str]]:
        counts: Dict[str, int] = {}
        worst: Dict[str, str] = {}
        for g in gaps:
            if g.get("status") == "RESOLVED":
                continue
            if g.get("process_model_id") and g["process_model_id"] != pm_id:
                continue
            for nid in g.get("affected_node_ids") or []:
                counts[nid] = counts.get(nid, 0) + 1
                prev = worst.get(nid, "INFO")
                if sev_rank.get(g.get("severity", "MODERATE"), 2) > sev_rank.get(prev, 1):
                    worst[nid] = g.get("severity", "MODERATE")
        return {nid: (cnt, worst.get(nid, "MODERATE")) for nid, cnt in counts.items()}

    diagram_svgs: List[Dict[str, Any]] = []
    process_profiles: List[Dict[str, Any]] = []

    for pm in ws["process_models"]:
        try:
            ir = BPMNProcessIR.model_validate(pm["ir"]) if pm.get("ir") else parse_bpmn_xml_to_ir(pm["bpmn_xml"], fallback_title=pm["title"])
        except Exception:
            ir = BPMNProcessIR(process_title=pm["title"])

        badge_map = _gap_badge_map_for_pm(pm["id"])
        svg = render_ir_to_svg(ir, gap_counts_by_step=badge_map)
        diagram_svgs.append({
            "id": pm["id"],
            "title": pm["title"],
            "version": pm.get("version", 1),
            "description": pm.get("description_text") or "",
            "svg": svg,
        })

        ent = entities_by_id.get(pm.get("entity_id") or "")
        decl = (ent or {}).get("declared_protection") or {}
        inh = (ent or {}).get("inherited_protection") or {}
        prot = {
            "confidentiality": decl.get("confidentiality") or inh.get("confidentiality"),
            "integrity": decl.get("integrity") or inh.get("integrity"),
            "availability": decl.get("availability") or inh.get("availability"),
            "rto": decl.get("rto") if decl.get("rto") is not None else inh.get("rto"),
            "mtpd": decl.get("mtpd") if decl.get("mtpd") is not None else inh.get("mtpd"),
        }
        actors = [l for l in ir.lanes if l and l.lower() not in ("general process", "default lane")]
        systems = sorted({s.system_name for s in ir.steps if s.system_name})
        if ent:
            for dep in structure["dependencies"]:
                if dep["to_entity_id"] == ent["id"] and dep["dependency_type"] == "SUPPORTS":
                    src = entities_by_id.get(dep["from_entity_id"])
                    if src and src["name"] not in systems:
                        systems.append(src["name"])
        data_assets = [d.name for d in ir.data_objects]
        if ent:
            for dep in structure["dependencies"]:
                if dep["from_entity_id"] == ent["id"] and dep["dependency_type"] == "USES_DATA":
                    tgt = entities_by_id.get(dep["to_entity_id"])
                    if tgt and tgt["name"] not in data_assets:
                        data_assets.append(tgt["name"])

        process_profiles.append({
            "title": pm["title"],
            "owner_role": (ent or {}).get("owner_role") or (actors[0] if actors else None),
            "trigger": ir.trigger_event,
            "actors": actors,
            "systems": systems,
            "data_assets": data_assets,
            "protection": prot,
            "description": pm.get("description_text") or "",
            "steps": ir.steps,
            "svg": svg,
        })

    # Include processes from org_entities that don't have a process_model yet in process_profiles
    modeled_entity_ids = {pm.get("entity_id") for pm in ws["process_models"] if pm.get("entity_id")}
    for ent in structure["entities"]:
        if ent["entity_type"] == "PROCESS" and ent["id"] not in modeled_entity_ids:
            decl = ent.get("declared_protection") or {}
            process_profiles.append({
                "title": ent["name"],
                "owner_role": ent.get("owner_role"),
                "trigger": None,
                "actors": [ent["owner_role"]] if ent.get("owner_role") else [],
                "systems": [
                    entities_by_id[d["from_entity_id"]]["name"]
                    for d in structure["dependencies"]
                    if d["to_entity_id"] == ent["id"] and d["dependency_type"] == "SUPPORTS" and d["from_entity_id"] in entities_by_id
                ],
                "data_assets": [
                    entities_by_id[d["to_entity_id"]]["name"]
                    for d in structure["dependencies"]
                    if d["from_entity_id"] == ent["id"] and d["dependency_type"] == "USES_DATA" and d["to_entity_id"] in entities_by_id
                ],
                "protection": decl,
                "description": ent.get("description") or "",
                "steps": [],
                "svg": None,
            })

    named_dependencies = [
        {
            "from_name": (entities_by_id.get(d["from_entity_id"]) or {}).get("name", d["from_entity_id"]),
            "dependency_type": d["dependency_type"],
            "to_name": (entities_by_id.get(d["to_entity_id"]) or {}).get("name", d["to_entity_id"]),
            "notes": d.get("notes"),
        }
        for d in structure["dependencies"]
    ]

    return {
        "doc_type": doc_type,
        "doc_title": _DOC_TITLES[doc_type],
        "project": ws["project"],
        "brand": ws["brand_profile"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "process_models": ws["process_models"],
        "process_profiles": process_profiles,
        "diagram_svgs": diagram_svgs,
        "structure": structure,
        "named_dependencies": named_dependencies,
        "evidence_items": ws["evidence_items"],
        "gaps": gaps,
        "grouped_gaps": grouped_gaps,
    }


def render_deliverable_html(project_id: str, doc_type: str) -> str:
    """Render a branded deliverable document to print-ready HTML via Jinja2."""
    started = time.perf_counter()
    ctx = _build_deliverable_context(project_id, doc_type)
    template = _JINJA_ENV.get_template(f"{doc_type}.html")
    html = template.render(**ctx)
    logger.info("Rendered deliverable HTML doc_type=%s project=%s duration_ms=%.2f", doc_type, project_id, (time.perf_counter() - started) * 1000)
    return html


def render_deliverable_pdf(project_id: str, doc_type: str) -> bytes:
    """Render a branded deliverable document to native PDF bytes via WeasyPrint."""
    html = render_deliverable_html(project_id, doc_type)
    started = time.perf_counter()
    try:
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    except (ImportError, OSError) as exc:
        logger.warning("WeasyPrint unavailable on host (%s); falling back to HTML print preview", exc)
        raise DeliverableRenderError(
            "Native PDF renderer (WeasyPrint/Pango) is unavailable on this host. Use HTML Print-to-PDF preview.",
            error_code="PMGA_PDF_UNAVAILABLE",
            status_code=501,
        ) from exc
    except Exception as exc:
        logger.error("WeasyPrint PDF rendering failed: %s", exc, exc_info=True)
        raise DeliverableRenderError(f"PDF generation failed: {exc}") from exc

    logger.info("Rendered deliverable PDF doc_type=%s bytes=%d duration_ms=%.2f", doc_type, len(pdf_bytes), (time.perf_counter() - started) * 1000)
    return pdf_bytes


def export_deliverable_xlsx(project_id: str) -> str:
    """Export unified workbook combining v2 remediation gaps and survey/structure rows."""
    proj = get_project_or_404(project_id)
    ws = get_workspace_state(project_id)
    with get_db() as conn:
        legacy_gaps = conn.execute("SELECT * FROM gap_assessments WHERE project_id = ?", (project_id,)).fetchall()
        survey_rows = conn.execute("SELECT * FROM process_survey_rows WHERE project_id = ? ORDER BY row_order ASC", (project_id,)).fetchall()

    gaps_list: List[Dict[str, Any]] = []
    if ws["remediation_gaps"]:
        for g in ws["remediation_gaps"]:
            sev = g.get("severity", "MODERATE")
            mapped_status = "CRITICAL_GAP" if sev == "CRITICAL" else ("COMPLIANT" if g.get("status") == "RESOLVED" else "PARTIAL_GAP")
            gaps_list.append({
                "chapter": g.get("standard_clause", ""),
                "control_id": g.get("req_id", ""),
                "control_title": g.get("gap_category", "").replace("_", " ").title(),
                "status": mapped_status,
                "policy_claim": g.get("policy_vs_reality") or "",
                "operational_reality": f"Affected nodes: {', '.join(g.get('affected_node_ids') or []) or 'Process-wide'}",
                "plain_english_gap": g.get("warm_summary", ""),
                "actionable_todos": g.get("action_steps") or [],
                "roles_to_consult": g.get("roles_to_involve") or [],
                "compensating_controls": g.get("compensating_options") or [],
                "user_decision_notes": f"Status: {g.get('status', 'OPEN')}",
            })
    else:
        for g in legacy_gaps:
            item = dict(g)
            item["actionable_todos"] = json.loads(g["actionable_todos_json"] or "[]")
            item["roles_to_consult"] = json.loads(g["roles_to_consult_json"] or "[]")
            item["compensating_controls"] = json.loads(g["compensating_controls_json"] or "[]")
            gaps_list.append(item)

    file_name = f"PMGA_Deliverable_{project_id[:8]}.xlsx"
    out_path = os.path.join(settings.exports_path, file_name)
    ISOExcelService.export_annotated_gap_report(
        output_path=out_path,
        project_title=proj["title"],
        gap_assessments=gaps_list,
        survey_rows=[dict(r) for r in survey_rows],
    )
    return out_path


def export_deliverable_bpmn(project_id: str, process_id: Optional[str] = None) -> Tuple[str, str]:
    """Return (filename, bpmn_xml) for the selected or primary process model."""
    get_project_or_404(project_id)
    with get_db() as conn:
        if process_id:
            pm = conn.execute("SELECT title, bpmn_xml FROM process_models WHERE id = ? AND project_id = ?", (process_id, project_id)).fetchone()
        else:
            pm = conn.execute("SELECT title, bpmn_xml FROM process_models WHERE project_id = ? ORDER BY is_primary DESC, created_at ASC LIMIT 1", (project_id,)).fetchone()
        if pm and pm["bpmn_xml"]:
            safe_title = re.sub(r"[^A-Za-z0-9_\-]+", "_", pm["title"]).strip("_") or "Process"
            return f"PMGA_{safe_title}.bpmn", pm["bpmn_xml"]

        legacy = conn.execute("SELECT title, bpmn_xml FROM bpmn_models WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        if legacy and legacy["bpmn_xml"]:
            return f"PMGA_Process_{project_id[:8]}.bpmn", legacy["bpmn_xml"]

    raise NotFoundError(f"No BPMN diagram found for project '{project_id}'.")
