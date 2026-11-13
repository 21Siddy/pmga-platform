"""Pythonic AST Intermediate Representation (IR) & Deterministic Plothole Engine.

Translates process survey rows and entity graphs into a compact Pythonic
control-flow IR, parses it via ``ast.parse``, and detects structural,
dataflow, and compliance plotholes deterministically before rendering.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Set

from app.services.entity_service import EntityService
from app.services.mapping_verifier import STATUS_CRITICAL_GAP, evaluate_rule


def _slug(name: str, fallback: str = "step") -> str:
    cleaned = re.sub(r"[^\w]+", "_", (name or "").strip(), flags=re.UNICODE).strip("_").lower()
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}" if cleaned else fallback
    return cleaned[:40]


def compile_survey_to_python_ir(rows: List[Dict[str, Any]]) -> str:
    """Emit a deterministic Pythonic IR script representing the process steps & branches."""
    lines = ["def execute_process(intake_payload):"]
    if not rows:
        lines.append("    return intake_payload")
        return "\n".join(lines)

    defined_vars: Set[str] = {"intake_payload"}
    for idx, r in enumerate(rows, start=1):
        step_fn = _slug(r.get("q03_process_name") or r.get("q01_raw_entry") or f"step_{idx}")
        in_data = _slug(r.get("q08_input_data_mandatory") or "", fallback="in") if r.get("q08_input_data_mandatory") else None
        out_data = _slug(r.get("q10_output_data") or f"result_{idx}", fallback="out")
        exc = (r.get("q12_exception_path") or "").strip()
        is_rep = bool(r.get("q13_is_repeatable"))

        arg = in_data if in_data else "intake_payload"
        if is_rep:
            lines.append(f"    while needs_revision_{idx}:")
            lines.append(f"        {out_data} = {step_fn}({arg})")
        elif exc:
            lines.append(f"    if check_valid_{idx}({arg}):")
            lines.append(f"        {out_data} = {step_fn}({arg})")
            lines.append("    else:")
            lines.append(f"        handle_exception_{idx}({arg})")
        else:
            # Potential decision/check step without an explicit else branch
            raw_low = (r.get("q01_raw_entry") or "").lower()
            if any(k in raw_low for k in ("prüf", "check", "verify", "genehmig", "approv", "freigeb", "valid")):
                lines.append(f"    if check_valid_{idx}({arg}):")
                lines.append(f"        {out_data} = {step_fn}({arg})")
            else:
                lines.append(f"    {out_data} = {step_fn}({arg})")
        defined_vars.add(out_data)

    lines.append("    return True")
    return "\n".join(lines)


def analyze_plotholes(project_id: str, survey_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run AST control-flow + def-use + entity-graph plothole checks."""
    ir_source = compile_survey_to_python_ir(survey_rows)
    tree = ast.parse(ir_source)

    plotholes: List[Dict[str, Any]] = []

    # 1. AST Check: Decision branches missing an `else:` rejection/fallback path
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and not node.orelse:
            plotholes.append({
                "kind": "MISSING_ELSE_BRANCH",
                "severity": "HIGH",
                "question_code": "q12_exception_path",
                "message": f"Validation branch on IR line {node.lineno} has no fallback/rejection path (`else:`).",
            })

    # 2. AST Def-Use Check: Required input data referenced before any prior step produced it
    assigned: Set[str] = {"intake_payload"}
    for idx, r in enumerate(survey_rows, start=1):
        mand_in = (r.get("q08_input_data_mandatory") or "").strip()
        if mand_in and idx > 1:
            tok = _slug(mand_in, fallback="in")
            if tok not in assigned and not r.get("q05_trigger_event"):
                plotholes.append({
                    "kind": "UNPRODUCED_INPUT_DATA",
                    "severity": "MEDIUM",
                    "row_id": r.get("id"),
                    "entry": r.get("q01_raw_entry"),
                    "question_code": "q08_input_data_mandatory",
                    "message": f"Step '{r.get('q01_raw_entry')}' requires '{mand_in}' which no earlier step outputs.",
                })
        if r.get("q10_output_data"):
            assigned.add(_slug(r["q10_output_data"], fallback="out"))

    # 3. Entity-Graph & Math Checks (Unlinked systems, Missing Owners, RTO >= MTPD)
    structure = EntityService.get_project_structure(project_id)
    deps = structure["dependencies"]
    linked_from = {d["from_entity_id"] for d in deps if d["dependency_type"] == "SUPPORTS"}

    for ent in structure["entities"]:
        if ent["entity_type"] == "SYSTEM" and ent["id"] not in linked_from:
            plotholes.append({
                "kind": "UNLINKED_SYSTEM",
                "severity": "CRITICAL",
                "entity_id": ent["id"],
                "entry": ent["name"],
                "req_id": "R002",
                "message": f"System '{ent['name']}' is not linked to any business process (BSI 200-2 R002).",
            })
        if ent["entity_type"] in ("PROCESS", "SYSTEM") and not ent.get("owner_role"):
            plotholes.append({
                "kind": "MISSING_OWNER",
                "severity": "HIGH",
                "entity_id": ent["id"],
                "entry": ent["name"],
                "req_id": "R003",
                "message": f"{ent['entity_type'].title()} '{ent['name']}' has no owner role assigned (R003 / ISO 5.3).",
            })
        decl = ent.get("declared_protection") or {}
        if decl.get("rto") is not None or decl.get("mtpd") is not None:
            rto_res = evaluate_rule("RTO_LT_MTPD", context={"rto": decl.get("rto"), "mtpd": decl.get("mtpd")})
            if rto_res["status"] == STATUS_CRITICAL_GAP:
                plotholes.append({
                    "kind": "INVALID_RTO_MTPD",
                    "severity": "CRITICAL",
                    "entity_id": ent["id"],
                    "entry": ent["name"],
                    "req_id": "R018",
                    "message": f"{ent['name']}: {rto_res['reason']}",
                })

    return {
        "ir_source": ir_source,
        "plothole_count": len(plotholes),
        "plotholes": plotholes,
    }
