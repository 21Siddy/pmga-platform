"""Unified PMGA v2 workspace hydration, DMN evaluation, gap projection, and maturity tracking.

Shared by both the LangGraph multi-session orchestrator (`workflow_graph.py`) and
direct canvas/UI endpoints (`PUT /processes/{process_id}/bpmn`, `PATCH /gaps/{gap_id}`).
Ensures one single source of truth across SQLite tables, BPMN IR, and DMN rules.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db
from app.errors import NotFoundError, ValidationError
from app.services.bpmn_ir_service import (
    BPMNFlow,
    BPMNProcessIR,
    BPMNStep,
    compile_ir_to_bpmn_xml,
    describe_bpmn_for_user,
    parse_bpmn_xml_to_ir,
)
from app.services.decision_engine import DecisionEngine
from app.services.entity_service import EntityService
from app.services.llm_client import LLMClient
from app.services.mapping_verifier import CIA_LEVELS, STATUS_CRITICAL_GAP, evaluate_rule

logger = logging.getLogger(__name__)

VALID_GAP_STATUSES = ("OPEN", "IN_PROGRESS", "ACCEPTED_RISK", "RESOLVED")

_CURATED_CONTROL_PLAYBOOKS: Dict[str, Dict[str, Any]] = {
    "R002": {
        "warm_summary": "Link this IT system to the business processes it supports so protection needs inherit cleanly.",
        "action_steps": [
            "Confirm which department processes depend on this system during daily operations.",
            "Record the link so BSI 200-2 Maximumprinzip automatically sets the system's target protection level.",
        ],
        "roles": ["Process Owner", "IT Application Owner"],
        "compensating": [
            "If full application mapping is still underway, assign an interim departmental owner and log the top 1–2 primary processes."
        ],
    },
    "R003": {
        "warm_summary": "Assign a clear organizational owner or team responsible for keeping this workflow or system safe.",
        "action_steps": [
            "Name a responsible department role (not just an individual) as owner.",
            "Confirm who approves changes and handles missing citizen inputs.",
        ],
        "roles": ["Department Lead", "Process Owner"],
        "compensating": [
            "Designate the team lead or shared functional mailbox as temporary steward until formal role sign-off."
        ],
    },
    "R018": {
        "warm_summary": "Your target recovery time (RTO) is equal to or slower than the maximum outage your team can tolerate (MTPD).",
        "action_steps": [
            "Define a manual paper/offline fallback procedure so essential service continues before MTPD is reached.",
            "Verify backup restore speed and vendor SLA so technical RTO stays strictly below MTPD.",
        ],
        "roles": ["Business Continuity Coordinator", "IT Operations Lead", "Process Owner"],
        "compensating": [
            "Keep printed emergency intake forms and offline signature checklists ready so clerks can bridge short outages without losing citizen requests."
        ],
    },
    "R021": {
        "warm_summary": "High-availability workflows need a practical emergency fallback when systems are unreachable.",
        "action_steps": [
            "Write a 1-page emergency workaround checklist (who switches to paper/offline, how backlog is re-entered).",
            "Walk through the fallback once a year with frontline staff.",
        ],
        "roles": ["Process Owner", "Department Staff", "IT Support"],
        "compensating": [
            "Use pre-numbered paper intake vouchers with end-of-day batch entry once the system returns."
        ],
    },
    "R054": {
        "warm_summary": "Citizen or sensitive records appear to move via regular email attachments or unencrypted removable media.",
        "action_steps": [
            "Provide an encrypted upload link or municipal file-exchange portal for incoming citizen/contractor files.",
            "Phase out plain email attachments and unencrypted USB sticks for sensitive case files.",
        ],
        "roles": ["Data Protection Officer (DPO)", "IT Infrastructure Lead", "Department Clerks"],
        "compensating": [
            "Until the portal supports larger files, use password-protected AES-256 archives (7-Zip/encrypted PDF) and share the passcode via a separate channel (SMS/phone)."
        ],
    },
    "R059": {
        "warm_summary": "External vendor or cloud dependencies need clear security rules and supervised remote access.",
        "action_steps": [
            "Ensure vendor support connects only through time-boxed, ticket-approved sessions (no always-on remote tunnels).",
            "Verify data processing agreements (AVV / GDPR Art. 28) and incident notification contacts are current.",
        ],
        "roles": ["IT Security Officer", "Procurement / Legal", "Vendor Account Lead"],
        "compensating": [
            "Require an internal staff member to enable remote access on demand and disable it immediately after maintenance."
        ],
    },
    "R074": {
        "warm_summary": "Personal citizen or employee data is processed here and needs clear GDPR VVT & minimization documentation.",
        "action_steps": [
            "Document the processing purpose, legal basis, and retention/deletion schedule in the Records of Processing Activities (VVT).",
            "Ensure sensitive fields are restricted to staff who actively need them.",
        ],
        "roles": ["Data Protection Officer (DPO)", "Process Owner"],
        "compensating": [
            "Mask or minimize optional personal fields on intake templates while formal VVT updates are finalized."
        ],
    },
    "R101": {
        "warm_summary": "Shared or informal administrative accounts make it impossible to tell who changed critical settings.",
        "action_steps": [
            "Issue named personal admin accounts to each administrator instead of shared logins.",
            "Store break-glass emergency credentials in an audited vault with dual-control notification.",
        ],
        "roles": ["IT Systems Lead", "Information Security Officer"],
        "compensating": [
            "If a legacy tool only supports one local admin login, place it behind a monitored jump host or password checkout log recording who checked it out and when."
        ],
    },
    "R104": {
        "warm_summary": "Multi-factor authentication (MFA) is missing or not yet enforced for administrative or remote access.",
        "action_steps": [
            "Enable MFA (FIDO2 hardware key or authenticator app) on administrative and remote logins first.",
            "Reconcile written policy with live system settings so no accounts bypass MFA silently.",
        ],
        "roles": ["IT Security Officer", "Identity/AD Admin"],
        "compensating": [
            "For staff without work smartphones, issue low-cost USB FIDO2 security keys or restrict admin logins strictly to locked internal management VLANs."
        ],
    },
    "R107": {
        "warm_summary": "Legacy software or unpatched components need structured vulnerability tracking or isolation.",
        "action_steps": [
            "Maintain a simple inventory of software versions/SBOMs and subscribe to vendor security advisories.",
            "Apply critical security patches on a defined schedule or isolate legacy tools from general office networks.",
        ],
        "roles": ["IT Operations", "Application Owner"],
        "compensating": [
            "Place legacy applications in a segmented network zone with strict firewall rules and read-only backups if vendor patches are delayed."
        ],
    },
    "R112": {
        "warm_summary": "High-integrity data requires regular, tested backups so you know recovery actually works.",
        "action_steps": [
            "Schedule automated daily encrypted backups with at least one offline/immutable copy.",
            "Perform and log a quarterly restore drill on sample records.",
        ],
        "roles": ["IT Infrastructure Lead", "Database Admin"],
        "compensating": [
            "Export nightly signed snapshots to write-protected municipal storage until automated immutable backups are provisioned."
        ],
    },
    "R114": {
        "warm_summary": "Key approvals and administrative changes should leave a reliable audit trail.",
        "action_steps": [
            "Enable audit logging for logins, permission changes, and record approvals.",
            "Forward security logs to central retention where normal staff cannot alter them.",
        ],
        "roles": ["IT Security Officer", "Systems Admin"],
        "compensating": [
            "Maintain signed change/approval tickets in the DMS for manual or legacy system changes lacking built-in logs."
        ],
    },
    "R123": {
        "warm_summary": "High-confidentiality data needs verified encryption in transit and at rest.",
        "action_steps": [
            "Enforce TLS 1.2+ / 1.3 for all web portals and internal APIs.",
            "Verify database/file-share encryption at rest and key management responsibilities.",
        ],
        "roles": ["IT Infrastructure Lead", "DPO"],
        "compensating": [
            "Use encrypted VPN tunnels or AES-256 containers when transferring files across legacy internal shares."
        ],
    },
}


def get_project_or_404(project_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Project '{project_id}' not found.")
        return dict(row)


def get_brand_profile(project_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM brand_profiles WHERE project_id = ?", (project_id,)).fetchone()
        if row:
            return dict(row)
    return {
        "project_id": project_id,
        "org_name": "Municipal Administration",
        "logo_data_uri": None,
        "primary_color": "#1E4E68",
        "accent_color": "#C86D44",
        "font_family": "Plus Jakarta Sans",
        "footer_text": "Prepared with PMGA Resilience & Process Workbench",
    }


def upsert_process_model(
    project_id: str,
    *,
    title: str,
    ir: BPMNProcessIR,
    bpmn_xml: str,
    entity_id: Optional[str] = None,
    description_text: Optional[str] = None,
    process_model_id: Optional[str] = None,
    is_primary: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create or update a distinct process_model without overwriting unrelated processes."""
    clean_title = (title or ir.process_title or "Business Process").strip()[:120]
    ir_json_str = ir.model_dump_json()
    auto_desc = description_text if description_text is not None else describe_bpmn_for_user(ir).get("plain_english_description", "")

    with get_db() as conn:
        existing = None
        if process_model_id:
            existing = conn.execute(
                "SELECT * FROM process_models WHERE id = ? AND project_id = ?",
                (process_model_id, project_id),
            ).fetchone()
        if not existing and entity_id:
            existing = conn.execute(
                "SELECT * FROM process_models WHERE project_id = ? AND entity_id = ?",
                (project_id, entity_id),
            ).fetchone()
        if not existing:
            existing = conn.execute(
                "SELECT * FROM process_models WHERE project_id = ? AND lower(title) = lower(?)",
                (project_id, clean_title),
            ).fetchone()

        if existing:
            new_ver = int(existing["version"] or 1) + 1
            new_desc = description_text if description_text is not None else auto_desc
            new_ent = entity_id or existing["entity_id"]
            prim = int(is_primary) if is_primary is not None else int(existing["is_primary"] or 0)
            conn.execute(
                """UPDATE process_models
                   SET title = ?, entity_id = ?, version = ?, ir_json = ?, bpmn_xml = ?,
                       description_text = ?, is_primary = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (clean_title, new_ent, new_ver, ir_json_str, bpmn_xml, new_desc, prim, existing["id"]),
            )
            row = conn.execute("SELECT * FROM process_models WHERE id = ?", (existing["id"],)).fetchone()
            return dict(row)

        existing_count = conn.execute(
            "SELECT COUNT(*) AS c FROM process_models WHERE project_id = ?",
            (project_id,),
        ).fetchone()["c"]
        prim = 1 if (is_primary is True or (is_primary is None and existing_count == 0)) else 0
        new_id = process_model_id or str(uuid.uuid4())
        conn.execute(
            """INSERT INTO process_models
               (id, project_id, entity_id, title, version, ir_json, bpmn_xml, description_text, is_primary)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (new_id, project_id, entity_id, clean_title, ir_json_str, bpmn_xml, auto_desc, prim),
        )
        row = conn.execute("SELECT * FROM process_models WHERE id = ?", (new_id,)).fetchone()
        return dict(row)


def _detect_context_cues(project_id: str, structure: Dict[str, Any], process_models: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gather deterministic risk & operational cues across documents, entities, survey rows, and BPMN steps."""
    with get_db() as conn:
        docs = conn.execute(
            "SELECT doc_type, redacted_text, extracted_text FROM documents WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        survey_rows = conn.execute(
            "SELECT * FROM process_survey_rows WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        turns = conn.execute(
            "SELECT content FROM session_turns WHERE project_id = ? AND role = 'user'",
            (project_id,),
        ).fetchall()
        evidence = conn.execute(
            "SELECT * FROM evidence_items WHERE project_id = ?",
            (project_id,),
        ).fetchall()

    corpus_parts: List[str] = []
    for d in docs:
        corpus_parts.append(d["redacted_text"] or d["extracted_text"] or "")
    for sr in survey_rows:
        for k in ("q01_raw_entry", "q07_lived_reality_notes", "q08_input_data_mandatory", "q10_output_data", "q11_storage_location", "q12_exception_path"):
            if sr[k]:
                corpus_parts.append(str(sr[k]))
    for t in turns:
        corpus_parts.append(t["content"] or "")
    for pm in process_models:
        if pm.get("description_text"):
            corpus_parts.append(pm["description_text"])
        try:
            ir = BPMNProcessIR.model_validate_json(pm["ir_json"])
            for s in ir.steps:
                corpus_parts.append(s.name)
                if s.system_name:
                    corpus_parts.append(s.system_name)
        except Exception:
            pass

    all_text = "\n".join(corpus_parts).lower()

    uses_email_or_usb = bool(re.search(r"\b(email|e-mail|attachment|anhang|usb|stick|dvd|unencrypted|unverschlüsselt)\b", all_text))
    uses_shared_account = bool(
        re.search(r"\b(shared\s+(?:admin|account|login|pwd)|no\s+mfa|without\s+mfa|ohne\s+mfa|mfa\s+(?:is\s+)?(?:currently\s+)?not\s+enforced|whiteboard|keepass|svc_muni_admin|geteiltes?\s+konto)\b", all_text)
    )
    has_external_vendor = bool(re.search(r"\b(vendor|supplier|contractor|anbieter|dienstleister|anydesk|saas|cloud|external|extern)\b", all_text))
    has_personal_data = bool(
        re.search(r"\b(citizen|bürger|applicant|antrag|personal|employee|mitarbeiter|staff|anmeld|payroll|gehalt|hr|tax|iban|ausweis|address|adresse|gdpr|dsgvo|pii|personenbezogen)\b", all_text)
    ) or any(e["entity_type"] == "DATA_ASSET" for e in structure.get("entities", []))
    is_legacy_system = bool(re.search(r"\b(legacy|alt-?system|outdated|cve|sbom|end-of-life|windows\s+7|on-?prem\s+old)\b", all_text))
    has_fallback_path = bool(re.search(r"\b(notfallplan|emergency\s+plan|fallback|notbetrieb|manual\s+backup|wiederanlaufplan)\b", all_text)) and not bool(
        re.search(r"\b(no\s+(?:emergency|fallback)|kein\s+notfallplan|without\s+fallback)\b", all_text)
    )

    return {
        "uses_email_or_usb": uses_email_or_usb,
        "uses_shared_account": uses_shared_account,
        "has_external_vendor": has_external_vendor,
        "has_personal_data": has_personal_data,
        "is_legacy_system": is_legacy_system,
        "has_fallback_path": has_fallback_path,
        "evidence_rows": [dict(e) for e in evidence],
        "survey_rows": [dict(r) for r in survey_rows],
    }


def _match_affected_nodes(
    ir: Optional[BPMNProcessIR],
    req_id: str,
    entity_name: str,
    gap_category: str,
) -> List[str]:
    """Deterministically bind a gap to relevant BPMN step IDs inside a process model."""
    if not ir or not ir.steps:
        return []

    non_boundary = [s for s in ir.steps if s.type not in ("startEvent", "endEvent")]
    if not non_boundary:
        return [ir.steps[0].id]

    kw_map = {
        "R054": ("email", "mail", "usb", "attachment", "upload", "send", "versend", "transfer", "portal", "blueprints"),
        "R101": ("admin", "shared", "login", "account", "access", "berecht", "clone", "anmeld"),
        "R104": ("mfa", "login", "auth", "admin", "portal", "anmeld", "anydesk", "access"),
        "R059": ("vendor", "external", "anbieter", "anydesk", "remote", "scada", "cloud", "supplier"),
        "R074": ("citizen", "antrag", "applicant", "personal", "data", "hr", "register", "erfassen", "form"),
        "R018": ("recover", "restore", "backup", "system", "voucher", "permit", "anmeld", "gutschein"),
        "R021": ("emergency", "notfall", "manual", "system", "service"),
        "R107": ("legacy", "system", "patch", "sbom", "server", "db"),
    }
    hints = kw_map.get(req_id, ())
    ename_low = (entity_name or "").lower()

    matched: List[str] = []
    for step in non_boundary:
        hay = " ".join(filter(None, [step.name, step.system_name, step.lane, step.risk_annotation])).lower()
        if ename_low and ename_low in hay:
            matched.append(step.id)
        elif hints and any(h in hay for h in hints):
            matched.append(step.id)

    if not matched:
        # Fallback: bind to steps missing actor/system or first substantive task
        if req_id == "R003":
            unowned = [s.id for s in non_boundary if s.lane.lower() in ("general process", "default lane", "unassigned")]
            matched = unowned[:3] or [non_boundary[0].id]
        else:
            matched = [non_boundary[0].id]
    return matched[:5]


def recompute_gaps_and_agenda(project_id: str) -> Dict[str, Any]:
    """Run DMN 03_control_applicability, 04_gap_severity_and_signal, and 02_question_agenda over live project graph."""
    structure = EntityService.get_project_structure(project_id)
    entities = structure["entities"]
    deps = structure["dependencies"]

    with get_db() as conn:
        pm_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM process_models WHERE project_id = ? ORDER BY is_primary DESC, created_at ASC",
                (project_id,),
            ).fetchall()
        ]
        existing_gaps = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM remediation_gaps WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        ]

    # Preserve user-set status (IN_PROGRESS, ACCEPTED_RISK, RESOLVED) keyed by (entity_id, req_id)
    preserved_status: Dict[Tuple[Optional[str], str], str] = {
        (g["entity_id"], g["req_id"]): g["status"]
        for g in existing_gaps
        if g["status"] in ("IN_PROGRESS", "ACCEPTED_RISK", "RESOLVED")
    }

    cues = _detect_context_cues(project_id, structure, pm_rows)
    evidence_rows = cues["evidence_rows"]
    survey_rows = cues["survey_rows"]

    pm_by_entity: Dict[str, Tuple[Dict[str, Any], BPMNProcessIR]] = {}
    parsed_pms: List[Tuple[Dict[str, Any], BPMNProcessIR]] = []
    for pm in pm_rows:
        try:
            ir = BPMNProcessIR.model_validate_json(pm["ir_json"]) if pm.get("ir_json") and pm["ir_json"] != "{}" else parse_bpmn_xml_to_ir(pm["bpmn_xml"], fallback_title=pm["title"])
        except Exception as exc:
            logger.warning("Could not parse process_model %s IR/XML cleanly: %s", pm["id"], exc)
            ir = BPMNProcessIR(process_title=pm["title"])
        parsed_pms.append((pm, ir))
        if pm.get("entity_id"):
            pm_by_entity[pm["entity_id"]] = (pm, ir)

    # Helper: map system -> supported process entity ids
    supports_map: Dict[str, List[str]] = {}
    for d in deps:
        if d["dependency_type"] == "SUPPORTS":
            supports_map.setdefault(d["from_entity_id"], []).append(d["to_entity_id"])

    # Evidence lookup by req_id
    ev_by_req: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evidence_rows:
        if ev.get("req_id"):
            ev_by_req.setdefault(ev["req_id"], []).append(ev)

    generated_gaps: List[Dict[str, Any]] = []
    seen_gap_keys: Set[Tuple[Optional[str], str]] = set()

    # Clear gap_ids on all IRs before re-projecting
    for _pm, ir in parsed_pms:
        for step in ir.steps:
            step.gap_ids = []

    def _highest_cia(ent: Dict[str, Any]) -> str:
        decl = ent.get("declared_protection") or {}
        inh = ent.get("inherited_protection") or {}
        best = "Normal"
        best_rank = 1
        for src in (decl, inh):
            for k in ("confidentiality", "integrity", "availability"):
                v = src.get(k)
                if v in CIA_LEVELS and CIA_LEVELS[v] > best_rank:
                    best = v
                    best_rank = CIA_LEVELS[v]
        return best

    for ent in entities:
        if ent["entity_type"] not in ("PROCESS", "SUB_PROCESS", "SYSTEM"):
            continue
        decl = ent.get("declared_protection") or {}
        inh = ent.get("inherited_protection") or {}
        eff_c = inh.get("confidentiality") or decl.get("confidentiality") or "Normal"
        eff_i = inh.get("integrity") or decl.get("integrity") or "Normal"
        eff_a = inh.get("availability") or decl.get("availability") or "Normal"
        eff_rto = inh.get("rto") if inh.get("rto") is not None else decl.get("rto")
        eff_mtpd = inh.get("mtpd") if inh.get("mtpd") is not None else decl.get("mtpd")
        cia_max = _highest_cia(ent)

        applicable = DecisionEngine.evaluate(
            "03_control_applicability",
            {
                "entity_type": ent["entity_type"],
                "confidentiality": eff_c,
                "integrity": eff_i,
                "availability": eff_a,
                "has_personal_data": cues["has_personal_data"],
                "uses_email_or_usb": cues["uses_email_or_usb"],
                "uses_shared_account": cues["uses_shared_account"],
                "has_external_vendor": cues["has_external_vendor"],
                "is_legacy_system": cues["is_legacy_system"],
                "has_fallback_path": cues["has_fallback_path"],
                "rto_hours": eff_rto,
                "mtpd_hours": eff_mtpd,
            },
        )

        # Find target process_model(s) for node projection
        target_pms: List[Tuple[Dict[str, Any], BPMNProcessIR]] = []
        if ent["id"] in pm_by_entity:
            target_pms.append(pm_by_entity[ent["id"]])
        elif ent["entity_type"] == "SYSTEM":
            for proc_eid in supports_map.get(ent["id"], []):
                if proc_eid in pm_by_entity:
                    target_pms.append(pm_by_entity[proc_eid])
        if not target_pms and parsed_pms:
            target_pms.append(parsed_pms[0])

        assess_by_req = {a["req_id"]: a for a in ent.get("assessments", [])}

        for app_rule in applicable:
            req_id = app_rule["req_id"]
            if (ent["id"], req_id) in seen_gap_keys:
                continue

            # Determine rule_status & contradiction status
            rule_status = "NEEDS_USER_INPUT"
            policy_contradicted = False
            ev_status = ""
            policy_vs_reality = app_rule["trigger_reason"]

            if req_id == "R002" and ent["entity_type"] == "SYSTEM":
                linked = len(supports_map.get(ent["id"], []))
                eval_r = evaluate_rule("MUST_LINK_PROCESS", context={"links": linked})
                rule_status = eval_r["status"]
                if rule_status == "COMPLIANT":
                    continue  # System properly linked!
            elif req_id == "R003":
                eval_r = evaluate_rule("ROLE_ASSIGNED", context={"owner_role": ent.get("owner_role")})
                rule_status = eval_r["status"]
                if rule_status == "COMPLIANT":
                    continue
            elif req_id == "R018":
                rto_eval = evaluate_rule("RTO_LT_MTPD", context={"rto": eff_rto, "mtpd": eff_mtpd})
                rule_status = rto_eval["status"]
                if rule_status == "COMPLIANT":
                    continue
            else:
                # Check entity assessment + project-wide evidence items
                ent_assess = assess_by_req.get(req_id)
                req_evs = ev_by_req.get(req_id, [])
                if any(e["status"] == "CONTRADICTED" for e in req_evs) or (ent_assess and ent_assess["status"] == "CRITICAL_GAP"):
                    rule_status = "CRITICAL_GAP"
                    policy_contradicted = True
                    ev_status = "CONTRADICTED"
                    first_c = next((e for e in req_evs if e["status"] == "CONTRADICTED"), None)
                    if first_c:
                        policy_vs_reality = f"Contradicted in {first_c.get('source_location') or 'uploaded doc'}: \"{first_c['source_quote']}\""
                elif req_id in ("R101", "R104") and cues["uses_shared_account"]:
                    rule_status = "CRITICAL_GAP"
                    policy_contradicted = True
                    ev_status = "CONTRADICTED"
                elif req_id == "R054" and cues["uses_email_or_usb"]:
                    rule_status = "CRITICAL_GAP"
                    policy_contradicted = True
                    ev_status = "CONTRADICTED"
                elif req_id == "R059" and cues["has_external_vendor"] and not any(e["status"] == "USER_CONFIRMED" for e in req_evs):
                    rule_status = "PARTIAL_GAP"
                elif req_id == "R021" and not cues["has_fallback_path"]:
                    rule_status = "CRITICAL_GAP"
                elif any(e["status"] == "USER_CONFIRMED" for e in req_evs):
                    continue  # Confirmed safe!
                elif any(e["status"] == "DOCUMENT_CLAIM" for e in req_evs):
                    rule_status = "PARTIAL_GAP"
                    ev_status = "DOCUMENT_CLAIM"
                    first_d = req_evs[0]
                    policy_vs_reality = f"Document claim in {first_d.get('source_location') or 'policy'}: \"{first_d['source_quote']}\" (awaiting lived-reality check)"
                elif ent_assess and ent_assess["status"] == "COMPLIANT":
                    continue
                else:
                    rule_status = "PARTIAL_GAP"

            rto_violation = (req_id == "R018") or (eff_rto is not None and eff_mtpd is not None and eff_rto >= eff_mtpd)
            sev_out = DecisionEngine.evaluate(
                "04_gap_severity_and_signal",
                {
                    "rule_status": rule_status,
                    "inherited_cia_max": cia_max,
                    "rto_violation": rto_violation and req_id == "R018",
                    "policy_contradicted": policy_contradicted,
                    "evidence_status": ev_status,
                },
            )[0]

            playbook = _CURATED_CONTROL_PLAYBOOKS.get(req_id, {})
            warm_summary = (
                f"[{ent['name']}] {playbook.get('warm_summary') or app_rule['trigger_reason']}"
            )

            pm_id: Optional[str] = None
            affected_node_ids: List[str] = []
            if target_pms:
                pm_dict, ir = target_pms[0]
                pm_id = pm_dict["id"]
                affected_node_ids = _match_affected_nodes(ir, req_id, ent["name"], app_rule["gap_category"])

            gap_id = f"gap_{ent['id'][:8]}_{req_id}"
            for _pm_d, ir in target_pms:
                for step in ir.steps:
                    if step.id in affected_node_ids and gap_id not in step.gap_ids:
                        step.gap_ids.append(gap_id)

            seen_gap_keys.add((ent["id"], req_id))
            generated_gaps.append({
                "id": gap_id,
                "project_id": project_id,
                "process_model_id": pm_id,
                "entity_id": ent["id"],
                "req_id": req_id,
                "standard_clause": app_rule["standard_clause"],
                "affected_node_ids": affected_node_ids,
                "severity": sev_out["severity"],
                "priority_bucket": sev_out["priority_bucket"],
                "gap_category": app_rule["gap_category"],
                "warm_summary": f"{warm_summary} {sev_out['warm_tone_framing']}".strip(),
                "policy_vs_reality": policy_vs_reality,
                "action_steps": playbook.get("action_steps") or [f"Review {app_rule['control_title']} with {ent['name']} owner."],
                "roles_to_involve": playbook.get("roles") or [ent.get("owner_role") or "Process Owner", "IT Security Officer"],
                "compensating_options": playbook.get("compensating") or ["Document an interim manual control and review timeline."],
                "status": preserved_status.get((ent["id"], req_id), "OPEN"),
            })

    # Persist gaps & updated BPMN XML/IR with gap_ids back to SQLite
    with get_db() as conn:
        conn.execute("DELETE FROM remediation_gaps WHERE project_id = ?", (project_id,))
        for g in generated_gaps:
            conn.execute(
                """INSERT INTO remediation_gaps
                   (id, project_id, process_model_id, entity_id, req_id, standard_clause,
                    affected_node_ids_json, severity, priority_bucket, gap_category,
                    warm_summary, policy_vs_reality, action_steps_json,
                    roles_to_involve_json, compensating_options_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    g["id"],
                    project_id,
                    g["process_model_id"],
                    g["entity_id"],
                    g["req_id"],
                    g["standard_clause"],
                    json.dumps(g["affected_node_ids"]),
                    g["severity"],
                    g["priority_bucket"],
                    g["gap_category"],
                    g["warm_summary"],
                    g["policy_vs_reality"],
                    json.dumps(g["action_steps"], ensure_ascii=False),
                    json.dumps(g["roles_to_involve"], ensure_ascii=False),
                    json.dumps(g["compensating_options"], ensure_ascii=False),
                    g["status"],
                ),
            )
        for pm_dict, ir in parsed_pms:
            new_xml = compile_ir_to_bpmn_xml(ir)
            conn.execute(
                "UPDATE process_models SET ir_json = ?, bpmn_xml = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ir.model_dump_json(), new_xml, pm_dict["id"]),
            )

    # Compute live agenda inputs for 02_question_agenda.json
    unlinked_systems = [
        e for e in entities
        if e["entity_type"] == "SYSTEM" and not supports_map.get(e["id"])
    ]
    processes = [e for e in entities if e["entity_type"] in ("PROCESS", "SUB_PROCESS")]
    processes_with_pm = {pm_d.get("entity_id") for pm_d, _ in parsed_pms if pm_d.get("entity_id")}
    procs_no_steps = [
        p for p in processes
        if p["id"] not in processes_with_pm
        and not any(len([s for s in ir.steps if s.type not in ("startEvent", "endEvent")]) > 0 for _pm, ir in parsed_pms if _pm.get("title", "").lower() == p["name"].lower())
    ]

    steps_no_actors = 0
    steps_no_data = 0
    missing_exc = 0
    for _pm, ir in parsed_pms:
        task_steps = [s for s in ir.steps if s.type in ("userTask", "serviceTask", "callActivity")]
        steps_no_actors += sum(
            1 for s in task_steps if not s.lane or s.lane.lower() in ("general process", "default lane", "unassigned")
        )
        if task_steps and not ir.data_objects and not any(e["entity_type"] == "DATA_ASSET" for e in entities):
            steps_no_data += 1
        has_xor = any(s.type == "exclusiveGateway" for s in ir.steps)
        if task_steps and not has_xor:
            missing_exc += 1

    # Vague criticality: survey row has q14_raw_criticality without unpacked C/I/A assessments on target entity
    vague_crit_count = 0
    for sr in survey_rows:
        if sr.get("q14_raw_criticality") and not (sr.get("q15_cia_confidentiality") and sr.get("q16_cia_integrity") and sr.get("q17_cia_availability")):
            vague_crit_count += 1
    # Also check entities with no declared C/I/A when project has entities
    if vague_crit_count == 0 and processes:
        for p in processes:
            decl = p.get("declared_protection") or {}
            if not (decl.get("confidentiality") and decl.get("integrity") and decl.get("availability")):
                vague_crit_count += 1

    missing_bia_count = 0
    for p in processes:
        decl = p.get("declared_protection") or {}
        if decl.get("rto") is None or decl.get("mtpd") is None:
            missing_bia_count += 1

    unconfirmed_claims = sum(1 for e in evidence_rows if e["status"] == "DOCUMENT_CLAIM")
    open_high_risk = sum(1 for g in generated_gaps if g["status"] == "OPEN" and g["severity"] in ("CRITICAL", "HIGH"))

    agenda_ctx = {
        "unlinked_systems_count": len(unlinked_systems),
        "processes_without_steps": len(procs_no_steps),
        "steps_without_actors": steps_no_actors,
        "steps_without_data": steps_no_data,
        "missing_exception_branches": missing_exc,
        "vague_criticality_count": vague_crit_count,
        "missing_rto_mtpd_count": missing_bia_count,
        "unconfirmed_doc_claims": unconfirmed_claims,
        "open_high_risk_controls": open_high_risk,
    }
    raw_agenda = DecisionEngine.evaluate("02_question_agenda", agenda_ctx)

    # Enrich top agenda items with exact target entity / process name
    enriched_agenda: List[Dict[str, Any]] = []
    for item in raw_agenda:
        key = item["agenda_key"]
        target_eid = None
        target_name = None
        if key == "LINK_SYSTEM_TO_PROCESS" and unlinked_systems:
            target_eid = unlinked_systems[0]["id"]
            target_name = unlinked_systems[0]["name"]
        elif key == "ELICIT_PROCESS_STEPS" and procs_no_steps:
            target_eid = procs_no_steps[0]["id"]
            target_name = procs_no_steps[0]["name"]
        elif key in ("UNPACK_CIA_CRITICALITY", "CAPTURE_BIA_RTO_MTPD") and processes:
            target_eid = processes[0]["id"]
            target_name = processes[0]["name"]
        elif key == "VERIFY_DOCUMENT_CLAIMS":
            first_claim = next((e for e in evidence_rows if e["status"] == "DOCUMENT_CLAIM"), None)
            if first_claim:
                target_eid = first_claim.get("entity_id")
                target_name = first_claim.get("claim_text")
        enriched_agenda.append({
            **item,
            "target_entity_id": target_eid,
            "target_name": target_name or (parsed_pms[0][0]["title"] if parsed_pms else "Primary Workflow"),
        })

    maturity = compute_maturity_progress(entities, parsed_pms, agenda_ctx, generated_gaps)
    return {
        "gaps": generated_gaps,
        "agenda_queue": enriched_agenda,
        "agenda_metrics": agenda_ctx,
        "maturity_progress": maturity,
    }


def compute_maturity_progress(
    entities: List[Dict[str, Any]],
    parsed_pms: List[Tuple[Dict[str, Any], BPMNProcessIR]],
    metrics: Dict[str, int],
    gaps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute the 5-stage maturity strip shown in the two-pane UI."""
    has_scope = len(entities) > 0 and metrics["unlinked_systems_count"] == 0
    has_flow = len(parsed_pms) > 0 and metrics["processes_without_steps"] == 0 and metrics["steps_without_actors"] == 0
    has_reality = len(parsed_pms) > 0 and metrics["missing_exception_branches"] == 0 and metrics["unconfirmed_doc_claims"] == 0
    has_protection = len(entities) > 0 and metrics["vague_criticality_count"] == 0 and metrics["missing_rto_mtpd_count"] == 0
    has_plan = len(parsed_pms) > 0 and (len(gaps) == 0 or all(g["status"] != "OPEN" for g in gaps))

    stages = [
        {"id": "1_SCOPE_HIERARCHY", "label": "Scope & Hierarchy", "done": has_scope},
        {"id": "2_FLOW_ACTORS_DATA", "label": "Flow, Actors & Data", "done": has_flow},
        {"id": "3_REALITY_EXCEPTIONS", "label": "Reality & Exceptions", "done": has_reality},
        {"id": "4_PROTECTION_BIA", "label": "Protection & BIA", "done": has_protection},
        {"id": "5_REMEDIATION_PLAN", "label": "Remediation Plan", "done": has_plan},
    ]
    done_count = sum(1 for s in stages if s["done"])
    # Partial credit if entities or diagrams exist
    partial_bonus = 10 if (entities or parsed_pms) and done_count == 0 else 0
    overall = min(100, done_count * 20 + partial_bonus)
    current = next((s["id"] for s in stages if not s["done"]), "5_REMEDIATION_PLAN")
    return {
        "stages": stages,
        "done_count": done_count,
        "overall_percent": overall,
        "current_stage": current,
    }


def sync_bpmn_canvas_to_project(
    project_id: str,
    process_id: str,
    bpmn_xml: str,
    *,
    extract_description: bool = True,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Bidirectional canvas save: parses XML -> IR, extracts description, syncs entities, re-runs DMN gaps."""
    get_project_or_404(project_id)
    with get_db() as conn:
        existing_pm = conn.execute(
            "SELECT * FROM process_models WHERE id = ? AND project_id = ?",
            (process_id, project_id),
        ).fetchone()
    if not existing_pm:
        raise NotFoundError(f"Process model '{process_id}' not found in project '{project_id}'.")

    ir = parse_bpmn_xml_to_ir(bpmn_xml, fallback_title=existing_pm["title"])
    desc_info = describe_bpmn_for_user(
        ir,
        api_key=api_key,
        provider=provider,
        model_name=model_name,
        base_url=base_url,
    ) if extract_description else {
        "process_title": ir.process_title,
        "plain_english_description": existing_pm["description_text"] or "",
        "actors": ir.lanes,
        "systems": sorted({s.system_name for s in ir.steps if s.system_name}),
        "data_assets": [d.name for d in ir.data_objects],
        "missing_metadata": [],
        "llm_enhanced": False,
    }

    # Ensure matching PROCESS entity & sync actors/systems/data
    proc_ent = EntityService.upsert_entity(
        project_id=project_id,
        entity_type="PROCESS",
        name=ir.process_title or existing_pm["title"],
        description=desc_info["plain_english_description"] or None,
    )
    for actor in desc_info.get("actors", []):
        clean_actor = actor.replace(" (External)", "").strip()
        if clean_actor and clean_actor.lower() not in ("general process", "default lane"):
            role_ent = EntityService.upsert_entity(project_id, "ROLE", clean_actor)
            EntityService.upsert_dependency(project_id, proc_ent["id"], role_ent["id"], "EXECUTED_BY")
    for sys_name in desc_info.get("systems", []):
        sys_ent = EntityService.upsert_entity(project_id, "SYSTEM", sys_name)
        EntityService.upsert_dependency(project_id, sys_ent["id"], proc_ent["id"], "SUPPORTS")
    for dobj in ir.data_objects:
        d_ent = EntityService.upsert_entity(project_id, "DATA_ASSET", dobj.name)
        EntityService.upsert_dependency(project_id, proc_ent["id"], d_ent["id"], "USES_DATA")

    updated_pm = upsert_process_model(
        project_id,
        title=ir.process_title or existing_pm["title"],
        ir=ir,
        bpmn_xml=compile_ir_to_bpmn_xml(ir),
        entity_id=proc_ent["id"],
        description_text=desc_info["plain_english_description"],
        process_model_id=process_id,
    )

    recompute = recompute_gaps_and_agenda(project_id)
    workspace = get_workspace_state(project_id)
    return {
        "process_model": next((p for p in workspace["process_models"] if p["id"] == updated_pm["id"]), updated_pm),
        "description": desc_info,
        "recompute": recompute,
        "workspace": workspace,
    }


def update_gap_status(project_id: str, gap_id: str, new_status: str) -> Dict[str, Any]:
    clean_status = (new_status or "").strip().upper()
    if clean_status not in VALID_GAP_STATUSES:
        raise ValidationError(f"Unsupported gap status '{new_status}'. Must be one of {VALID_GAP_STATUSES}.")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM remediation_gaps WHERE id = ? AND project_id = ?",
            (gap_id, project_id),
        ).fetchone()
        if not row:
            raise NotFoundError(f"Remediation gap '{gap_id}' not found in project '{project_id}'.")
        conn.execute(
            "UPDATE remediation_gaps SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (clean_status, gap_id),
        )
        updated = conn.execute("SELECT * FROM remediation_gaps WHERE id = ?", (gap_id,)).fetchone()
    return _decode_gap_row(dict(updated))


def _decode_gap_row(g: Dict[str, Any]) -> Dict[str, Any]:
    g["affected_node_ids"] = json.loads(g.get("affected_node_ids_json") or "[]")
    g["action_steps"] = json.loads(g.get("action_steps_json") or "[]")
    g["roles_to_involve"] = json.loads(g.get("roles_to_involve_json") or "[]")
    g["compensating_options"] = json.loads(g.get("compensating_options_json") or "[]")
    return g


def get_workspace_state(project_id: str) -> Dict[str, Any]:
    """Hydrate the single-source-of-truth workspace payload for `/api/v1/projects/{project_id}/workspace`."""
    proj = get_project_or_404(project_id)
    structure = EntityService.get_project_structure(project_id)

    with get_db() as conn:
        turn_rows = conn.execute(
            "SELECT * FROM session_turns WHERE project_id = ? ORDER BY created_at ASC, rowid ASC",
            (project_id,),
        ).fetchall()
        pm_rows = conn.execute(
            "SELECT * FROM process_models WHERE project_id = ? ORDER BY is_primary DESC, created_at ASC",
            (project_id,),
        ).fetchall()
        ev_rows = conn.execute(
            "SELECT * FROM evidence_items WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        gap_rows = conn.execute(
            "SELECT * FROM remediation_gaps WHERE project_id = ? ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MODERATE' THEN 3 ELSE 4 END, created_at ASC",
            (project_id,),
        ).fetchall()
        doc_rows = conn.execute(
            "SELECT id, filename, doc_type, created_at FROM documents WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()

    turns = []
    for t in turn_rows:
        td = dict(t)
        try:
            td["patch_summary"] = json.loads(td.get("patch_summary_json") or "{}")
        except json.JSONDecodeError:
            td["patch_summary"] = {}
        turns.append(td)

    gaps = [_decode_gap_row(dict(g)) for g in gap_rows]
    gaps_by_pm: Dict[str, int] = {}
    for g in gaps:
        if g.get("process_model_id") and g["status"] != "RESOLVED":
            gaps_by_pm[g["process_model_id"]] = gaps_by_pm.get(g["process_model_id"], 0) + 1

    process_models = []
    parsed_pms: List[Tuple[Dict[str, Any], BPMNProcessIR]] = []
    for pm in pm_rows:
        pmd = dict(pm)
        try:
            ir = BPMNProcessIR.model_validate_json(pmd["ir_json"]) if pmd.get("ir_json") and pmd["ir_json"] != "{}" else parse_bpmn_xml_to_ir(pmd["bpmn_xml"], fallback_title=pmd["title"])
        except Exception:
            ir = BPMNProcessIR(process_title=pmd["title"])
        parsed_pms.append((pmd, ir))
        pmd["ir"] = ir.model_dump()
        pmd["open_gap_count"] = gaps_by_pm.get(pmd["id"], 0)
        process_models.append(pmd)

    # Lightweight agenda/maturity check without mutating DB on read
    supports_map: Dict[str, List[str]] = {}
    for d in structure["dependencies"]:
        if d["dependency_type"] == "SUPPORTS":
            supports_map.setdefault(d["from_entity_id"], []).append(d["to_entity_id"])
    unlinked_systems = [e for e in structure["entities"] if e["entity_type"] == "SYSTEM" and not supports_map.get(e["id"])]
    processes = [e for e in structure["entities"] if e["entity_type"] in ("PROCESS", "SUB_PROCESS")]
    processes_with_pm = {pm_d.get("entity_id") for pm_d, _ in parsed_pms if pm_d.get("entity_id")}
    pm_titles_with_steps = {
        pm_d.get("title", "").lower()
        for pm_d, ir in parsed_pms
        if any(s.type not in ("startEvent", "endEvent") for s in ir.steps)
    }
    procs_no_steps = [
        p for p in processes
        if p["id"] not in processes_with_pm and p["name"].lower() not in pm_titles_with_steps
    ]

    steps_no_actors = 0
    steps_no_data = 0
    missing_exc = 0
    for _pm, ir in parsed_pms:
        task_steps = [s for s in ir.steps if s.type in ("userTask", "serviceTask", "callActivity")]
        steps_no_actors += sum(1 for s in task_steps if not s.lane or s.lane.lower() in ("general process", "default lane", "unassigned"))
        if task_steps and not ir.data_objects and not any(e["entity_type"] == "DATA_ASSET" for e in structure["entities"]):
            steps_no_data += 1
        if task_steps and not any(s.type == "exclusiveGateway" for s in ir.steps):
            missing_exc += 1

    ev_list = [dict(e) for e in ev_rows]
    vague_crit = sum(
        1 for p in processes
        if not ((p.get("declared_protection") or {}).get("confidentiality") and (p.get("declared_protection") or {}).get("integrity") and (p.get("declared_protection") or {}).get("availability"))
    )
    missing_bia = sum(
        1 for p in processes
        if (p.get("declared_protection") or {}).get("rto") is None or (p.get("declared_protection") or {}).get("mtpd") is None
    )
    agenda_metrics = {
        "unlinked_systems_count": len(unlinked_systems),
        "processes_without_steps": len(procs_no_steps),
        "steps_without_actors": steps_no_actors,
        "steps_without_data": steps_no_data,
        "missing_exception_branches": missing_exc,
        "vague_criticality_count": vague_crit,
        "missing_rto_mtpd_count": missing_bia,
        "unconfirmed_doc_claims": sum(1 for e in ev_list if e["status"] == "DOCUMENT_CLAIM"),
        "open_high_risk_controls": sum(1 for g in gaps if g["status"] == "OPEN" and g["severity"] in ("CRITICAL", "HIGH")),
    }
    agenda_queue = DecisionEngine.evaluate("02_question_agenda", agenda_metrics)
    maturity_progress = compute_maturity_progress(structure["entities"], parsed_pms, agenda_metrics, gaps)

    return {
        "project": proj,
        "documents": [dict(d) for d in doc_rows],
        "session_turns": turns,
        "process_models": process_models,
        "structure": structure,
        "evidence_items": ev_list,
        "remediation_gaps": gaps,
        "agenda_queue": agenda_queue,
        "maturity_progress": maturity_progress,
        "brand_profile": get_brand_profile(project_id),
        "llm_status": LLMClient.get_provider_status(),
    }
