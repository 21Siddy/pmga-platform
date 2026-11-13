"""Smart Excel column mapping and deterministic handbook/sheet evidence extractor.

Extracts only what the uploaded documents actually prove, attaching exact
verbatim quotes and sheet/row or line citations. Never invents answers;
unproven items remain open questions (NEEDS_USER_INPUT).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db
from app.services.entity_service import EntityService

_HEADER_ALIASES = {
    "entry": ("prozess", "process", "geschäftsprozess", "anwendung", "system", "vorgang", "aufgabe", "activity", "name", "titel", "title", "item", "bezeichnung"),
    "kind": ("typ", "type", "art", "kind", "kategorie", "category", "objekttyp"),
    "process": ("zugehöriger prozess", "business process", "hauptprozess", "parent process", "prozessname"),
    "owner": ("verantwortlich", "owner", "rolle", "role", "abteilung", "department", "zuständig", "amt", "fachbereich"),
    "criticality": ("kritikalität", "criticality", "schutzbedarf", "bewertung", "einstufung", "priorität", "rating"),
    "cia_c": ("vertraulichkeit", "confidentiality"),
    "cia_i": ("integrität", "integrity"),
    "cia_a": ("verfügbarkeit", "availability"),
    "bia": ("rto", "mtpd", "wiederanlauf", "ausfallzeit"),
    "input_data": ("input", "eingangsdaten", "eingabe", "datenbedarf"),
    "output_data": ("output", "ausgangsdaten", "ergebnis"),
    "storage": ("speicherort", "storage", "ablage", "datenbank", "system"),
    "trigger": ("auslöser", "trigger", "ereignis", "start"),
    "notes": ("beschreibung", "description", "ablauf", "notizen", "notes", "hinweis", "bemerkung"),
}

# Deterministic keyword rules mapping handbook / policy sentences to catalog req_ids
_HANDBOOK_REQ_RULES: List[Tuple[str, re.Pattern, str]] = [
    ("R012", re.compile(r"\b(mfa|multi-?factor|zwei-?faktor|2fa|rbac|berechtigung|access control|identity|identitäts)\b", re.I | re.U), "YES"),
    ("R013", re.compile(r"\b(patch|update-?management|änderungsmanagement|change management|schwachstellen)\b", re.I | re.U), "YES"),
    ("R014", re.compile(r"\b(logging|protokollierung|siem|monitoring|incident|vorfall|meldeweg)\b", re.I | re.U), "YES"),
    ("R015", re.compile(r"\b(backup|datensicherung|wiederherstellung|löschkonzept|deletion|retention)\b", re.I | re.U), "YES"),
    ("R021", re.compile(r"\b(notbetrieb|notfallplan|emergency|fallback|wiederanlaufplan|contingency)\b", re.I | re.U), "YES"),
    ("R031", re.compile(r"\b(informationssicherheitsleitlinie|security policy|sicherheitsrichtlinie|richtlinie|directive)\b", re.I | re.U), "YES"),
    ("R037", re.compile(r"\b(dokumentiert|dms|dokumentenmanagement|process handbook|prozesshandbuch|freigegeben)\b", re.I | re.U), "YES"),
]


def _match_header_role(header: str) -> Optional[str]:
    low = (header or "").strip().lower()
    if not low:
        return None
    for role, aliases in _HEADER_ALIASES.items():
        if any(alias == low or alias in low for alias in aliases):
            return role
    return None


def map_excel_sheets_to_survey_items(sheets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert extracted Excel sheets into survey items using header detection.

    Avoids assuming Column A is always the entry name when ID or department columns
    appear first. Attaches exact sheet & row citations.
    """
    items: List[Dict[str, Any]] = []
    for sheet in sheets:
        sname = sheet["sheet_name"]
        headers = sheet.get("headers") or []
        col_roles: Dict[int, str] = {}
        for idx, h in enumerate(headers):
            role = _match_header_role(h)
            if role and role not in col_roles.values():
                col_roles[idx] = role

        entry_idx = next((i for i, r in col_roles.items() if r == "entry"), None)
        if entry_idx is None:
            # Fallback: skip pure numeric ID columns if column 1 looks textual
            entry_idx = 0

        for row_num, r in enumerate(sheet.get("rows") or [], start=2):
            raw_list = r.get("raw_list") or []
            if not raw_list:
                continue

            # If chosen entry_idx is empty or purely numeric ID and another non-empty text col exists, pick it
            idx_to_use = entry_idx if entry_idx < len(raw_list) else 0
            raw_entry = (raw_list[idx_to_use] or "").strip()
            if (not raw_entry or raw_entry.isdigit()) and len(raw_list) > 1:
                for alt_idx, val in enumerate(raw_list):
                    if val and not val.isdigit():
                        raw_entry = val.strip()
                        break
            if not raw_entry:
                continue

            def _get(role_name: str) -> Optional[str]:
                for i, role in col_roles.items():
                    if role == role_name and i < len(raw_list) and raw_list[i].strip():
                        return raw_list[i].strip()
                return None

            crit = _get("criticality") or ""
            if not crit:
                for cell_val in raw_list:
                    if any(c in cell_val.lower() for c in ("kritisch", "critical", "sehr hoch", "very high", "hoch", "high")):
                        crit = cell_val.strip()
                        break

            raw_kind = (_get("kind") or "").lower()
            explicit_kind = None
            if any(k in raw_kind for k in ("system", "app", "anwendung", "software", "tool")):
                explicit_kind = "APPLICATION"
            elif any(k in raw_kind for k in ("prozess", "process", "vorgang", "activity")):
                explicit_kind = "PROCESS"

            items.append({
                "sheet_name": sname,
                "row_number": row_num,
                "raw_entry": raw_entry,
                "entry_kind": explicit_kind,
                "raw_criticality": crit,
                "process_name": _get("process"),
                "owner_role": _get("owner"),
                "trigger_event": _get("trigger"),
                "lived_reality_notes": _get("notes"),
                "input_data": _get("input_data"),
                "output_data": _get("output_data"),
                "storage_location": _get("storage"),
                "citation": f"Sheet '{sname}', Row {row_num}",
            })
    return items


def extract_handbook_evidence(
    project_id: str,
    text: str,
    filename: str,
    doc_type: str = "FORMAL_POLICY",
) -> Dict[str, Any]:
    """Scan handbook/operational text for proven entities, roles, and standard requirements.

    Only records facts backed by an exact verbatim sentence quote from the file.
    """
    if not (text or "").strip():
        return {"entities_touched": 0, "assessments_proven": 0, "citations": []}

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[\.\!\?\n])\s+", text.replace("\r", ""))
        if len(s.strip()) >= 12
    ]

    # Ensure at least one anchor process entity exists to attach document-level evidence
    with get_db() as conn:
        existing_procs = conn.execute(
            "SELECT * FROM org_entities WHERE project_id = ? AND entity_type = 'PROCESS' ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()

    if existing_procs:
        anchor_entity = dict(existing_procs[0])
    else:
        # Derive process/scope name from first heading or filename
        first_line = next((ln.strip(" #*-") for ln in text.splitlines() if ln.strip()), filename)
        anchor_entity = EntityService.upsert_entity(
            project_id=project_id,
            entity_type="PROCESS",
            name=first_line[:80],
            description=f"Derived from uploaded {doc_type}: {filename}",
        )

    entities_touched = 1
    assessments_proven = 0
    citations: List[Dict[str, Any]] = []

    # 1. Extract explicit Role / Owner mentions ("Verantwortlich: ...", "Owner: ...", "Role: ...")
    for line_no, line in enumerate(text.splitlines(), start=1):
        role_m = re.search(
            r"(?:verantwortlich|zuständig|owner|role|department|abteilung)\s*[:\-]\s*([^\n.;,]{3,50})",
            line,
            re.IGNORECASE | re.UNICODE,
        )
        if role_m:
            role_name = role_m.group(1).strip()
            role_ent = EntityService.upsert_entity(project_id, "ROLE", role_name)
            EntityService.upsert_dependency(project_id, anchor_entity["id"], role_ent["id"], "EXECUTED_BY")
            ev = [{"source": filename, "location": f"Line {line_no}", "quote": line.strip()[:220]}]
            EntityService.save_entity_assessment(
                project_id, anchor_entity["id"], "R003",
                answer_value=role_name, evidence=ev, context={"owner_role": role_name},
                answered_by="document_extraction",
            )
            entities_touched += 1
            assessments_proven += 1
            citations.append({"req_id": "R003", "quote": line.strip()[:220], "source": f"{filename}:L{line_no}"})

        # Extract RTO / MTPD figures if explicitly stated
        bia_m = re.search(r"\b(RTO|MTPD)\s*[:=]?\s*(\d+(?:[.,]\d+)?\s*(?:h|std|stunden|tag|tage|day|days)?)", line, re.I)
        if bia_m:
            ev = [{"source": filename, "location": f"Line {line_no}", "quote": line.strip()[:220]}]
            from app.services.entity_service import _parse_rto_mtpd
            rto_val, mtpd_val = _parse_rto_mtpd(line)
            if mtpd_val is not None:
                EntityService.save_entity_assessment(
                    project_id, anchor_entity["id"], "R017",
                    answer_value=f"{mtpd_val:g} h", answer_numeric=mtpd_val,
                    evidence=ev, answered_by="document_extraction",
                )
                assessments_proven += 1
            if rto_val is not None and mtpd_val is not None:
                EntityService.save_entity_assessment(
                    project_id, anchor_entity["id"], "R018",
                    answer_value=f"{rto_val:g} h", answer_numeric=rto_val,
                    evidence=ev, context={"rto": rto_val, "mtpd": mtpd_val},
                    answered_by="document_extraction",
                )
                assessments_proven += 1

    # 2. Match evidence-backed requirements from verbatim handbook sentences
    matched_reqs = set()
    for sent in sentences:
        for req_id, pattern, answer_val in _HANDBOOK_REQ_RULES:
            if req_id in matched_reqs:
                continue
            if pattern.search(sent):
                matched_reqs.add(req_id)
                ev = [{"source": filename, "location": "Verbatim quote", "quote": sent[:240]}]
                EntityService.save_entity_assessment(
                    project_id,
                    anchor_entity["id"],
                    req_id,
                    answer_value=answer_val,
                    justification=f"Proven in {filename}: \"{sent[:160]}\"",
                    evidence=ev,
                    answered_by="document_extraction",
                )
                assessments_proven += 1
                citations.append({"req_id": req_id, "quote": sent[:200], "source": filename})

    return {
        "anchor_entity_id": anchor_entity["id"],
        "entities_touched": entities_touched,
        "assessments_proven": assessments_proven,
        "citations": citations,
    }
