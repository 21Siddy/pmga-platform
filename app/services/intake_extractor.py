"""Smart Excel column mapping and negation-safe handbook/policy evidence extractor.

Extracts only what uploaded workbooks, handbooks, policies, AGBs, or SBOMs
actually prove, attaching exact verbatim quotes and sheet/row or line citations.
Never invents answers and never marks a requirement COMPLIANT solely from a
keyword match—negated or conditional statements are recorded as CONTRADICTED /
DOCUMENT_CLAIM evidence items with explicit policy-vs-reality flags.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db
from app.services.entity_service import EntityService

logger = logging.getLogger(__name__)

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

# Rules mapping handbook/policy/AGB/SBOM sentences to catalog req_ids
_HANDBOOK_REQ_RULES: List[Tuple[str, re.Pattern, str]] = [
    ("R012", re.compile(r"\b(mfa|multi-?factor|zwei-?faktor|2fa|rbac|berechtigung|access control|identity|identitäts)\b", re.I | re.U), "Identity & access control (MFA/RBAC)"),
    ("R013", re.compile(r"\b(patch|update-?management|änderungsmanagement|change management|schwachstellen|cve|sbom|outdated|legacy)\b", re.I | re.U), "Patch, vulnerability & change management"),
    ("R014", re.compile(r"\b(logging|protokollierung|siem|monitoring|incident|vorfall|meldeweg|audit trail)\b", re.I | re.U), "Logging, monitoring & incident detection"),
    ("R015", re.compile(r"\b(backups?|datensicherung|wiederherstellung|löschkonzept|deletion|retention)\b", re.I | re.U), "Backup & retention procedures"),
    ("R021", re.compile(r"\b(notbetrieb|notfallplan|emergency|fallback|wiederanlaufplan|contingency)\b", re.I | re.U), "Emergency operation & fallback procedures"),
    ("R031", re.compile(r"\b(informationssicherheitsleitlinie|security policy|sicherheitsrichtlinie|richtlinie|directive)\b", re.I | re.U), "Information security policy"),
    ("R037", re.compile(r"\b(dokumentiert|dms|dokumentenmanagement|process handbook|prozesshandbuch|freigegeben)\b", re.I | re.U), "Documented process information"),
    ("R054", re.compile(r"\b(email attachment|unencrypted|usb|information transfer|datenübertragung|verschlüsselte übertragung)\b", re.I | re.U), "Information transfer security"),
    ("R104", re.compile(r"\b(mfa|multi-?factor|zwei-?faktor|2fa|password|kennwort|authentication|authentifizierung)\b", re.I | re.U), "Secure authentication (MFA)"),
]

_NEGATION_RE = re.compile(
    r"(?:\b(?:not|never|no|without|lacks?|missing|absent|disabled|bypass(?:ed|es|ing)?|unencrypted|shared\s+(?:admin|account|login|password)|currently\s+not)\b"
    r"|\b(?:nicht|kein(?:e|en|em|er|es)?|ohne|fehlt|unverschlüsselt|geteilt(?:es|e)?)\b"
    r"|n't\b)",
    re.I | re.U,
)

_PARTIAL_RE = re.compile(
    r"\b(partial(?:ly)?|only\s+for|informal(?:ly)?|planned|draft|work\s+in\s+progress|pending|teilweise|nur\s+für|geplant|entwurf)\b",
    re.I | re.U,
)


def classify_sentence_polarity(sentence: str) -> str:
    """Return 'NEGATED', 'PARTIAL', or 'AFFIRMATIVE' for negation-safe extraction."""
    if _NEGATION_RE.search(sentence):
        return "NEGATED"
    if _PARTIAL_RE.search(sentence):
        return "PARTIAL"
    return "AFFIRMATIVE"


def _match_header_role(header: str) -> Optional[str]:
    low = (header or "").strip().lower()
    if not low:
        return None
    for role, aliases in _HEADER_ALIASES.items():
        if any(alias == low or alias in low for alias in aliases):
            return role
    return None


def map_excel_sheets_to_survey_items(sheets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert extracted Excel sheets into survey items using header detection."""
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
            entry_idx = 0

        for row_num, r in enumerate(sheet.get("rows") or [], start=2):
            raw_list = r.get("raw_list") or []
            if not raw_list:
                continue

            idx_to_use = entry_idx if entry_idx < len(raw_list) else 0
            raw_entry = (raw_list[idx_to_use] or "").strip()
            if (not raw_entry or raw_entry.isdigit()) and len(raw_list) > 1:
                for val in raw_list:
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
                "raw_kind_label": _get("kind") or "",
                "raw_criticality": crit,
                "process_name": _get("process"),
                "owner_role": _get("owner"),
                "trigger_event": _get("trigger"),
                "lived_reality_notes": _get("notes"),
                "input_data": _get("input_data"),
                "output_data": _get("output_data"),
                "storage_location": _get("storage"),
                "bia_raw": _get("bia"),
                "citation": f"Sheet '{sname}', Row {row_num}",
            })
    return items


def record_evidence_item(
    project_id: str,
    *,
    entity_id: Optional[str],
    document_id: Optional[str],
    req_id: Optional[str],
    claim_text: str,
    source_quote: str,
    source_location: str,
    status: str = "DOCUMENT_CLAIM",
) -> str:
    """Persist a quote-backed evidence row in the v2 evidence_items ledger (idempotent on quote/req)."""
    ev_id = str(uuid.uuid4())
    with get_db() as conn:
        existing = conn.execute(
            """SELECT id, status FROM evidence_items
               WHERE project_id = ? AND COALESCE(req_id, '') = COALESCE(?, '') AND lower(source_quote) = lower(?)""",
            (project_id, req_id, source_quote[:260]),
        ).fetchone()
        if existing:
            if existing["status"] != status:
                conn.execute("UPDATE evidence_items SET status = ? WHERE id = ?", (status, existing["id"]))
            return existing["id"]

        conn.execute(
            """INSERT INTO evidence_items
               (id, project_id, entity_id, document_id, req_id, claim_text, source_quote, source_location, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ev_id, project_id, entity_id, document_id, req_id, claim_text, source_quote[:260], source_location, status),
        )
    return ev_id


def extract_handbook_evidence(
    project_id: str,
    text: str,
    filename: str,
    doc_type: str = "FORMAL_POLICY",
    document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan handbook/policy/AGB/SBOM text with negation awareness and exact verbatim quotes.

    - Negated rules ("MFA is currently not enforced") -> status='CONTRADICTED', answer='NO'
    - Partial rules -> status='DOCUMENT_CLAIM', answer='PARTIAL'
    - Affirmative claims -> status='DOCUMENT_CLAIM', answer='PARTIAL' (never auto-marked COMPLIANT
      without operational confirmation!)
    """
    if not (text or "").strip():
        return {"entities_touched": 0, "assessments_proven": 0, "citations": [], "contradictions": []}

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[\.\!\?\n])\s+|\n+", text.replace("\r", ""))
        if len(s.strip()) >= 10
    ]

    with get_db() as conn:
        existing_procs = conn.execute(
            "SELECT * FROM org_entities WHERE project_id = ? AND entity_type = 'PROCESS' ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()

    if existing_procs:
        anchor_entity = dict(existing_procs[0])
    else:
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
    contradictions: List[Dict[str, Any]] = []

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
            record_evidence_item(
                project_id,
                entity_id=anchor_entity["id"],
                document_id=document_id,
                req_id="R003",
                claim_text=f"Owner role stated as {role_name}",
                source_quote=line.strip()[:220],
                source_location=f"{filename}:L{line_no}",
                status="DOCUMENT_CLAIM",
            )
            entities_touched += 1
            assessments_proven += 1
            citations.append({"req_id": "R003", "quote": line.strip()[:220], "source": f"{filename}:L{line_no}", "status": "DOCUMENT_CLAIM"})

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
                record_evidence_item(
                    project_id,
                    entity_id=anchor_entity["id"],
                    document_id=document_id,
                    req_id="R017",
                    claim_text=f"MTPD stated as {mtpd_val:g} hours",
                    source_quote=line.strip()[:220],
                    source_location=f"{filename}:L{line_no}",
                    status="DOCUMENT_CLAIM",
                )
                assessments_proven += 1
            if rto_val is not None and mtpd_val is not None:
                EntityService.save_entity_assessment(
                    project_id, anchor_entity["id"], "R018",
                    answer_value=f"{rto_val:g} h", answer_numeric=rto_val,
                    evidence=ev, context={"rto": rto_val, "mtpd": mtpd_val},
                    answered_by="document_extraction",
                )
                record_evidence_item(
                    project_id,
                    entity_id=anchor_entity["id"],
                    document_id=document_id,
                    req_id="R018",
                    claim_text=f"RTO stated as {rto_val:g}h vs MTPD {mtpd_val:g}h",
                    source_quote=line.strip()[:220],
                    source_location=f"{filename}:L{line_no}",
                    status="CONTRADICTED" if rto_val >= mtpd_val else "DOCUMENT_CLAIM",
                )
                assessments_proven += 1

    # Negation-safe requirement extraction across sentences
    # Track strongest polarity per req_id (NEGATED beats PARTIAL/AFFIRMATIVE so contradictions are never hidden!)
    matched_by_req: Dict[str, Tuple[str, str, str, int]] = {}
    for sent_idx, sent in enumerate(sentences, start=1):
        polarity = classify_sentence_polarity(sent)
        for req_id, pattern, claim_label in _HANDBOOK_REQ_RULES:
            if pattern.search(sent):
                prev = matched_by_req.get(req_id)
                if prev is None or (polarity == "NEGATED" and prev[0] != "NEGATED"):
                    matched_by_req[req_id] = (polarity, sent, claim_label, sent_idx)

    for req_id, (polarity, sent, claim_label, sent_idx) in matched_by_req.items():
        loc = f"{filename}:S{sent_idx}"
        ev = [{"source": filename, "location": loc, "quote": sent[:240], "polarity": polarity}]
        if polarity == "NEGATED":
            ev_status = "CONTRADICTED"
            ans_val = "NO"
            just = f"Negated/missing control reported in {filename}: \"{sent[:180]}\""
            contradictions.append({"req_id": req_id, "claim": claim_label, "quote": sent[:240], "source": loc})
        elif polarity == "PARTIAL":
            ev_status = "DOCUMENT_CLAIM"
            ans_val = "PARTIAL"
            just = f"Partial/conditional implementation noted in {filename}: \"{sent[:180]}\""
        else:
            ev_status = "DOCUMENT_CLAIM"
            ans_val = "PARTIAL"
            just = f"Document claim (awaiting confirmation against daily practice) in {filename}: \"{sent[:180]}\""

        EntityService.save_entity_assessment(
            project_id,
            anchor_entity["id"],
            req_id,
            answer_value=ans_val,
            justification=just,
            evidence=ev,
            answered_by="document_extraction",
        )
        record_evidence_item(
            project_id,
            entity_id=anchor_entity["id"],
            document_id=document_id,
            req_id=req_id,
            claim_text=claim_label,
            source_quote=sent[:240],
            source_location=loc,
            status=ev_status,
        )
        assessments_proven += 1
        citations.append({"req_id": req_id, "quote": sent[:200], "source": loc, "status": ev_status})

    logger.info(
        "Handbook evidence extracted file=%s doc_type=%s entities=%d assessments=%d contradictions=%d",
        filename,
        doc_type,
        entities_touched,
        assessments_proven,
        len(contradictions),
    )
    return {
        "anchor_entity_id": anchor_entity["id"],
        "entities_touched": entities_touched,
        "assessments_proven": assessments_proven,
        "citations": citations,
        "contradictions": contradictions,
    }
