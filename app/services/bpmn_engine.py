import os
import json
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from app.config import settings
from app.services.llm_client import LLMClient
from app.services.bpmn_verifier import BPMNVerifier, VerificationTelemetry

class BPMNNode(BaseModel):
    id: str = Field(description="Unique identifier, e.g. Start_1, Task_1, Gateway_1, End_1")
    name: str = Field(description="Clear, plain-English descriptive name of the step")
    type: str = Field(description="One of: 'startEvent', 'userTask', 'serviceTask', 'exclusiveGateway', 'parallelGateway', 'endEvent'")
    lane: str = Field(description="Role, department or actor executing this step, e.g. 'Citizen', 'Department Clerk', 'IT Admin'")
    source_type: str = Field(default="ACTUAL_OPERATION", description="'ACTUAL_OPERATION' or 'FORMAL_POLICY' or 'POLICY_VIOLATION'")
    risk_annotation: Optional[str] = Field(default=None, description="Optional security or procedural risk note")

class BPMNEdge(BaseModel):
    id: str = Field(description="Unique flow identifier, e.g. Flow_1")
    sourceRef: str = Field(description="Source node id")
    targetRef: str = Field(description="Target node id")
    name: Optional[str] = Field(default=None, description="Condition or decision branch label, e.g. 'Yes', 'No'")

class BPMNProcessGraph(BaseModel):
    process_title: str
    lanes: List[str] = Field(description="List of swimlane names / roles involved")
    nodes: List[BPMNNode]
    edges: List[BPMNEdge]
    telemetry: Optional[VerificationTelemetry] = None

class BPMNEngine:
    @staticmethod
    def generate_graph_from_text(
        process_text: str,
        policy_context: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> BPMNProcessGraph:
        """
        Executes the Scientific 3-Stage Generation, Verification & Recursive Refinement Loop.
        Accepts messy free-text, shorthand notes, and broken grammar.
        """
        # Determine if any LLM credentials are configured
        has_gemini = bool(api_key or settings.GEMINI_API_KEY)
        has_kiconnect = bool(settings.API_KEY and settings.API_BASE)

        if has_gemini or has_kiconnect:
            return BPMNEngine._generate_with_recursive_refinement(
                process_text=process_text,
                policy_context=policy_context,
                api_key=api_key,
                provider=provider,
                model_name=model_name
            )

        # Fallback deterministic generator (robust to messy free text)
        return BPMNEngine._generate_fallback_graph(process_text)

    @staticmethod
    def _generate_with_recursive_refinement(
        process_text: str,
        policy_context: Optional[str],
        api_key: Optional[str],
        provider: Optional[str],
        model_name: Optional[str]
    ) -> BPMNProcessGraph:
        """
        Recursive Critique-Refine Loop with BYOK Playbook Injection:
        Detects style -> Injects archetype rule and verified few-shot -> Generates draft -> Verifies mathematically.
        """
        from app.services.perturbation_engine import PerturbationEngine

        # Detect linguistic archetype and load BYOK Playbook guidelines
        style_meta = PerturbationEngine.detect_input_style(process_text)
        detected_style = style_meta.get("detected_style", "formal")
        rule = style_meta.get("playbook_rule")
        few_shot = style_meta.get("playbook_few_shot")

        system_instruction = (
            "You are a certified Business Process Management (BPMN 2.0) and public administration systems architect. "
            "Convert raw, messy natural language process notes into a sound, connected BPMN 2.0 graph. "
            "Ensure every path starts with a startEvent and terminates in an endEvent. No orphan nodes."
        )

        prompt_parts = [
            "Convert the following operational process notes into a structured BPMN 2.0 graph.",
            "Even if the notes contain typos, shorthand, or broken sentences, extract the actual steps staff take."
        ]

        if rule:
            prompt_parts.append(f"\n--- ARCHETYPE RULE ({detected_style}):\n{rule}")

        if few_shot and isinstance(few_shot, dict) and "input_text" in few_shot and "graph" in few_shot:
            prompt_parts.append(
                f"\n--- VERIFIED FEW-SHOT REFERENCE:\nINPUT NOTES: {few_shot['input_text']}\nEXPECTED JSON GRAPH: {json.dumps(few_shot['graph'])}"
            )

        prompt_parts.append(f"\n--- LIVED STAFF NOTES / OPERATIONAL PROCESS:\n{process_text}")
        prompt_parts.append(f"\n--- FORMAL POLICY RULES (Context):\n{policy_context or 'None'}")

        prompt = "\n".join(prompt_parts)

        # Draft generation
        graph = LLMClient.generate_structured(
            prompt=prompt,
            response_model=BPMNProcessGraph,
            system_instruction=system_instruction,
            api_key=api_key,
            provider=provider,
            model_name=model_name
        )

        # Verification Stage
        telemetry = BPMNVerifier.verify_graph(
            graph_nodes=[n.model_dump() for n in graph.nodes],
            graph_edges=[e.model_dump() for e in graph.edges],
            source_text=process_text
        )

        # Refinement loop (up to 1 refinement pass if defects found)
        if not telemetry.is_valid and telemetry.critique_prompt:
            refine_prompt = f"""The previous BPMN graph had the following structural/faithfulness defects:
CRITIQUE: {telemetry.critique_prompt}

Please revise the graph to fix these defects completely while preserving the original process.

Original Notes:
{process_text}
"""
            try:
                refined_graph = LLMClient.generate_structured(
                    prompt=refine_prompt,
                    response_model=BPMNProcessGraph,
                    system_instruction=system_instruction,
                    api_key=api_key,
                    provider=provider,
                    model_name=model_name
                )
                refined_telemetry = BPMNVerifier.verify_graph(
                    graph_nodes=[n.model_dump() for n in refined_graph.nodes],
                    graph_edges=[e.model_dump() for e in refined_graph.edges],
                    source_text=process_text
                )
                refined_graph.telemetry = refined_telemetry
                return refined_graph
            except Exception:
                # If refinement fails or hits rate limits, keep original draft with telemetry
                pass

        graph.telemetry = telemetry
        return graph

    @staticmethod
    def _generate_fallback_graph(text: str) -> BPMNProcessGraph:
        """
        Deterministic, offline graph builder that parses messy shorthand (arrows, semicolons, bullets),
        detects linguistic styles, parses roles, and handles parallel/branching structures.
        """
        from app.services.perturbation_engine import PerturbationEngine
        style_meta = PerturbationEngine.detect_input_style(text)
        detected_style = style_meta.get("detected_style", "formal")

        actions = BPMNVerifier.extract_atomic_actions(text)
        if not actions:
            actions = [
                "Receive intake filing from citizen",
                "Perform preliminary document completeness check",
                "Process application in departmental records ledger",
                "Issue final permit certificate and archive case"
            ]

        # Extract roles from prefix format (e.g. "Clerk: verify id")
        parsed_items: List[tuple] = []
        discovered_lanes = []
        for act in actions:
            if ":" in act and not act.lower().startswith("http"):
                prefix, rem = act.split(":", 1)
                prefix_clean = prefix.strip().title()
                rem_clean = rem.strip()
                if len(prefix_clean) <= 30 and len(rem_clean) >= 3:
                    if prefix_clean not in discovered_lanes:
                        discovered_lanes.append(prefix_clean)
                    parsed_items.append((prefix_clean, rem_clean))
                    continue
            parsed_items.append((None, act))

        lanes = discovered_lanes if discovered_lanes else ["Citizen / Applicant", "Department Staff", "Supervisor / Approver"]
        default_lane = lanes[0] if lanes else "General Process"

        # Handle out-of-order cues if present
        if detected_style == "out_of_order" and len(parsed_items) >= 3:
            # If text describes closing first and initial last, reorder
            lowered = text.lower()
            if "prior to closing" in lowered and "only after" in lowered:
                # Reverse first and last items
                parsed_items = [parsed_items[-1]] + parsed_items[1:-1] + [parsed_items[0]]

        nodes: List[BPMNNode] = [
            BPMNNode(id="Start_1", name="Process Request Received", type="startEvent", lane=default_lane)
        ]
        edges: List[BPMNEdge] = []
        prev_id = "Start_1"

        # Check if parallel concurrency should be modeled
        if detected_style == "parallel_and_loops" and len(parsed_items) >= 3 and ("parallel" in text.lower() or "simultaneously" in text.lower()):
            # First task
            lane_0, name_0 = parsed_items[0]
            nodes.append(BPMNNode(id="Task_1", name=name_0[:55], type="userTask", lane=lane_0 or lanes[0]))
            edges.append(BPMNEdge(id="F_1", sourceRef="Start_1", targetRef="Task_1"))

            # Parallel split gateway
            split_lane = lane_0 or lanes[0]
            nodes.append(BPMNNode(id="Gate_ParallelSplit", name="Parallel Workstreams", type="parallelGateway", lane=split_lane))
            edges.append(BPMNEdge(id="F_Split", sourceRef="Task_1", targetRef="Gate_ParallelSplit"))

            # Parallel branches (items 1 and 2)
            lane_1, name_1 = parsed_items[1]
            lane_2, name_2 = parsed_items[2]
            nodes.append(BPMNNode(id="Task_Par1", name=name_1[:55], type="userTask", lane=lane_1 or lanes[min(1, len(lanes)-1)]))
            nodes.append(BPMNNode(id="Task_Par2", name=name_2[:55], type="userTask", lane=lane_2 or lanes[min(2, len(lanes)-1)]))
            edges.append(BPMNEdge(id="F_Par1", sourceRef="Gate_ParallelSplit", targetRef="Task_Par1"))
            edges.append(BPMNEdge(id="F_Par2", sourceRef="Gate_ParallelSplit", targetRef="Task_Par2"))

            # Parallel join gateway
            nodes.append(BPMNNode(id="Gate_ParallelJoin", name="Synchronize Workstreams", type="parallelGateway", lane=split_lane))
            edges.append(BPMNEdge(id="F_Join1", sourceRef="Task_Par1", targetRef="Gate_ParallelJoin"))
            edges.append(BPMNEdge(id="F_Join2", sourceRef="Task_Par2", targetRef="Gate_ParallelJoin"))
            prev_id = "Gate_ParallelJoin"

            # Trailing tasks if any
            for idx, (l_assigned, a_name) in enumerate(parsed_items[3:5], start=3):
                node_id = f"Task_{idx}"
                assigned = l_assigned or (lanes[1] if idx <= 4 else lanes[min(2, len(lanes)-1)])
                nodes.append(BPMNNode(id=node_id, name=a_name[:55], type="userTask", lane=assigned))
                edges.append(BPMNEdge(id=f"Flow_{idx}", sourceRef=prev_id, targetRef=node_id))
                prev_id = node_id
        else:
            # Sequential pipeline with lane mapping
            for idx, (l_assigned, a_name) in enumerate(parsed_items[:6], start=1):
                node_id = f"Task_{idx}"
                if l_assigned:
                    assigned_lane = l_assigned
                else:
                    assigned_lane = lanes[1] if idx <= 4 else lanes[min(2, len(lanes)-1)]
                nodes.append(
                    BPMNNode(
                        id=node_id,
                        name=a_name[:55],
                        type="userTask",
                        lane=assigned_lane,
                        source_type="ACTUAL_OPERATION"
                    )
                )
                edges.append(BPMNEdge(id=f"Flow_{idx}", sourceRef=prev_id, targetRef=node_id))
                prev_id = node_id

        end_lane = lanes[min(1, len(lanes)-1)]
        nodes.append(BPMNNode(id="End_1", name="Case Completed & Logged", type="endEvent", lane=end_lane))
        edges.append(BPMNEdge(id=f"Flow_End", sourceRef=prev_id, targetRef="End_1"))

        telemetry = BPMNVerifier.verify_graph(
            graph_nodes=[n.model_dump() for n in nodes],
            graph_edges=[e.model_dump() for e in edges],
            source_text=text
        )

        return BPMNProcessGraph(
            process_title="Operational Workflow",
            lanes=lanes,
            nodes=nodes,
            edges=edges,
            telemetry=telemetry
        )


    @staticmethod
    def compile_graph_to_bpmn_xml(graph: BPMNProcessGraph) -> str:
        """
        Compiles process graph into 100% valid BPMN 2.0 XML with deterministic <bpmndi:BPMNDiagram> coordinates.
        Supports custom node dimensions and swimlanes.
        """
        lane_map = {lane: [] for lane in graph.lanes}
        for node in graph.nodes:
            if node.lane not in lane_map:
                lane_map[node.lane] = []
            lane_map[node.lane].append(node)

        active_lanes = [l for l in graph.lanes if l in lane_map] or ["General Department"]

        lane_height = 180
        total_lanes = len(active_lanes)
        node_spacing_x = 180
        canvas_width = max(800, 260 + len(graph.nodes) * node_spacing_x)
        canvas_height = 80 + total_lanes * lane_height + 40

        lane_y_coords = {lane: 80 + idx * lane_height for idx, lane in enumerate(active_lanes)}

        node_coords = {}
        curr_x = 220
        for node in graph.nodes:
            lane_top = lane_y_coords.get(node.lane, 80)
            if node.type in ["startEvent", "endEvent"]:
                w, h = 36, 36
            elif "gateway" in node.type.lower():
                w, h = 50, 50
            else:
                w, h = 130, 80
            y = lane_top + (lane_height - h) // 2
            node_coords[node.id] = {"x": curr_x, "y": y, "w": w, "h": h}
            curr_x += node_spacing_x

        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            '                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
            '                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
            '                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
            '                  id="Definitions_PMGA"',
            '                  targetNamespace="http://pmga.local/bpmn">',
            '  <bpmn:collaboration id="Collaboration_PMGA">',
            f'    <bpmn:participant id="Participant_PMGA" name="{_esc(graph.process_title)}" processRef="Process_PMGA" />',
            '  </bpmn:collaboration>',
            '  <bpmn:process id="Process_PMGA" isExecutable="false">',
            '    <bpmn:laneSet id="LaneSet_PMGA">'
        ]

        for idx, lane in enumerate(active_lanes):
            lane_id = f"Lane_{idx+1}"
            xml_parts.append(f'      <bpmn:lane id="{lane_id}" name="{_esc(lane)}">')
            for node in graph.nodes:
                if node.lane == lane:
                    xml_parts.append(f'        <bpmn:flowNodeRef>{node.id}</bpmn:flowNodeRef>')
            xml_parts.append('      </bpmn:lane>')
        xml_parts.append('    </bpmn:laneSet>')

        for node in graph.nodes:
            tag = f"bpmn:{node.type}"
            in_flows = [e.id for e in graph.edges if e.targetRef == node.id]
            out_flows = [e.id for e in graph.edges if e.sourceRef == node.id]

            if not in_flows and not out_flows:
                xml_parts.append(f'    <{tag} id="{node.id}" name="{_esc(node.name)}" />')
            else:
                xml_parts.append(f'    <{tag} id="{node.id}" name="{_esc(node.name)}">')
                for f_in in in_flows:
                    xml_parts.append(f'      <bpmn:incoming>{f_in}</bpmn:incoming>')
                for f_out in out_flows:
                    xml_parts.append(f'      <bpmn:outgoing>{f_out}</bpmn:outgoing>')
                xml_parts.append(f'    </{tag}>')

        for edge in graph.edges:
            name_attr = f'name="{_esc(edge.name)}"' if edge.name else ''
            xml_parts.append(
                f'    <bpmn:sequenceFlow id="{edge.id}" sourceRef="{edge.sourceRef}" targetRef="{edge.targetRef}" {name_attr} />'
            )

        xml_parts.append('  </bpmn:process>')
        xml_parts.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_PMGA">')
        xml_parts.append('    <bpmndi:BPMNPlane id="BPMNPlane_PMGA" bpmnElement="Collaboration_PMGA">')
        xml_parts.append(f'      <bpmndi:BPMNShape id="Participant_PMGA_di" bpmnElement="Participant_PMGA" isHorizontal="true">')
        xml_parts.append(f'        <dc:Bounds x="160" y="80" width="{canvas_width}" height="{canvas_height - 80}" />')
        xml_parts.append('      </bpmndi:BPMNShape>')

        for idx, lane in enumerate(active_lanes):
            lane_id = f"Lane_{idx+1}"
            ly = lane_y_coords[lane]
            xml_parts.append(f'      <bpmndi:BPMNShape id="{lane_id}_di" bpmnElement="{lane_id}" isHorizontal="true">')
            xml_parts.append(f'        <dc:Bounds x="190" y="{ly}" width="{canvas_width - 30}" height="{lane_height}" />')
            xml_parts.append('      </bpmndi:BPMNShape>')

        for node in graph.nodes:
            c = node_coords[node.id]
            xml_parts.append(f'      <bpmndi:BPMNShape id="{node.id}_di" bpmnElement="{node.id}">')
            xml_parts.append(f'        <dc:Bounds x="{c["x"]}" y="{c["y"]}" width="{c["w"]}" height="{c["h"]}" />')
            xml_parts.append('      </bpmndi:BPMNShape>')

        for edge in graph.edges:
            s_c = node_coords.get(edge.sourceRef)
            t_c = node_coords.get(edge.targetRef)
            if s_c and t_c:
                s_x, s_y = s_c["x"] + s_c["w"], s_c["y"] + s_c["h"] // 2
                t_x, t_y = t_c["x"], t_c["y"] + t_c["h"] // 2
                xml_parts.append(f'      <bpmndi:BPMNEdge id="{edge.id}_di" bpmnElement="{edge.id}">')
                xml_parts.append(f'        <di:waypoint x="{s_x}" y="{s_y}" />')
                xml_parts.append(f'        <di:waypoint x="{t_x}" y="{t_y}" />')
                xml_parts.append('      </bpmndi:BPMNEdge>')

        xml_parts.append('    </bpmndi:BPMNPlane>')
        xml_parts.append('  </bpmndi:BPMNDiagram>')
        xml_parts.append('</bpmn:definitions>')

        return "\n".join(xml_parts)

def _esc(val: Optional[str]) -> str:
    if not val:
        return ""
    return (
        str(val)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
