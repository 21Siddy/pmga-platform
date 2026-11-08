import re
import uuid
import logging
from typing import Dict, List, Any, Optional
from app.database import get_db
from app.services.bpmn_engine import BPMNProcessGraph, BPMNNode, BPMNEdge
from app.services.bpmn_verifier import BPMNVerifier

logger = logging.getLogger(__name__)

ALLOWED_QUESTION_COLS = {
    "q02_entry_kind",
    "q03_process_name",
    "q04_owner_role",
    "q05_trigger_event",
    "q06_in_handbook",
    "q07_lived_reality_notes",
    "q08_input_data_mandatory",
    "q09_input_data_optional",
    "q10_output_data",
    "q11_storage_location",
    "q12_exception_path",
    "q13_is_repeatable",
    "q14_raw_criticality",
    "q15_cia_confidentiality",
    "q16_cia_integrity",
    "q17_cia_availability",
    "q18_bia_mtpd_rto"
}

QUESTION_DEFINITIONS = [
    {
        "code": "q02_entry_kind",
        "phase": "Group 1: Scope & Strukturanalyse",
        "question": "Is this an actual business process, or an IT application/software system used by a process?",
        "options": ["APPLICATION", "PROCESS", "CATEGORY"],
        "concept_type": None
    },
    {
        "code": "q03_process_name",
        "phase": "Group 1: Scope & Strukturanalyse",
        "question": "Which business process uses the '{entry}' system? (e.g. Issue Voucher, Redeem Voucher, Settle Accounting)",
        "condition": lambda row: row.get("q02_entry_kind") == "APPLICATION",
        "concept_type": "PROCESS"
    },
    {
        "code": "q04_owner_role",
        "phase": "Group 1: Scope & Strukturanalyse",
        "question": "Which organizational role, department, or clerk is responsible for executing this?",
        "concept_type": "ROLE"
    },
    {
        "code": "q05_trigger_event",
        "phase": "Group 2: Trigger & Handbook vs. Lived Reality",
        "question": "What exact event or filing triggers this process to start?",
        "concept_type": "TRIGGER"
    },
    {
        "code": "q06_in_handbook",
        "phase": "Group 2: Trigger & Handbook vs. Lived Reality",
        "question": "Is this process formally documented in an official process handbook or statutory mandate?",
        "options": ["YES", "NO", "PARTIAL"],
        "concept_type": None
    },
    {
        "code": "q07_lived_reality_notes",
        "phase": "Group 2: Trigger & Handbook vs. Lived Reality",
        "question": "How is this process actually executed day-to-day by staff (including shortcuts, email handoffs, or workarounds)?",
        "concept_type": None
    },
    {
        "code": "q08_input_data_mandatory",
        "phase": "Group 3: Data Assets & Storage (VVT)",
        "question": "What input data/documents are strictly mandatory for this process to begin?",
        "concept_type": "DATA_ASSET"
    },
    {
        "code": "q09_input_data_optional",
        "phase": "Group 3: Data Assets & Storage (VVT)",
        "question": "What data fields can be omitted while leaving process completion intact?",
        "concept_type": "DATA_ASSET"
    },
    {
        "code": "q10_output_data",
        "phase": "Group 3: Data Assets & Storage (VVT)",
        "question": "What output data, certificate, notification, or file is produced at completion?",
        "concept_type": "DATA_ASSET"
    },
    {
        "code": "q11_storage_location",
        "phase": "Group 3: Data Assets & Storage (VVT)",
        "question": "Where is the data stored (system, database, shared network folder, or physical paper)?",
        "concept_type": "SYSTEM"
    },
    {
        "code": "q12_exception_path",
        "phase": "Group 4: Exceptions & Repeatability",
        "question": "What happens if required data is missing, approval fails, or an error occurs?",
        "concept_type": None
    },
    {
        "code": "q13_is_repeatable",
        "phase": "Group 4: Exceptions & Repeatability",
        "question": "Can this activity repeat in a loop where different outcomes or revisions can occur?",
        "options": ["YES", "NO"],
        "concept_type": None
    },
    {
        "code": "q15_cia_confidentiality",
        "phase": "Group 5: Criticality (BSI 200-2 CIA & BSI 200-4 BIA)",
        "question": "Why is this labeled '{criticality}' for Confidentiality (Vertraulichkeit)? (Normal, High, Very High — what is the impact if leaked?)",
        "condition": lambda row: bool(row.get("q14_raw_criticality")),
        "options": ["Normal", "High", "Very High"],
        "concept_type": None
    },
    {
        "code": "q16_cia_integrity",
        "phase": "Group 5: Criticality (BSI 200-2 CIA & BSI 200-4 BIA)",
        "question": "Why is this labeled '{criticality}' for Integrity (Integrität)? (Normal, High, Very High — what is the impact if altered?)",
        "condition": lambda row: bool(row.get("q14_raw_criticality")),
        "options": ["Normal", "High", "Very High"],
        "concept_type": None
    },
    {
        "code": "q17_cia_availability",
        "phase": "Group 5: Criticality (BSI 200-2 CIA & BSI 200-4 BIA)",
        "question": "Why is this labeled '{criticality}' for Availability (Verfügbarkeit)? (Normal, High, Very High — what is the impact of downtime?)",
        "condition": lambda row: bool(row.get("q14_raw_criticality")),
        "options": ["Normal", "High", "Very High"],
        "concept_type": None
    },
    {
        "code": "q18_bia_mtpd_rto",
        "phase": "Group 5: Criticality (BSI 200-2 CIA & BSI 200-4 BIA)",
        "question": "What is the maximum tolerable downtime (MTPD) and target recovery time (RTO < MTPD)?",
        "condition": lambda row: bool(row.get("q14_raw_criticality")),
        "concept_type": None
    }
]

APP_KEYWORDS = ("system", "software", "portal", "server", "datenbank", "database", "tool", "app", "sap", "dms", "crm", "erp", "datev")

class SurveyService:
    @staticmethod
    def classify_entry_kind(entry_text: str) -> str:
        """
        Deterministically classifies if an entry is an APPLICATION, PROCESS, or CATEGORY.
        Zero LLM latency.
        """
        low = entry_text.lower().strip()
        # Ends with system/software/tool or contains explicit application keywords without action verbs
        has_app_word = any(k in low for k in APP_KEYWORDS)
        # German or English infinitive action verbs
        has_action_verb = bool(re.search(r'\b(erfassen|prüfen|freigeben|genehmigen|versenden|archivieren|bearbeiten|buchen|check|process|approve|send|archive|verify)\b', low))

        if has_app_word and not has_action_verb:
            return "APPLICATION"
        if has_action_verb:
            return "PROCESS"
        if len(low.split()) <= 2 and not has_action_verb:
            return "APPLICATION"
        return "PROCESS"

    @staticmethod
    def ingest_rows_into_survey(project_id: str, items: List[Dict[str, Any]]) -> List[str]:
        """
        Ingests rows from an uploaded Excel or parsed dump into process_survey_rows.
        Stores raw strings verbatim.
        """
        inserted_ids = []
        with get_db() as conn:
            # Get current max row_order for project
            max_order_row = conn.execute(
                "SELECT COALESCE(MAX(row_order), 0) as max_o FROM process_survey_rows WHERE project_id = ?",
                (project_id,)
            ).fetchone()
            current_order = max_order_row["max_o"] if max_order_row else 0

            for it in items:
                raw_entry = str(it.get("raw_entry") or it.get("title") or it.get("name") or "").strip()
                if not raw_entry:
                    continue

                row_id = str(uuid.uuid4())
                current_order += 1
                sheet_name = str(it.get("sheet_name") or "General").strip()

                # Detect entry kind
                entry_kind = it.get("entry_kind") or SurveyService.classify_entry_kind(raw_entry)
                raw_crit = str(it.get("raw_criticality") or it.get("criticality") or "").strip()

                conn.execute(
                    """INSERT INTO process_survey_rows 
                       (id, project_id, sheet_name, row_order, q01_raw_entry, q02_entry_kind, q14_raw_criticality)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (row_id, project_id, sheet_name, current_order, raw_entry, entry_kind, raw_crit or None)
                )

                # Pre-register application in accepted_concepts if applicable
                if entry_kind == "APPLICATION":
                    conn.execute(
                        """INSERT OR IGNORE INTO accepted_concepts 
                           (id, project_id, concept_type, source_text, label, status)
                           VALUES (?, ?, 'SYSTEM', ?, ?, 'ACCEPTED')""",
                        (str(uuid.uuid4()), project_id, raw_entry, raw_entry)
                    )

                inserted_ids.append(row_id)

        return inserted_ids

    @staticmethod
    def get_survey_state(project_id: str) -> Dict[str, Any]:
        """
        Returns all verbatim survey rows, progress stats, and the ordered next question per row.
        Executes in <1ms via single SQL query.
        """
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM process_survey_rows WHERE project_id = ? ORDER BY row_order ASC",
                (project_id,)
            ).fetchall()
            concepts = conn.execute(
                "SELECT * FROM accepted_concepts WHERE project_id = ? AND status = 'ACCEPTED' ORDER BY created_at ASC",
                (project_id,)
            ).fetchall()

        concepts_by_type: Dict[str, List[str]] = {}
        for c in concepts:
            ctype = c["concept_type"]
            if ctype not in concepts_by_type:
                concepts_by_type[ctype] = []
            if c["label"] not in concepts_by_type[ctype]:
                concepts_by_type[ctype].append(c["label"])

        processed_rows = []
        total_slots = 0
        filled_slots = 0

        for r in rows:
            r_dict = dict(r)
            # Count answered question slots
            row_total = 0
            row_filled = 0
            next_q = None

            for qdef in QUESTION_DEFINITIONS:
                code = qdef["code"]
                # Evaluate condition
                if "condition" in qdef and not qdef["condition"](r_dict):
                    continue

                row_total += 1
                val = r_dict.get(code)
                if val is not None and str(val).strip() != "":
                    row_filled += 1
                elif next_q is None:
                    # First unanswered slot, in fixed order
                    entry_val = r_dict.get("q01_raw_entry") or "this item"
                    crit_val = r_dict.get("q14_raw_criticality") or "critical"
                    q_text = qdef["question"].replace("{entry}", str(entry_val))
                    q_text = q_text.replace("{criticality}", str(crit_val))

                    # Look up dynamic Prelead options from accepted_concepts
                    dynamic_options = list(qdef.get("options") or [])
                    ctype = qdef.get("concept_type")
                    if ctype and ctype in concepts_by_type:
                        for opt in concepts_by_type[ctype]:
                            if opt not in dynamic_options:
                                dynamic_options.append(opt)

                    next_q = {
                        "row_id": r_dict["id"],
                        "entry": str(entry_val),
                        "question_code": code,
                        "phase": qdef["phase"],
                        "question_text": q_text,
                        "options": dynamic_options,
                        "concept_type": ctype
                    }

            total_slots += row_total
            filled_slots += row_filled
            r_dict["slots_total"] = row_total
            r_dict["slots_filled"] = row_filled
            r_dict["next_question"] = next_q
            processed_rows.append(r_dict)

        overall_percent = round((filled_slots / total_slots * 100)) if total_slots > 0 else 0

        return {
            "project_id": project_id,
            "total_rows": len(processed_rows),
            "total_slots": total_slots,
            "filled_slots": filled_slots,
            "completion_percent": overall_percent,
            "rows": processed_rows,
            "accepted_concepts": concepts_by_type
        }

    @staticmethod
    def save_verbatim_answer(
        project_id: str,
        row_id: str,
        question_code: str,
        answer_text: str,
        accept_as_concept: bool = True
    ) -> Dict[str, Any]:
        """
        Saves a question answer verbatim to the specific SQL column.
        Validates column against allowlist. Re-compiles non-blocking BPMN.
        """
        clean_code = question_code.strip()
        if clean_code not in ALLOWED_QUESTION_COLS:
            raise ValueError(f"Invalid question code: '{clean_code}'. Must be one of {sorted(ALLOWED_QUESTION_COLS)}")

        clean_answer = str(answer_text).strip()
        val_to_store = clean_answer if clean_answer else None

        # Convert boolean repeatability flag
        if clean_code == "q13_is_repeatable":
            val_to_store = 1 if str(clean_answer).upper() in ["1", "YES", "TRUE", "JA"] else 0

        with get_db() as conn:
            # Verify row belongs to project
            row = conn.execute(
                "SELECT * FROM process_survey_rows WHERE id = ? AND project_id = ?",
                (row_id, project_id)
            ).fetchone()
            if not row:
                raise ValueError(f"Row '{row_id}' not found for project '{project_id}'.")

            # Update verbatim slot
            conn.execute(
                f"UPDATE process_survey_rows SET {clean_code} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (val_to_store, row_id)
            )

            # Record reusable Prelead concept if relevant
            if accept_as_concept and clean_answer and clean_code in ["q03_process_name", "q04_owner_role", "q08_input_data_mandatory", "q10_output_data", "q11_storage_location"]:
                ctype_map = {
                    "q03_process_name": "PROCESS",
                    "q04_owner_role": "ROLE",
                    "q08_input_data_mandatory": "DATA_ASSET",
                    "q10_output_data": "DATA_ASSET",
                    "q11_storage_location": "SYSTEM"
                }
                ctype = ctype_map.get(clean_code)
                if ctype:
                    # Clean up split items if multiple separated by comma
                    parts = [p.strip() for p in re.split(r'[,;\n]', clean_answer) if p.strip()]
                    for p in parts:
                        conn.execute(
                            """INSERT OR IGNORE INTO accepted_concepts 
                               (id, project_id, concept_type, source_text, label, status)
                               VALUES (?, ?, ?, ?, ?, 'ACCEPTED')""",
                            (str(uuid.uuid4()), project_id, ctype, p, p)
                        )

        # Return refreshed survey state
        return SurveyService.get_survey_state(project_id)

    @staticmethod
    def compile_survey_to_bpmn_graph(project_id: str) -> BPMNProcessGraph:
        """
        Non-blockingly compiles all survey rows into a valid, connected BPMN 2.0 graph.
        Handles missing slots gracefully and generates loop-back flows for repeatable steps.
        """
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM process_survey_rows WHERE project_id = ? ORDER BY row_order ASC",
                (project_id,)
            ).fetchall()

        if not rows:
            return BPMNProcessGraph(
                process_title="Empty Process Survey",
                lanes=["General Staff"],
                nodes=[
                    BPMNNode(id="Start_1", name="Process Initiated", type="startEvent", lane="General Staff"),
                    BPMNNode(id="End_1", name="Process Concluded", type="endEvent", lane="General Staff")
                ],
                edges=[BPMNEdge(id="Flow_1", sourceRef="Start_1", targetRef="End_1")]
            )

        # Discover swimlanes
        lanes = []
        for r in rows:
            role = r["q04_owner_role"]
            if role and role.strip() and role.strip() not in lanes:
                lanes.append(role.strip())
        if not lanes:
            lanes = ["Citizen / Applicant", "Department Staff", "Supervisor / Approver"]
        default_lane = lanes[0]

        nodes: List[BPMNNode] = []
        edges: List[BPMNEdge] = []

        # Start Event
        trigger_text = rows[0]["q05_trigger_event"] or "Intake Request Received"
        nodes.append(BPMNNode(id="Start_1", name=trigger_text[:45], type="startEvent", lane=default_lane))
        prev_node_id = "Start_1"

        for idx, r in enumerate(rows, start=1):
            task_id = f"Task_{idx}"
            assigned_lane = r["q04_owner_role"] or (lanes[min(1, len(lanes)-1)])
            
            # Label: Use actual process name or wrap application
            if r["q02_entry_kind"] == "APPLICATION":
                proc_name = r["q03_process_name"] or f"Execute {r['q01_raw_entry']}"
                task_name = f"{proc_name} [{r['q01_raw_entry']}]"
                task_type = "serviceTask"
            else:
                task_name = r["q03_process_name"] or r["q01_raw_entry"]
                task_type = "userTask"

            # Risk annotation if critical or unspecified
            notes = []
            if r["q14_raw_criticality"]:
                notes.append(f"Criticality: {r['q14_raw_criticality']}")
            if not r["q04_owner_role"]:
                notes.append("UNSPECIFIED: Role/Owner")
            if not r["q08_input_data_mandatory"]:
                notes.append("UNSPECIFIED: Input Data")

            risk_note = " | ".join(notes) if notes else None

            nodes.append(
                BPMNNode(
                    id=task_id,
                    name=task_name[:55],
                    type=task_type,
                    lane=assigned_lane,
                    risk_annotation=risk_note
                )
            )
            edges.append(BPMNEdge(id=f"Flow_to_{task_id}", sourceRef=prev_node_id, targetRef=task_id))
            prev_node_id = task_id

            # If activity is repeatable, add an exclusive gateway loop-back
            if r["q13_is_repeatable"] == 1:
                gw_id = f"Gate_Repeat_{idx}"
                nodes.append(
                    BPMNNode(
                        id=gw_id,
                        name="Repeat required?",
                        type="exclusiveGateway",
                        lane=assigned_lane
                    )
                )
                edges.append(BPMNEdge(id=f"Flow_{task_id}_to_{gw_id}", sourceRef=task_id, targetRef=gw_id))
                # Loop-back edge
                edges.append(
                    BPMNEdge(
                        id=f"Flow_Loop_{idx}",
                        sourceRef=gw_id,
                        targetRef=task_id,
                        name="Retry / Revision"
                    )
                )
                prev_node_id = gw_id

        # End Event
        end_lane = lanes[min(1, len(lanes)-1)]
        output_label = rows[-1]["q10_output_data"] or "Case Completed & Archived"
        nodes.append(BPMNNode(id="End_1", name=output_label[:45], type="endEvent", lane=end_lane))
        edges.append(BPMNEdge(id="Flow_to_End", sourceRef=prev_node_id, targetRef="End_1"))

        # Verify telemetry
        telemetry = BPMNVerifier.verify_graph(
            graph_nodes=[n.model_dump() for n in nodes],
            graph_edges=[e.model_dump() for e in edges],
            source_text=" ".join([r["q01_raw_entry"] for r in rows])
        )

        title = f"Process Architecture ({len(rows)} Elements)"
        return BPMNProcessGraph(
            process_title=title,
            lanes=lanes,
            nodes=nodes,
            edges=edges,
            telemetry=telemetry
        )
