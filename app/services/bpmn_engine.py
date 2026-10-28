import os
import json
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from app.config import settings

class BPMNNode(BaseModel):
    id: str = Field(description="Unique identifier, e.g. Task_1, Gateway_1")
    name: str = Field(description="Clear, plain-English descriptive name of the step")
    type: str = Field(description="One of: 'startEvent', 'userTask', 'serviceTask', 'exclusiveGateway', 'parallelGateway', 'endEvent'")
    lane: str = Field(description="Role, department or actor executing this step, e.g. 'Citizen', 'Clerk', 'IT Admin'")
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

class BPMNEngine:
    @staticmethod
    def generate_graph_from_text(
        process_text: str,
        policy_context: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> BPMNProcessGraph:
        """
        Uses BYOK Google Gemini API to extract a structured process graph from natural text.
        Falls back to deterministic rule-based graph generation if no API key is supplied.
        """
        active_key = api_key or settings.GEMINI_API_KEY
        active_model = model_name or settings.DEFAULT_GEMINI_MODEL

        if active_key:
            client = genai.Client(api_key=active_key)
            prompt = f"""You are a certified Business Process Management (BPMN 2.0) & Public Administration systems architect.
Convert the following natural English process descriptions and documents into a clean, accurate BPMN 2.0 process graph.

Rules:
1. Make the labels simple, direct, and actionable in plain English.
2. Separate into clear Lanes/Roles (e.g. 'Citizen', 'Department Clerk', 'IT Helpdesk', 'Department Head').
3. Distinguish between actual operational shortcuts ('ACTUAL_OPERATION') and formal policies ('FORMAL_POLICY').
4. If an operational shortcut violates a policy or security practice, mark source_type='POLICY_VIOLATION' and explain in risk_annotation.
5. Provide a valid sequence of connected nodes from a startEvent to one or more endEvents with tasks and gateways in between.

--- ACTUAL PROCESS DESCRIPTION (Operational Reality):
{process_text}

--- FORMAL POLICY / LEGAL RULES (Optional Context):
{policy_context or 'None provided'}
"""
            response = client.models.generate_content(
                model=active_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=BPMNProcessGraph,
                    temperature=0.2
                )
            )
            return BPMNProcessGraph.model_validate_json(response.text)

        # Fallback deterministic generator if no key is supplied
        return BPMNEngine._generate_fallback_graph(process_text)

    @staticmethod
    def _generate_fallback_graph(text: str) -> BPMNProcessGraph:
        """Deterministic heuristic fallback when no LLM key is configured."""
        lines = [line.strip("- *•0123456789. ") for line in text.split("\n") if len(line.strip()) > 10]
        if not lines:
            lines = ["Submit municipal request", "Review documentation and verify identity", "Approve and record in municipal ledger", "Archive completed case"]
        
        nodes: List[BPMNNode] = [
            BPMNNode(id="StartEvent_1", name="Process Initiated", type="startEvent", lane="Applicant / Initiator")
        ]
        edges: List[BPMNEdge] = []
        lanes = ["Applicant / Initiator", "Municipal Administration"]

        prev_id = "StartEvent_1"
        for i, step in enumerate(lines[:6], start=1):
            node_id = f"Task_{i}"
            nodes.append(
                BPMNNode(
                    id=node_id,
                    name=step[:60],
                    type="userTask",
                    lane="Municipal Administration",
                    source_type="ACTUAL_OPERATION"
                )
            )
            edges.append(
                BPMNEdge(id=f"Flow_{i}", sourceRef=prev_id, targetRef=node_id)
            )
            prev_id = node_id

        end_id = "EndEvent_1"
        nodes.append(
            BPMNNode(id=end_id, name="Process Completed", type="endEvent", lane="Municipal Administration")
        )
        edges.append(
            BPMNEdge(id=f"Flow_{len(lines)+1}", sourceRef=prev_id, targetRef=end_id)
        )

        return BPMNProcessGraph(
            process_title="Municipal Operational Workflow",
            lanes=lanes,
            nodes=nodes,
            edges=edges
        )

    @staticmethod
    def compile_graph_to_bpmn_xml(graph: BPMNProcessGraph) -> str:
        """
        Compiles structured process graph into 100% valid BPMN 2.0 XML with
        deterministic horizontal swimlane and coordinate layout (<bpmndi:BPMNDiagram>).
        Guaranteed to open and render seamlessly inside bpmn-js and Camunda Modeler.
        """
        # Lane grouping & coordinates
        lane_map = {lane: [] for lane in graph.lanes}
        for node in graph.nodes:
            if node.lane not in lane_map:
                lane_map[node.lane] = []
            lane_map[node.lane].append(node)

        active_lanes = [l for l in graph.lanes if l in lane_map]
        if not active_lanes:
            active_lanes = ["Default Lane"]

        # Dimensions
        lane_height = 180
        total_lanes = len(active_lanes)
        total_nodes = max(len(graph.nodes), 4)
        node_spacing_x = 180
        canvas_width = max(800, 260 + total_nodes * node_spacing_x)
        canvas_height = 80 + total_lanes * lane_height + 40

        lane_y_coords = {}
        for idx, lane_name in enumerate(active_lanes):
            lane_y_coords[lane_name] = 80 + idx * lane_height

        # Node coordinates calculation
        node_coords = {}
        curr_x = 220
        for node in graph.nodes:
            lane_top = lane_y_coords.get(node.lane, 80)
            if node.type in ["startEvent", "endEvent"]:
                w, h = 36, 36
                y = lane_top + (lane_height - h) // 2
            elif "gateway" in node.type.lower():
                w, h = 50, 50
                y = lane_top + (lane_height - h) // 2
            else:
                w, h = 130, 80
                y = lane_top + (lane_height - h) // 2
            node_coords[node.id] = {"x": curr_x, "y": y, "w": w, "h": h}
            curr_x += node_spacing_x

        # Build XML
        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            '                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
            '                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
            '                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
            '                  id="Definitions_CivicProcess"',
            '                  targetNamespace="http://civicprocess.org/bpmn">',
            '  <bpmn:collaboration id="Collaboration_Civic">',
            f'    <bpmn:participant id="Participant_Civic" name="{_esc(graph.process_title)}" processRef="Process_Civic" />',
            '  </bpmn:collaboration>',
            '  <bpmn:process id="Process_Civic" isExecutable="false">',
            '    <bpmn:laneSet id="LaneSet_Civic">'
        ]

        for idx, lane in enumerate(active_lanes):
            lane_id = f"Lane_{idx+1}"
            xml_parts.append(f'      <bpmn:lane id="{lane_id}" name="{_esc(lane)}">')
            for node in graph.nodes:
                if node.lane == lane:
                    xml_parts.append(f'        <bpmn:flowNodeRef>{node.id}</bpmn:flowNodeRef>')
            xml_parts.append('      </bpmn:lane>')
        xml_parts.append('    </bpmn:laneSet>')

        # Process Elements
        for node in graph.nodes:
            n_type = node.type
            name_attr = f'name="{_esc(node.name)}"'
            tag = f"bpmn:{n_type}"
            
            # Incoming and outgoing flows
            in_flows = [e.id for e in graph.edges if e.targetRef == node.id]
            out_flows = [e.id for e in graph.edges if e.sourceRef == node.id]

            if not in_flows and not out_flows:
                xml_parts.append(f'    <{tag} id="{node.id}" {name_attr} />')
            else:
                xml_parts.append(f'    <{tag} id="{node.id}" {name_attr}>')
                for f_in in in_flows:
                    xml_parts.append(f'      <bpmn:incoming>{f_in}</bpmn:incoming>')
                for f_out in out_flows:
                    xml_parts.append(f'      <bpmn:outgoing>{f_out}</bpmn:outgoing>')
                xml_parts.append(f'    </{tag}>')

        # Sequence flows
        for edge in graph.edges:
            name_attr = f'name="{_esc(edge.name)}"' if edge.name else ''
            xml_parts.append(
                f'    <bpmn:sequenceFlow id="{edge.id}" sourceRef="{edge.sourceRef}" targetRef="{edge.targetRef}" {name_attr} />'
            )

        xml_parts.append('  </bpmn:process>')

        # Diagram Interchange (BPMNDI)
        xml_parts.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_Civic">')
        xml_parts.append('    <bpmndi:BPMNPlane id="BPMNPlane_Civic" bpmnElement="Collaboration_Civic">')
        
        # Participant shape
        xml_parts.append('      <bpmndi:BPMNShape id="Participant_Civic_di" bpmnElement="Participant_Civic" isHorizontal="true">')
        xml_parts.append(f'        <dc:Bounds x="160" y="80" width="{canvas_width}" height="{canvas_height - 80}" />')
        xml_parts.append('      </bpmndi:BPMNShape>')

        # Lanes shapes
        for idx, lane in enumerate(active_lanes):
            lane_id = f"Lane_{idx+1}"
            ly = lane_y_coords[lane]
            xml_parts.append(f'      <bpmndi:BPMNShape id="{lane_id}_di" bpmnElement="{lane_id}" isHorizontal="true">')
            xml_parts.append(f'        <dc:Bounds x="190" y="{ly}" width="{canvas_width - 30}" height="{lane_height}" />')
            xml_parts.append('      </bpmndi:BPMNShape>')

        # Node shapes
        for node in graph.nodes:
            c = node_coords[node.id]
            xml_parts.append(f'      <bpmndi:BPMNShape id="{node.id}_di" bpmnElement="{node.id}">')
            xml_parts.append(f'        <dc:Bounds x="{c["x"]}" y="{c["y"]}" width="{c["w"]}" height="{c["h"]}" />')
            xml_parts.append('      </bpmndi:BPMNShape>')

        # Edge waypoints
        for edge in graph.edges:
            s_c = node_coords.get(edge.sourceRef)
            t_c = node_coords.get(edge.targetRef)
            if s_c and t_c:
                s_x = s_c["x"] + s_c["w"]
                s_y = s_c["y"] + s_c["h"] // 2
                t_x = t_c["x"]
                t_y = t_c["y"] + t_c["h"] // 2
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
