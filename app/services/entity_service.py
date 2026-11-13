"""Object-centric organizational entity graph & BSI 200-2 inheritance engine.

Manages the 6 core entity kinds (PROCESS, SUB_PROCESS, ACTIVITY, SYSTEM,
DATA_ASSET, ROLE), parent-child hierarchies, directed dependencies, and
deterministic BSI Maximumprinzip inheritance + requirement assessments.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db
from app.services.mapping_verifier import (
    CIA_LEVELS,
    STATUS_COMPLIANT,
    STATUS_CRITICAL_GAP,
    STATUS_NEEDS_INPUT,
    compute_inherited_protection,
    evaluate_rule,
    parse_cia,
    parse_number,
)
from app.services.standards_service import ensure_catalog_ready

ENTITY_TYPES = ("PROCESS", "SUB_PROCESS", "ACTIVITY", "SYSTEM", "DATA_ASSET", "ROLE")
DEPENDENCY_TYPES = ("SUPPORTS", "USES_DATA", "READS", "WRITES", "EXECUTED_BY")


def _parse_cia_and_reason(raw: Optional[str]) -> Tuple[Optional[str], str]:
    if not raw:
        return None, ""
    text = str(raw).strip()
    level = parse_cia(text)
    if level:
        # Strip leading level label if justification follows after '-' or ':'
        rem = re.sub(r"^(?:very\s+high|sehr\s+hoch|high|hoch|normal)\s*[:\-–]?\s*", "", text, flags=re.IGNORECASE).strip()
        return level, rem
    return None, text


def _parse_rto_mtpd(raw: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    if not raw:
        return None, None
    text = str(raw).strip()
    rto = None
    mtpd = None
    rto_m = re.search(r"rto\s*[:=]?\s*(\d+(?:[.,]\d+)?\s*(?:h|std|stunden|tag|tage|day|days|woche|week)?)", text, re.IGNORECASE)
    mtpd_m = re.search(r"mtpd\s*[:=]?\s*(\d+(?:[.,]\d+)?\s*(?:h|std|stunden|tag|tage|day|days|woche|week)?)", text, re.IGNORECASE)
    if rto_m:
        rto = parse_number(rto_m.group(1))
    if mtpd_m:
        mtpd = parse_number(mtpd_m.group(1))
    if rto is None and mtpd is None:
        nums = re.findall(r"\d+(?:[.,]\d+)?\s*(?:h|tag|tage|day|days|woche|week)?", text, re.IGNORECASE)
        if len(nums) >= 2:
            rto = parse_number(nums[0])
            mtpd = parse_number(nums[1])
        elif len(nums) == 1:
            mtpd = parse_number(nums[0])
    return rto, mtpd


class EntityService:
    @staticmethod
    def upsert_entity(
        project_id: str,
        entity_type: str,
        name: str,
        description: Optional[str] = None,
        parent_id: Optional[str] = None,
        owner_role: Optional[str] = None,
        source_survey_row_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        etype = (entity_type or "").strip().upper()
        if etype not in ENTITY_TYPES:
            raise ValueError(f"Unsupported entity_type '{entity_type}'. Must be one of {ENTITY_TYPES}")
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Entity name must not be empty.")

        with get_db() as conn:
            if parent_id:
                parent = conn.execute(
                    "SELECT id FROM org_entities WHERE id = ? AND project_id = ?",
                    (parent_id, project_id),
                ).fetchone()
                if not parent:
                    raise ValueError(f"Parent entity '{parent_id}' not found in project.")

            existing = conn.execute(
                "SELECT * FROM org_entities WHERE project_id = ? AND entity_type = ? AND lower(name) = lower(?)",
                (project_id, etype, clean_name),
            ).fetchone()

            if existing:
                new_desc = description if description else existing["description"]
                new_parent = parent_id if parent_id is not None else existing["parent_id"]
                new_owner = owner_role if owner_role else existing["owner_role"]
                new_row_id = source_survey_row_id or existing["source_survey_row_id"]
                conn.execute(
                    """UPDATE org_entities
                       SET description = ?, parent_id = ?, owner_role = ?,
                           source_survey_row_id = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (new_desc, new_parent, new_owner, new_row_id, existing["id"]),
                )
                row = conn.execute("SELECT * FROM org_entities WHERE id = ?", (existing["id"],)).fetchone()
                return dict(row)

            eid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO org_entities
                   (id, project_id, entity_type, name, description, parent_id, owner_role, source_survey_row_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (eid, project_id, etype, clean_name, description, parent_id, owner_role, source_survey_row_id),
            )
            row = conn.execute("SELECT * FROM org_entities WHERE id = ?", (eid,)).fetchone()
            return dict(row)

    @staticmethod
    def delete_entity(project_id: str, entity_id: str) -> bool:
        with get_db() as conn:
            res = conn.execute(
                "DELETE FROM org_entities WHERE id = ? AND project_id = ?",
                (entity_id, project_id),
            )
            return res.rowcount > 0

    @staticmethod
    def upsert_dependency(
        project_id: str,
        from_entity_id: str,
        to_entity_id: str,
        dependency_type: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        dtype = (dependency_type or "").strip().upper()
        if dtype not in DEPENDENCY_TYPES:
            raise ValueError(f"Unsupported dependency_type '{dependency_type}'. Must be one of {DEPENDENCY_TYPES}")
        if from_entity_id == to_entity_id:
            raise ValueError("Cannot link an entity to itself.")

        with get_db() as conn:
            for eid in (from_entity_id, to_entity_id):
                found = conn.execute(
                    "SELECT id FROM org_entities WHERE id = ? AND project_id = ?",
                    (eid, project_id),
                ).fetchone()
                if not found:
                    raise ValueError(f"Entity '{eid}' not found in project.")

            existing = conn.execute(
                """SELECT * FROM entity_dependencies
                   WHERE project_id = ? AND from_entity_id = ? AND to_entity_id = ? AND dependency_type = ?""",
                (project_id, from_entity_id, to_entity_id, dtype),
            ).fetchone()
            if existing:
                if notes is not None:
                    conn.execute("UPDATE entity_dependencies SET notes = ? WHERE id = ?", (notes, existing["id"]))
                row = conn.execute("SELECT * FROM entity_dependencies WHERE id = ?", (existing["id"],)).fetchone()
                return dict(row)

            dep_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO entity_dependencies
                   (id, project_id, from_entity_id, to_entity_id, dependency_type, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dep_id, project_id, from_entity_id, to_entity_id, dtype, notes),
            )
            row = conn.execute("SELECT * FROM entity_dependencies WHERE id = ?", (dep_id,)).fetchone()
            return dict(row)

    @staticmethod
    def delete_dependency(project_id: str, dep_id: str) -> bool:
        with get_db() as conn:
            res = conn.execute(
                "DELETE FROM entity_dependencies WHERE id = ? AND project_id = ?",
                (dep_id, project_id),
            )
            return res.rowcount > 0

    @staticmethod
    def save_entity_assessment(
        project_id: str,
        entity_id: str,
        req_id: str,
        answer_value: Optional[str] = None,
        answer_numeric: Optional[float] = None,
        justification: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
        answered_by: str = "system_extraction",
    ) -> Dict[str, Any]:
        ensure_catalog_ready()
        with get_db() as conn:
            req = conn.execute(
                "SELECT validation_rule FROM standards_requirements WHERE req_id = ?",
                (req_id,),
            ).fetchone()
            if not req:
                raise ValueError(f"Unknown req_id '{req_id}'")

            eval_res = evaluate_rule(
                req["validation_rule"],
                answer_value=answer_value,
                answer_numeric=answer_numeric,
                justification=justification,
                context=context,
            )
            evidence_str = json.dumps(evidence or [], ensure_ascii=False)
            aid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO entity_requirement_assessments
                   (id, project_id, entity_id, req_id, answer_value, answer_numeric,
                    justification, status, evidence_json, answered_by, answered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(entity_id, req_id) DO UPDATE SET
                     answer_value = excluded.answer_value,
                     answer_numeric = excluded.answer_numeric,
                     justification = excluded.justification,
                     status = excluded.status,
                     evidence_json = CASE
                       WHEN excluded.evidence_json != '[]' THEN excluded.evidence_json
                       ELSE entity_requirement_assessments.evidence_json
                     END,
                     answered_by = excluded.answered_by,
                     answered_at = CURRENT_TIMESTAMP,
                     updated_at = CURRENT_TIMESTAMP""",
                (
                    aid,
                    project_id,
                    entity_id,
                    req_id,
                    answer_value,
                    answer_numeric,
                    justification,
                    eval_res["status"],
                    evidence_str,
                    answered_by,
                ),
            )
            row = conn.execute(
                "SELECT * FROM entity_requirement_assessments WHERE entity_id = ? AND req_id = ?",
                (entity_id, req_id),
            ).fetchone()
            out = dict(row)
            out["reason"] = eval_res["reason"]
            return out

    @staticmethod
    def sync_survey_row_to_entities(project_id: str, row_dict: Dict[str, Any]) -> None:
        """Project a process_survey_rows entry into org_entities + entity_dependencies + assessments."""
        raw_entry = (row_dict.get("q01_raw_entry") or "").strip()
        if not raw_entry:
            return

        row_id = row_dict.get("id")
        sheet_name = row_dict.get("sheet_name") or "General"
        kind = (row_dict.get("q02_entry_kind") or "PROCESS").upper()
        owner = (row_dict.get("q04_owner_role") or "").strip() or None
        citation = [{"source": f"Survey Sheet '{sheet_name}'", "quote": raw_entry, "row_id": row_id}]

        proc_name = (row_dict.get("q03_process_name") or "").strip()
        primary_entity = None
        process_entity = None

        if kind == "APPLICATION":
            sys_ent = EntityService.upsert_entity(
                project_id=project_id,
                entity_type="SYSTEM",
                name=raw_entry,
                owner_role=owner,
                source_survey_row_id=row_id,
            )
            primary_entity = sys_ent
            if proc_name:
                process_entity = EntityService.upsert_entity(
                    project_id=project_id,
                    entity_type="PROCESS",
                    name=proc_name,
                    owner_role=owner,
                )
                EntityService.upsert_dependency(project_id, sys_ent["id"], process_entity["id"], "SUPPORTS")
                EntityService.save_entity_assessment(
                    project_id, sys_ent["id"], "R002", answer_value=proc_name,
                    evidence=citation, context={"links": 1},
                )
            else:
                EntityService.save_entity_assessment(
                    project_id, sys_ent["id"], "R002", answer_value="",
                    evidence=citation, context={"links": 0},
                )
        else:
            process_entity = EntityService.upsert_entity(
                project_id=project_id,
                entity_type="PROCESS",
                name=proc_name or raw_entry,
                description=row_dict.get("q07_lived_reality_notes") or None,
                owner_role=owner,
                source_survey_row_id=row_id,
            )
            primary_entity = process_entity
            EntityService.save_entity_assessment(
                project_id, process_entity["id"], "R001", answer_value=process_entity["name"], evidence=citation
            )

        target_for_attrs = process_entity or primary_entity

        if owner:
            role_ent = EntityService.upsert_entity(project_id, "ROLE", owner)
            EntityService.upsert_dependency(project_id, target_for_attrs["id"], role_ent["id"], "EXECUTED_BY")
            EntityService.save_entity_assessment(
                project_id, target_for_attrs["id"], "R003", answer_value=owner,
                evidence=citation, context={"owner_role": owner},
            )

        # Storage location -> SYSTEM supporting target process
        storage = (row_dict.get("q11_storage_location") or "").strip()
        if storage and process_entity:
            for s_name in [p.strip() for p in re.split(r"[,;\n]", storage) if p.strip()]:
                st_sys = EntityService.upsert_entity(project_id, "SYSTEM", s_name)
                EntityService.upsert_dependency(project_id, st_sys["id"], process_entity["id"], "SUPPORTS")
                EntityService.save_entity_assessment(
                    project_id, st_sys["id"], "R002", answer_value=process_entity["name"],
                    evidence=citation, context={"links": 1},
                )

        # Data assets -> DATA_ASSET linked via USES_DATA
        for col, role_note in (
            ("q08_input_data_mandatory", "READS (Mandatory Input)"),
            ("q09_input_data_optional", "READS (Optional Input)"),
            ("q10_output_data", "WRITES (Output)"),
        ):
            val = (row_dict.get(col) or "").strip()
            if val:
                for d_name in [p.strip() for p in re.split(r"[,;\n]", val) if p.strip()]:
                    d_ent = EntityService.upsert_entity(project_id, "DATA_ASSET", d_name)
                    EntityService.upsert_dependency(
                        project_id, target_for_attrs["id"], d_ent["id"], "USES_DATA", notes=role_note
                    )

        # CIA & BIA protection assessments
        for col, req_id in (
            ("q15_cia_confidentiality", "R005"),
            ("q16_cia_integrity", "R006"),
            ("q17_cia_availability", "R007"),
        ):
            raw_cia = row_dict.get(col)
            if raw_cia:
                lvl, reason = _parse_cia_and_reason(raw_cia)
                EntityService.save_entity_assessment(
                    project_id, target_for_attrs["id"], req_id,
                    answer_value=lvl or str(raw_cia),
                    justification=reason,
                    evidence=citation,
                )

        raw_bia = row_dict.get("q18_bia_mtpd_rto")
        if raw_bia:
            rto, mtpd = _parse_rto_mtpd(raw_bia)
            if mtpd is not None:
                EntityService.save_entity_assessment(
                    project_id, target_for_attrs["id"], "R017",
                    answer_value=str(raw_bia), answer_numeric=mtpd, evidence=citation,
                )
            if rto is not None or mtpd is not None:
                EntityService.save_entity_assessment(
                    project_id, target_for_attrs["id"], "R018",
                    answer_value=str(mtpd) if mtpd is not None else str(raw_bia),
                    answer_numeric=rto,
                    evidence=citation,
                    context={"rto": rto, "mtpd": mtpd},
                )

    @staticmethod
    def get_project_structure(project_id: str) -> Dict[str, Any]:
        """Return full entity hierarchy, links, and BSI Maximumprinzip inherited protection."""
        with get_db() as conn:
            entities = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM org_entities WHERE project_id = ? ORDER BY entity_type, name",
                    (project_id,),
                ).fetchall()
            ]
            dependencies = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM entity_dependencies WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
            ]
            assessments = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM entity_requirement_assessments WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
            ]

        assess_by_entity: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for a in assessments:
            a["evidence"] = json.loads(a.get("evidence_json") or "[]")
            assess_by_entity.setdefault(a["entity_id"], {})[a["req_id"]] = a

        entities_by_id = {e["id"]: e for e in entities}

        # Map each entity's own declared CIA/RTO/MTPD
        declared_by_entity: Dict[str, Dict[str, Any]] = {}
        for e in entities:
            ea = assess_by_entity.get(e["id"], {})
            declared_by_entity[e["id"]] = {
                "confidentiality": (ea.get("R005") or {}).get("answer_value"),
                "integrity": (ea.get("R006") or {}).get("answer_value"),
                "availability": (ea.get("R007") or {}).get("answer_value"),
                "rto": (ea.get("R018") or {}).get("answer_numeric"),
                "mtpd": (ea.get("R017") or {}).get("answer_numeric"),
            }

        # Compute BSI Maximumprinzip inheritance for SYSTEM and DATA_ASSET nodes
        inherited_by_entity: Dict[str, Dict[str, Any]] = {}
        for e in entities:
            dependent_process_answers: List[Dict[str, Any]] = []
            dependent_names: List[str] = []
            for dep in dependencies:
                if dep["from_entity_id"] == e["id"] and dep["dependency_type"] == "SUPPORTS":
                    target = entities_by_id.get(dep["to_entity_id"])
                    if target:
                        dependent_names.append(target["name"])
                        dependent_process_answers.append(declared_by_entity.get(target["id"], {}))
                elif dep["to_entity_id"] == e["id"] and dep["dependency_type"] in ("USES_DATA", "READS", "WRITES"):
                    src = entities_by_id.get(dep["from_entity_id"])
                    if src:
                        dependent_names.append(src["name"])
                        dependent_process_answers.append(declared_by_entity.get(src["id"], {}))

            if dependent_process_answers:
                inh = compute_inherited_protection(dependent_process_answers)
                inh["from_processes"] = dependent_names
                inherited_by_entity[e["id"]] = inh

        # Build parent-child tree & attach metadata
        children_map: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for e in entities:
            e["declared_protection"] = declared_by_entity.get(e["id"], {})
            e["inherited_protection"] = inherited_by_entity.get(e["id"])
            e["assessments"] = list(assess_by_entity.get(e["id"], {}).values())
            e["children"] = []
            children_map.setdefault(e["parent_id"], []).append(e)

        for e in entities:
            e["children"] = children_map.get(e["id"], [])

        roots = [e for e in entities if not e["parent_id"] or e["parent_id"] not in entities_by_id]

        return {
            "project_id": project_id,
            "total_entities": len(entities),
            "total_dependencies": len(dependencies),
            "entities": entities,
            "tree": roots,
            "dependencies": dependencies,
        }
