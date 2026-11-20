"""Bidirectional FIM-BPMN 2.0 JSON Intermediate Representation (IR) & XML Engine.

Implements:
- Canonical JSON IR (`BPMNProcessIR`) supporting FIM-BPMN 2.0 elements:
  Pools, Lanes, Collapsed External Pools, Tasks (`userTask`, `serviceTask`,
  `callActivity`), Gateways (`exclusiveGateway`, `parallelGateway`), Events
  (`startEvent`, `endEvent`), `dataObjectReference`, `dataStoreReference`,
  data associations, and `<pmga:annotations>` gap/system bindings.
- Forward compiler (`compile_ir_to_bpmn_xml`) with cycle-safe 2D layered
  topological layout and XML-escaped attributes.
- XXE-safe, namespace-agnostic reverse parser (`parse_bpmn_xml_to_ir`).
- Plain-English process description & missing-metadata detector
  (`describe_bpmn_for_user`) for technical diagram-first users.
- Atomic IR patcher (`apply_ir_patch`) that modifies targeted nodes/flows
  without regenerating or wiping untouched user edits.
- Clean standalone SVG generator (`render_ir_to_svg`) for HTML/WeasyPrint PDFs.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from lxml import etree
from pydantic import BaseModel, Field

from app.errors import BPMNParseError, ValidationError
from app.services.bpmn_engine import BPMNEdge, BPMNNode, BPMNProcessGraph

logger = logging.getLogger(__name__)

StepType = Literal[
    "startEvent",
    "userTask",
    "serviceTask",
    "exclusiveGateway",
    "parallelGateway",
    "endEvent",
    "callActivity",
]


class BPMNDataObject(BaseModel):
    id: str
    name: str
    kind: Literal["dataObject", "dataStore"] = "dataObject"
    sensitivity: Optional[Literal["Normal", "High", "Very High"]] = None
    linked_step_ids: List[str] = Field(default_factory=list)


class BPMNStep(BaseModel):
    id: str
    name: str
    type: StepType = "userTask"
    lane: str = "Department Staff"
    system_name: Optional[str] = None
    data_in: List[str] = Field(default_factory=list)
    data_out: List[str] = Field(default_factory=list)
    sub_process_id: Optional[str] = None
    gap_ids: List[str] = Field(default_factory=list)
    source_type: str = "ACTUAL_OPERATION"
    risk_annotation: Optional[str] = None


class BPMNFlow(BaseModel):
    id: str
    sourceRef: str
    targetRef: str
    name: Optional[str] = None
    is_default: bool = False


class BPMNCollapsedPool(BaseModel):
    id: str
    name: str
    message_flows: List[Dict[str, str]] = Field(default_factory=list)


class BPMNProcessIR(BaseModel):
    process_id: str = "Process_PMGA"
    process_title: str = "Municipal Business Process"
    pool_name: Optional[str] = None
    trigger_event: Optional[str] = None
    lanes: List[str] = Field(default_factory=list)
    collapsed_pools: List[BPMNCollapsedPool] = Field(default_factory=list)
    steps: List[BPMNStep] = Field(default_factory=list)
    flows: List[BPMNFlow] = Field(default_factory=list)
    data_objects: List[BPMNDataObject] = Field(default_factory=list)

    def to_legacy_graph(self) -> BPMNProcessGraph:
        """Convert to BPMNProcessGraph for compatibility with MateiEvaluator and BPMNVerifier."""
        lanes = list(self.lanes) or sorted({s.lane for s in self.steps if s.lane}) or ["Department Staff"]
        nodes = [
            BPMNNode(
                id=s.id,
                name=s.name,
                type="serviceTask" if s.type == "callActivity" else s.type,
                lane=s.lane,
                source_type=s.source_type,
                risk_annotation=s.risk_annotation,
            )
            for s in self.steps
        ]
        edges = [
            BPMNEdge(id=f.id, sourceRef=f.sourceRef, targetRef=f.targetRef, name=f.name)
            for f in self.flows
        ]
        return BPMNProcessGraph(
            process_title=self.process_title,
            lanes=lanes,
            nodes=nodes,
            edges=edges,
        )


def _xml_esc(val: Optional[str]) -> str:
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


def _safe_xml_id(raw: str, prefix: str = "El") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]", "_", (raw or "").strip())
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:80]


def _strip_ns(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _compute_layout(ir: BPMNProcessIR) -> Dict[str, Any]:
    """Compute cycle-safe 2D layered coordinates for lanes, steps, and data objects."""
    active_lanes = list(ir.lanes)
    for step in ir.steps:
        if step.lane and step.lane not in active_lanes:
            active_lanes.append(step.lane)
    if not active_lanes:
        active_lanes = ["Department Staff"]

    step_ids = {s.id for s in ir.steps}
    adj: Dict[str, List[str]] = {s.id: [] for s in ir.steps}
    for flow in ir.flows:
        if flow.sourceRef in adj and flow.targetRef in step_ids:
            adj[flow.sourceRef].append(flow.targetRef)

    layer_of: Dict[str, int] = {}
    queue = [s.id for s in ir.steps if s.type == "startEvent"]
    if not queue and ir.steps:
        queue = [ir.steps[0].id]
    for sid in queue:
        layer_of[sid] = 0

    visited = set(queue)
    head = 0
    while head < len(queue):
        curr = queue[head]
        head += 1
        curr_layer = layer_of[curr]
        for nxt in adj.get(curr, []):
            if nxt not in visited:
                visited.add(nxt)
                layer_of[nxt] = curr_layer + 1
                queue.append(nxt)

    fallback_layer = (max(layer_of.values()) + 1) if layer_of else 0
    for s in ir.steps:
        if s.id not in layer_of:
            layer_of[s.id] = fallback_layer
            fallback_layer += 1

    slot_counts: Dict[Tuple[str, int], int] = {}
    step_slot: Dict[str, int] = {}
    lane_max_stack: Dict[str, int] = {l: 1 for l in active_lanes}
    for s in ir.steps:
        key = (s.lane, layer_of[s.id])
        idx = slot_counts.get(key, 0)
        slot_counts[key] = idx + 1
        step_slot[s.id] = idx
        lane_max_stack[s.lane] = max(lane_max_stack.get(s.lane, 1), idx + 1)

    has_data_in_lane: Dict[str, bool] = {l: False for l in active_lanes}
    data_anchor_step: Dict[str, str] = {}
    for dobj in ir.data_objects:
        if dobj.linked_step_ids:
            data_anchor_step[dobj.id] = dobj.linked_step_ids[0]
        else:
            for s in ir.steps:
                if dobj.id in s.data_in or dobj.id in s.data_out or dobj.name in s.data_in or dobj.name in s.data_out:
                    data_anchor_step[dobj.id] = s.id
                    break
    step_map = {s.id: s for s in ir.steps}
    for dobj in ir.data_objects:
        sid = data_anchor_step.get(dobj.id)
        if sid and sid in step_map:
            has_data_in_lane[step_map[sid].lane] = True

    base_row_height = 145
    lane_heights: Dict[str, int] = {}
    for l in active_lanes:
        extra_data_space = 55 if has_data_in_lane.get(l) else 0
        lane_heights[l] = max(185, lane_max_stack.get(l, 1) * base_row_height + 25 + extra_data_space)

    pool_top = 190 if ir.collapsed_pools else 80
    lane_y: Dict[str, int] = {}
    running_y = pool_top
    for l in active_lanes:
        lane_y[l] = running_y
        running_y += lane_heights[l]

    max_layer = max(layer_of.values()) if layer_of else 0
    node_spacing_x = 195
    canvas_width = max(900, 300 + (max_layer + 1) * node_spacing_x)
    canvas_height = running_y + 40

    step_coords: Dict[str, Dict[str, int]] = {}
    for s in ir.steps:
        l_top = lane_y.get(s.lane, pool_top)
        lh = lane_heights.get(s.lane, 185)
        usable_h = lh - (48 if has_data_in_lane.get(s.lane) else 0)
        stack_cnt = slot_counts.get((s.lane, layer_of[s.id]), 1)
        slot_idx = step_slot[s.id]

        if s.type in ("startEvent", "endEvent"):
            w, h = 36, 36
        elif "Gateway" in s.type:
            w, h = 50, 50
        else:
            w, h = 136, 80

        x = 230 + layer_of[s.id] * node_spacing_x + (136 - w) // 2
        band_h = usable_h // stack_cnt
        y = l_top + slot_idx * band_h + (band_h - h) // 2
        step_coords[s.id] = {"x": x, "y": y, "w": w, "h": h}

    # Position data objects neatly at the bottom strip of their anchored lane
    data_coords: Dict[str, Dict[str, int]] = {}
    lane_data_offset: Dict[str, int] = {}
    for idx, dobj in enumerate(ir.data_objects):
        sid = data_anchor_step.get(dobj.id)
        if sid and sid in step_coords and sid in step_map:
            sc = step_coords[sid]
            l_name = step_map[sid].lane
            l_bottom = lane_y[l_name] + lane_heights[l_name]
            dx = sc["x"] + sc["w"] // 2 - 18
            dy = l_bottom - 58
        else:
            l_name = active_lanes[0]
            offset = lane_data_offset.get(l_name, 0)
            lane_data_offset[l_name] = offset + 95
            dx = 240 + offset
            dy = running_y - 60
        w, h = (46, 46) if dobj.kind == "dataStore" else (36, 50)
        data_coords[dobj.id] = {"x": dx, "y": dy, "w": w, "h": h}

    return {
        "active_lanes": active_lanes,
        "pool_top": pool_top,
        "lane_heights": lane_heights,
        "lane_y": lane_y,
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "step_coords": step_coords,
        "data_coords": data_coords,
    }


def compile_ir_to_bpmn_xml(ir: BPMNProcessIR) -> str:
    """Compile BPMNProcessIR into standards-compliant FIM-BPMN 2.0 XML with 2D layout."""
    started = time.perf_counter()
    layout = _compute_layout(ir)
    active_lanes: List[str] = layout["active_lanes"]
    pool_top: int = layout["pool_top"]
    lane_heights: Dict[str, int] = layout["lane_heights"]
    lane_y: Dict[str, int] = layout["lane_y"]
    canvas_width: int = layout["canvas_width"]
    canvas_height: int = layout["canvas_height"]
    step_coords: Dict[str, Dict[str, int]] = layout["step_coords"]
    data_coords: Dict[str, Dict[str, int]] = layout["data_coords"]

    proc_id = _safe_xml_id(ir.process_id or "Process_PMGA", "Process")
    pool_label = ir.pool_name or ir.process_title or "Municipal Administration"

    # Build lookup for data objects by id or lower(name)
    data_by_key: Dict[str, BPMNDataObject] = {}
    for d in ir.data_objects:
        data_by_key[d.id] = d
        data_by_key[d.name.strip().lower()] = d

    xml: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        '                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
        '                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
        '                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
        '                  xmlns:pmga="http://pmga.local/schema/1.0"',
        f'                  id="Definitions_{proc_id}"',
        '                  targetNamespace="http://pmga.local/bpmn">',
        '  <bpmn:collaboration id="Collaboration_PMGA">',
        f'    <bpmn:participant id="Participant_Main" name="{_xml_esc(pool_label)}" processRef="{proc_id}" />',
    ]

    for cp in ir.collapsed_pools:
        cp_id = _safe_xml_id(cp.id, "Participant")
        xml.append(f'    <bpmn:participant id="{cp_id}" name="{_xml_esc(cp.name)}" />')
        for mf in cp.message_flows:
            mf_id = _safe_xml_id(mf.get("id") or f"Msg_{cp_id}", "Msg")
            step_ref = _safe_xml_id(mf.get("step_id") or "", "Step")
            direction = mf.get("direction", "to_process")
            src, tgt = (cp_id, step_ref) if direction == "to_process" else (step_ref, cp_id)
            mf_name = f' name="{_xml_esc(mf.get("name"))}"' if mf.get("name") else ""
            if step_ref:
                xml.append(f'    <bpmn:messageFlow id="{mf_id}" sourceRef="{src}" targetRef="{tgt}"{mf_name} />')

    xml.append('  </bpmn:collaboration>')

    # Global dataStore definitions when dataStores are used
    for d in ir.data_objects:
        if d.kind == "dataStore":
            ds_id = _safe_xml_id(f"DS_{d.id}", "DS")
            xml.append(f'  <bpmn:dataStore id="{ds_id}" name="{_xml_esc(d.name)}" />')

    xml.append(f'  <bpmn:process id="{proc_id}" name="{_xml_esc(ir.process_title)}" isExecutable="false">')
    xml.append('    <bpmn:laneSet id="LaneSet_PMGA">')
    for idx, lane_name in enumerate(active_lanes, start=1):
        lane_id = f"Lane_{idx}"
        xml.append(f'      <bpmn:lane id="{lane_id}" name="{_xml_esc(lane_name)}">')
        for s in ir.steps:
            if s.lane == lane_name:
                xml.append(f'        <bpmn:flowNodeRef>{_xml_esc(s.id)}</bpmn:flowNodeRef>')
        xml.append('      </bpmn:lane>')
    xml.append('    </bpmn:laneSet>')

    # Emit dataObject declarations & references inside process
    for d in ir.data_objects:
        if d.kind == "dataObject":
            do_def_id = _safe_xml_id(f"DO_{d.id}", "DO")
            xml.append(f'    <bpmn:dataObject id="{do_def_id}" />')
            xml.append(
                f'    <bpmn:dataObjectReference id="{_xml_esc(d.id)}" name="{_xml_esc(d.name)}" dataObjectRef="{do_def_id}">'
            )
        else:
            ds_id = _safe_xml_id(f"DS_{d.id}", "DS")
            xml.append(
                f'    <bpmn:dataStoreReference id="{_xml_esc(d.id)}" name="{_xml_esc(d.name)}" dataStoreRef="{ds_id}">'
            )
        if d.sensitivity:
            xml.append('      <bpmn:extensionElements>')
            xml.append(f'        <pmga:dataMeta sensitivity="{_xml_esc(d.sensitivity)}" />')
            xml.append('      </bpmn:extensionElements>')
        tag_close = "bpmn:dataObjectReference" if d.kind == "dataObject" else "bpmn:dataStoreReference"
        xml.append(f'    </{tag_close}>')

    data_assoc_di: List[Tuple[str, str, str, str]] = []  # (assoc_id, source_id, target_id, kind)

    for s in ir.steps:
        tag = f"bpmn:{s.type}"
        in_flows = [f.id for f in ir.flows if f.targetRef == s.id]
        out_flows = [f.id for f in ir.flows if f.sourceRef == s.id]
        default_flow = next((f.id for f in ir.flows if f.sourceRef == s.id and f.is_default), None)
        default_attr = f' default="{_xml_esc(default_flow)}"' if default_flow and "Gateway" in s.type else ""

        has_ext = bool(
            s.system_name
            or s.sub_process_id
            or s.gap_ids
            or s.risk_annotation
            or (s.source_type and s.source_type != "ACTUAL_OPERATION")
        )
        # Resolve data_in / data_out refs to valid BPMNDataObject IDs
        resolved_in: List[str] = []
        for item in s.data_in:
            d = data_by_key.get(item) or data_by_key.get(item.strip().lower())
            if d and d.id not in resolved_in:
                resolved_in.append(d.id)
        resolved_out: List[str] = []
        for item in s.data_out:
            d = data_by_key.get(item) or data_by_key.get(item.strip().lower())
            if d and d.id not in resolved_out:
                resolved_out.append(d.id)

        is_task = s.type in ("userTask", "serviceTask", "callActivity")
        has_io = is_task and bool(resolved_in or resolved_out)

        if not in_flows and not out_flows and not has_ext and not has_io:
            xml.append(f'    <{tag} id="{_xml_esc(s.id)}" name="{_xml_esc(s.name)}"{default_attr} />')
        else:
            xml.append(f'    <{tag} id="{_xml_esc(s.id)}" name="{_xml_esc(s.name)}"{default_attr}>')
            if has_ext:
                xml.append('      <bpmn:extensionElements>')
                xml.append(
                    '        <pmga:annotations'
                    f' systemName="{_xml_esc(s.system_name or "")}"'
                    f' subProcessId="{_xml_esc(s.sub_process_id or "")}"'
                    f' gapIds="{_xml_esc(",".join(s.gap_ids))}"'
                    f' sourceType="{_xml_esc(s.source_type)}"'
                    f' riskAnnotation="{_xml_esc(s.risk_annotation or "")}" />'
                )
                xml.append('      </bpmn:extensionElements>')
            for fid in in_flows:
                xml.append(f'      <bpmn:incoming>{_xml_esc(fid)}</bpmn:incoming>')
            for fid in out_flows:
                xml.append(f'      <bpmn:outgoing>{_xml_esc(fid)}</bpmn:outgoing>')

            if has_io:
                prop_id = f"Property_{_safe_xml_id(s.id, 'Prop')}"
                xml.append(f'      <bpmn:property id="{prop_id}" name="__targetRef_placeholder" />')
                for idx_in, did in enumerate(resolved_in, start=1):
                    assoc_id = f"DataIn_{_safe_xml_id(s.id, 'S')}_{idx_in}"
                    xml.append(f'      <bpmn:dataInputAssociation id="{assoc_id}">')
                    xml.append(f'        <bpmn:sourceRef>{_xml_esc(did)}</bpmn:sourceRef>')
                    xml.append(f'        <bpmn:targetRef>{prop_id}</bpmn:targetRef>')
                    xml.append('      </bpmn:dataInputAssociation>')
                    data_assoc_di.append((assoc_id, did, s.id, "in"))
                for idx_out, did in enumerate(resolved_out, start=1):
                    assoc_id = f"DataOut_{_safe_xml_id(s.id, 'S')}_{idx_out}"
                    xml.append(f'      <bpmn:dataOutputAssociation id="{assoc_id}">')
                    xml.append(f'        <bpmn:targetRef>{_xml_esc(did)}</bpmn:targetRef>')
                    xml.append('      </bpmn:dataOutputAssociation>')
                    data_assoc_di.append((assoc_id, s.id, did, "out"))
            xml.append(f'    </{tag}>')

    for f in ir.flows:
        name_attr = f' name="{_xml_esc(f.name)}"' if f.name else ""
        xml.append(
            f'    <bpmn:sequenceFlow id="{_xml_esc(f.id)}" sourceRef="{_xml_esc(f.sourceRef)}" targetRef="{_xml_esc(f.targetRef)}"{name_attr} />'
        )

    xml.append('  </bpmn:process>')

    # BPMNDI Section
    xml.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_PMGA">')
    xml.append('    <bpmndi:BPMNPlane id="BPMNPlane_PMGA" bpmnElement="Collaboration_PMGA">')

    # Collapsed external pools above main pool
    for idx_cp, cp in enumerate(ir.collapsed_pools):
        cp_id = _safe_xml_id(cp.id, "Participant")
        cy = 30 + idx_cp * 75
        xml.append(f'      <bpmndi:BPMNShape id="{cp_id}_di" bpmnElement="{cp_id}" isHorizontal="true">')
        xml.append(f'        <dc:Bounds x="160" y="{cy}" width="{canvas_width}" height="60" />')
        xml.append('      </bpmndi:BPMNShape>')

    xml.append('      <bpmndi:BPMNShape id="Participant_Main_di" bpmnElement="Participant_Main" isHorizontal="true">')
    xml.append(f'        <dc:Bounds x="160" y="{pool_top}" width="{canvas_width}" height="{canvas_height - pool_top}" />')
    xml.append('      </bpmndi:BPMNShape>')

    for idx, lane_name in enumerate(active_lanes, start=1):
        lane_id = f"Lane_{idx}"
        ly = lane_y[lane_name]
        lh = lane_heights[lane_name]
        xml.append(f'      <bpmndi:BPMNShape id="{lane_id}_di" bpmnElement="{lane_id}" isHorizontal="true">')
        xml.append(f'        <dc:Bounds x="190" y="{ly}" width="{canvas_width - 30}" height="{lh}" />')
        xml.append('      </bpmndi:BPMNShape>')

    for s in ir.steps:
        c = step_coords[s.id]
        marker_attr = ' isMarkerVisible="true"' if s.type == "exclusiveGateway" else ""
        xml.append(f'      <bpmndi:BPMNShape id="{_xml_esc(s.id)}_di" bpmnElement="{_xml_esc(s.id)}"{marker_attr}>')
        xml.append(f'        <dc:Bounds x="{c["x"]}" y="{c["y"]}" width="{c["w"]}" height="{c["h"]}" />')
        xml.append('      </bpmndi:BPMNShape>')

    for d in ir.data_objects:
        dc_box = data_coords[d.id]
        xml.append(f'      <bpmndi:BPMNShape id="{_xml_esc(d.id)}_di" bpmnElement="{_xml_esc(d.id)}">')
        xml.append(f'        <dc:Bounds x="{dc_box["x"]}" y="{dc_box["y"]}" width="{dc_box["w"]}" height="{dc_box["h"]}" />')
        xml.append('      </bpmndi:BPMNShape>')

    for f in ir.flows:
        sc = step_coords.get(f.sourceRef)
        tc = step_coords.get(f.targetRef)
        if not sc or not tc:
            continue
        xml.append(f'      <bpmndi:BPMNEdge id="{_xml_esc(f.id)}_di" bpmnElement="{_xml_esc(f.id)}">')
        if tc["x"] < sc["x"]:
            sx, sy = sc["x"] + sc["w"] // 2, sc["y"] + sc["h"]
            tx, ty = tc["x"] + tc["w"] // 2, tc["y"] + tc["h"]
            detour_y = max(sy, ty) + 28
            xml.append(f'        <di:waypoint x="{sx}" y="{sy}" />')
            xml.append(f'        <di:waypoint x="{sx}" y="{detour_y}" />')
            xml.append(f'        <di:waypoint x="{tx}" y="{detour_y}" />')
            xml.append(f'        <di:waypoint x="{tx}" y="{ty}" />')
        else:
            sx, sy = sc["x"] + sc["w"], sc["y"] + sc["h"] // 2
            tx, ty = tc["x"], tc["y"] + tc["h"] // 2
            xml.append(f'        <di:waypoint x="{sx}" y="{sy}" />')
            if sy != ty:
                mid_x = (sx + tx) // 2
                xml.append(f'        <di:waypoint x="{mid_x}" y="{sy}" />')
                xml.append(f'        <di:waypoint x="{mid_x}" y="{ty}" />')
            xml.append(f'        <di:waypoint x="{tx}" y="{ty}" />')
        xml.append('      </bpmndi:BPMNEdge>')

    for assoc_id, src_id, tgt_id, _kind in data_assoc_di:
        sc = data_coords.get(src_id) or step_coords.get(src_id)
        tc = step_coords.get(tgt_id) or data_coords.get(tgt_id)
        if sc and tc:
            sx, sy = sc["x"] + sc["w"] // 2, sc["y"] + sc["h"] // 2
            tx, ty = tc["x"] + tc["w"] // 2, tc["y"] + tc["h"] // 2
            xml.append(f'      <bpmndi:BPMNEdge id="{assoc_id}_di" bpmnElement="{assoc_id}">')
            xml.append(f'        <di:waypoint x="{sx}" y="{sy}" />')
            xml.append(f'        <di:waypoint x="{tx}" y="{ty}" />')
            xml.append('      </bpmndi:BPMNEdge>')

    xml.append('    </bpmndi:BPMNPlane>')
    xml.append('  </bpmndi:BPMNDiagram>')
    xml.append('</bpmn:definitions>')

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.debug("Compiled IR->BPMN XML steps=%d flows=%d data=%d duration_ms=%.2f", len(ir.steps), len(ir.flows), len(ir.data_objects), elapsed_ms)
    return "\n".join(xml)


_TASK_TAGS = {
    "task": "userTask",
    "userTask": "userTask",
    "manualTask": "userTask",
    "sendTask": "userTask",
    "receiveTask": "userTask",
    "serviceTask": "serviceTask",
    "scriptTask": "serviceTask",
    "businessRuleTask": "serviceTask",
    "callActivity": "callActivity",
}

_GATEWAY_TAGS = {
    "exclusiveGateway": "exclusiveGateway",
    "eventBasedGateway": "exclusiveGateway",
    "parallelGateway": "parallelGateway",
    "inclusiveGateway": "parallelGateway",
}


def parse_bpmn_xml_to_ir(xml_content: str, fallback_title: str = "Imported BPMN Process") -> BPMNProcessIR:
    """XXE-safe, namespace-agnostic parser translating BPMN 2.0 XML into BPMNProcessIR."""
    if not xml_content or not xml_content.strip():
        raise BPMNParseError("BPMN XML content is empty.")

    # Explicitly reject XML external entities / DTD declarations before parsing
    upper_head = xml_content[:4000].upper()
    if "<!ENTITY" in upper_head or "<!DOCTYPE" in upper_head:
        raise BPMNParseError("DOCTYPE and ENTITY declarations are not permitted in BPMN XML.")

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(xml_content.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError as exc:
        raise BPMNParseError(f"Invalid BPMN XML syntax: {exc}") from exc

    process_elem = None
    process_id = "Process_PMGA"
    process_title = fallback_title
    pool_name: Optional[str] = None
    collapsed_pools: List[BPMNCollapsedPool] = []
    collapsed_ids: Set[str] = set()
    msg_flows: List[Dict[str, str]] = []

    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "process" and process_elem is None:
            process_elem = elem
            process_id = elem.get("id") or process_id
            if elem.get("name"):
                process_title = elem.get("name") or process_title

    if process_elem is None:
        raise BPMNParseError("No <process> element found in BPMN XML.")

    # Inspect collaboration participants and message flows
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "participant":
            pid = elem.get("id") or ""
            pname = elem.get("name") or pid or "External Participant"
            pref = elem.get("processRef")
            if pref and pref == process_id:
                pool_name = pname
                if process_title == fallback_title and pname:
                    process_title = pname
            elif not pref:
                collapsed_pools.append(BPMNCollapsedPool(id=pid or f"Ext_{len(collapsed_pools)+1}", name=pname))
                if pid:
                    collapsed_ids.add(pid)
        elif tag == "messageFlow":
            msg_flows.append({
                "id": elem.get("id") or f"Msg_{len(msg_flows)+1}",
                "name": elem.get("name") or "",
                "sourceRef": elem.get("sourceRef") or "",
                "targetRef": elem.get("targetRef") or "",
            })

    for cp in collapsed_pools:
        for mf in msg_flows:
            if mf["sourceRef"] == cp.id and mf["targetRef"]:
                cp.message_flows.append({
                    "id": mf["id"],
                    "name": mf["name"],
                    "direction": "to_process",
                    "step_id": mf["targetRef"],
                })
            elif mf["targetRef"] == cp.id and mf["sourceRef"]:
                cp.message_flows.append({
                    "id": mf["id"],
                    "name": mf["name"],
                    "direction": "from_process",
                    "step_id": mf["sourceRef"],
                })

    # Lanes
    lanes_map: Dict[str, str] = {}
    discovered_lanes: List[str] = []
    for elem in process_elem.iter():
        if _strip_ns(elem.tag) == "lane":
            lane_name = elem.get("name") or elem.get("id") or "General Process"
            if lane_name not in discovered_lanes:
                discovered_lanes.append(lane_name)
            for child in elem:
                if _strip_ns(child.tag) == "flowNodeRef" and child.text:
                    lanes_map[child.text.strip()] = lane_name

    if not discovered_lanes:
        discovered_lanes = ["General Process"]
    default_lane = discovered_lanes[0]

    # Data objects & data stores
    data_objects_map: Dict[str, BPMNDataObject] = {}
    for child in process_elem:
        ctag = _strip_ns(child.tag)
        if ctag in ("dataObjectReference", "dataStoreReference"):
            did = child.get("id")
            if not did:
                continue
            dname = child.get("name") or did
            sensitivity: Optional[Literal["Normal", "High", "Very High"]] = None
            for ext in child.iter():
                if _strip_ns(ext.tag) == "dataMeta":
                    raw_s = ext.get("sensitivity")
                    if raw_s in ("Normal", "High", "Very High"):
                        sensitivity = raw_s  # type: ignore[assignment]
            data_objects_map[did] = BPMNDataObject(
                id=did,
                name=dname,
                kind="dataStore" if ctag == "dataStoreReference" else "dataObject",
                sensitivity=sensitivity,
            )

    steps: List[BPMNStep] = []
    flows: List[BPMNFlow] = []
    step_ids_seen: Set[str] = set()
    default_flow_ids: Set[str] = set()

    for child in process_elem:
        tag = _strip_ns(child.tag)
        nid = child.get("id")
        if not nid:
            continue
        name = child.get("name") or nid

        step_type: Optional[StepType] = None
        default_source_type = "ACTUAL_OPERATION"
        if tag == "startEvent":
            step_type = "startEvent"
            default_source_type = "FORMAL_POLICY"
        elif tag in ("endEvent", "terminateEndEvent"):
            step_type = "endEvent"
            default_source_type = "FORMAL_POLICY"
        elif tag in _TASK_TAGS:
            step_type = _TASK_TAGS[tag]  # type: ignore[assignment]
        elif tag in _GATEWAY_TAGS:
            step_type = _GATEWAY_TAGS[tag]  # type: ignore[assignment]
            if child.get("default"):
                default_flow_ids.add(child.get("default") or "")
        elif tag == "sequenceFlow":
            s_ref = child.get("sourceRef")
            t_ref = child.get("targetRef")
            flow_name = child.get("name") or None
            if not flow_name:
                for sub in child:
                    if _strip_ns(sub.tag) == "conditionExpression" and sub.text:
                        flow_name = sub.text.strip()
            if s_ref and t_ref:
                flows.append(BPMNFlow(id=nid, sourceRef=s_ref, targetRef=t_ref, name=flow_name))
            continue

        if step_type is None:
            continue

        # Parse extension annotations & data associations
        system_name: Optional[str] = None
        sub_process_id: Optional[str] = None
        gap_ids: List[str] = []
        source_type = default_source_type
        risk_annotation: Optional[str] = None
        data_in: List[str] = []
        data_out: List[str] = []

        for sub in child.iter():
            stag = _strip_ns(sub.tag)
            if stag == "annotations":
                system_name = (sub.get("systemName") or "").strip() or None
                sub_process_id = (sub.get("subProcessId") or "").strip() or None
                raw_gaps = (sub.get("gapIds") or "").strip()
                gap_ids = [g.strip() for g in raw_gaps.split(",") if g.strip()]
                if sub.get("sourceType"):
                    source_type = sub.get("sourceType") or source_type
                risk_annotation = (sub.get("riskAnnotation") or "").strip() or None
            elif stag == "governance":
                if sub.get("sourceType"):
                    source_type = sub.get("sourceType") or source_type
                risk_annotation = (sub.get("riskAnnotation") or "").strip() or risk_annotation
            elif stag == "dataInputAssociation":
                for ref in sub:
                    if _strip_ns(ref.tag) == "sourceRef" and ref.text:
                        did = ref.text.strip()
                        if did not in data_in:
                            data_in.append(did)
                        if did in data_objects_map and nid not in data_objects_map[did].linked_step_ids:
                            data_objects_map[did].linked_step_ids.append(nid)
            elif stag == "dataOutputAssociation":
                for ref in sub:
                    if _strip_ns(ref.tag) == "targetRef" and ref.text:
                        did = ref.text.strip()
                        if did not in data_out:
                            data_out.append(did)
                        if did in data_objects_map and nid not in data_objects_map[did].linked_step_ids:
                            data_objects_map[did].linked_step_ids.append(nid)

        steps.append(
            BPMNStep(
                id=nid,
                name=name,
                type=step_type,
                lane=lanes_map.get(nid, default_lane),
                system_name=system_name,
                data_in=data_in,
                data_out=data_out,
                sub_process_id=sub_process_id,
                gap_ids=gap_ids,
                source_type=source_type,
                risk_annotation=risk_annotation,
            )
        )
        step_ids_seen.add(nid)

    valid_flows: List[BPMNFlow] = []
    for f in flows:
        if f.sourceRef in step_ids_seen and f.targetRef in step_ids_seen:
            f.is_default = f.id in default_flow_ids
            valid_flows.append(f)

    trigger = next((s.name for s in steps if s.type == "startEvent"), None)
    return BPMNProcessIR(
        process_id=process_id,
        process_title=process_title,
        pool_name=pool_name,
        trigger_event=trigger,
        lanes=discovered_lanes,
        collapsed_pools=collapsed_pools,
        steps=steps,
        flows=valid_flows,
        data_objects=list(data_objects_map.values()),
    )


def apply_ir_patch(ir: BPMNProcessIR, operations: List[Dict[str, Any]]) -> BPMNProcessIR:
    """Apply atomic edits to a BPMNProcessIR copy without destroying untouched steps/IDs."""
    patched = ir.model_copy(deep=True)
    steps_by_id: Dict[str, BPMNStep] = {s.id: s for s in patched.steps}
    data_by_id: Dict[str, BPMNDataObject] = {d.id: d for d in patched.data_objects}

    def _next_id(prefix: str, existing: Set[str]) -> str:
        n = len(existing) + 1
        while f"{prefix}_{n}" in existing:
            n += 1
        return f"{prefix}_{n}"

    for op_idx, raw_op in enumerate(operations):
        if not isinstance(raw_op, dict) or not raw_op.get("op"):
            raise ValidationError(f"Patch operation #{op_idx} must be a dict with 'op'.")
        op = str(raw_op["op"]).strip()

        if op == "add_step":
            step_data = dict(raw_op.get("step") or raw_op)
            step_data.pop("op", None)
            step_data.pop("after_step_id", None)
            step_data.pop("before_step_id", None)
            step_data.pop("flow_name", None)
            sid = step_data.get("id") or _next_id("Task", set(steps_by_id.keys()))
            step_data["id"] = sid
            if not step_data.get("name"):
                raise ValidationError("add_step requires a non-empty step 'name'.")
            if step_data.get("lane") and step_data["lane"] not in patched.lanes:
                patched.lanes.append(step_data["lane"])
            new_step = BPMNStep(**step_data)
            patched.steps.append(new_step)
            steps_by_id[sid] = new_step

            after_id = raw_op.get("after_step_id")
            if after_id and after_id in steps_by_id:
                existing_flow_ids = {f.id for f in patched.flows}
                outgoing = [f for f in patched.flows if f.sourceRef == after_id]
                if len(outgoing) == 1 and not raw_op.get("keep_existing_edge"):
                    # Splice cleanly between after_id and its single successor
                    orig = outgoing[0]
                    old_target = orig.targetRef
                    orig.targetRef = sid
                    patched.flows.append(
                        BPMNFlow(
                            id=_next_id("Flow", existing_flow_ids),
                            sourceRef=sid,
                            targetRef=old_target,
                            name=raw_op.get("flow_name"),
                        )
                    )
                else:
                    patched.flows.append(
                        BPMNFlow(
                            id=_next_id("Flow", existing_flow_ids),
                            sourceRef=after_id,
                            targetRef=sid,
                            name=raw_op.get("flow_name"),
                        )
                    )

        elif op == "update_step":
            sid = raw_op.get("step_id") or raw_op.get("id")
            if not sid or sid not in steps_by_id:
                raise ValidationError(f"update_step target '{sid}' not found.")
            target = steps_by_id[sid]
            updates = raw_op.get("updates") or {k: v for k, v in raw_op.items() if k not in ("op", "step_id", "id")}
            for field in ("name", "type", "lane", "system_name", "sub_process_id", "risk_annotation", "source_type"):
                if field in updates and updates[field] is not None:
                    setattr(target, field, updates[field])
            if target.lane and target.lane not in patched.lanes:
                patched.lanes.append(target.lane)
            if "gap_ids" in updates and isinstance(updates["gap_ids"], list):
                target.gap_ids = list(dict.fromkeys(updates["gap_ids"]))

        elif op == "delete_step":
            sid = raw_op.get("step_id") or raw_op.get("id")
            if not sid or sid not in steps_by_id:
                raise ValidationError(f"delete_step target '{sid}' not found.")
            incoming = [f for f in patched.flows if f.targetRef == sid]
            outgoing = [f for f in patched.flows if f.sourceRef == sid]
            patched.steps = [s for s in patched.steps if s.id != sid]
            steps_by_id.pop(sid, None)
            patched.flows = [f for f in patched.flows if f.sourceRef != sid and f.targetRef != sid]
            if len(incoming) == 1 and len(outgoing) == 1:
                existing_flow_ids = {f.id for f in patched.flows}
                patched.flows.append(
                    BPMNFlow(
                        id=_next_id("Flow", existing_flow_ids),
                        sourceRef=incoming[0].sourceRef,
                        targetRef=outgoing[0].targetRef,
                        name=incoming[0].name or outgoing[0].name,
                    )
                )
            for d in patched.data_objects:
                d.linked_step_ids = [x for x in d.linked_step_ids if x != sid]

        elif op == "redirect_flow":
            fid = raw_op.get("flow_id")
            target_flow = next((f for f in patched.flows if f.id == fid), None)
            if not target_flow:
                raise ValidationError(f"redirect_flow target '{fid}' not found.")
            if raw_op.get("sourceRef"):
                if raw_op["sourceRef"] not in steps_by_id:
                    raise ValidationError(f"Unknown sourceRef '{raw_op['sourceRef']}'")
                target_flow.sourceRef = raw_op["sourceRef"]
            if raw_op.get("targetRef"):
                if raw_op["targetRef"] not in steps_by_id:
                    raise ValidationError(f"Unknown targetRef '{raw_op['targetRef']}'")
                target_flow.targetRef = raw_op["targetRef"]
            if "name" in raw_op:
                target_flow.name = raw_op["name"]

        elif op == "add_exception_branch":
            after_id = raw_op.get("after_step_id")
            if not after_id or after_id not in steps_by_id:
                raise ValidationError(f"add_exception_branch step '{after_id}' not found.")
            after_step = steps_by_id[after_id]
            existing_flow_ids = {f.id for f in patched.flows}

            # Create gateway right after `after_step` if it isn't already an exclusiveGateway
            if after_step.type == "exclusiveGateway":
                gw_id = after_step.id
            else:
                gw_id = _next_id("Gate_Exc", set(steps_by_id.keys()))
                gw = BPMNStep(
                    id=gw_id,
                    name=raw_op.get("gateway_name") or "Complete & valid?",
                    type="exclusiveGateway",
                    lane=after_step.lane,
                )
                patched.steps.append(gw)
                steps_by_id[gw_id] = gw
                outgoing = [f for f in patched.flows if f.sourceRef == after_id]
                if outgoing:
                    orig = outgoing[0]
                    orig.sourceRef = gw_id
                    if not orig.name:
                        orig.name = raw_op.get("happy_label") or "Valid"
                patched.flows.append(
                    BPMNFlow(id=_next_id("Flow", existing_flow_ids), sourceRef=after_id, targetRef=gw_id)
                )
                existing_flow_ids = {f.id for f in patched.flows}

            exc_id = raw_op.get("exception_step_id") or _next_id("Task_Exc", set(steps_by_id.keys()))
            exc_lane = raw_op.get("lane") or after_step.lane
            if exc_lane not in patched.lanes:
                patched.lanes.append(exc_lane)
            exc_step = BPMNStep(
                id=exc_id,
                name=raw_op.get("exception_name") or "Request Missing Info / Handle Exception",
                type=raw_op.get("exception_type") or "userTask",
                lane=exc_lane,
            )
            patched.steps.append(exc_step)
            steps_by_id[exc_id] = exc_step
            patched.flows.append(
                BPMNFlow(
                    id=_next_id("Flow", existing_flow_ids),
                    sourceRef=gw_id,
                    targetRef=exc_id,
                    name=raw_op.get("condition_label") or "Missing / Error",
                )
            )
            existing_flow_ids = {f.id for f in patched.flows}
            # Connect exception step back to upstream or to an end event so graph stays deadlock-free
            rejoin_id = raw_op.get("rejoin_step_id")
            if rejoin_id and rejoin_id in steps_by_id:
                patched.flows.append(
                    BPMNFlow(id=_next_id("Flow", existing_flow_ids), sourceRef=exc_id, targetRef=rejoin_id, name="Retry")
                )
            else:
                end_step = next((s for s in patched.steps if s.type == "endEvent"), None)
                if end_step:
                    patched.flows.append(
                        BPMNFlow(id=_next_id("Flow", existing_flow_ids), sourceRef=exc_id, targetRef=end_step.id)
                    )

        elif op == "attach_system":
            sid = raw_op.get("step_id")
            sys_name = (raw_op.get("system_name") or "").strip()
            if not sid or sid not in steps_by_id or not sys_name:
                raise ValidationError("attach_system requires valid 'step_id' and non-empty 'system_name'.")
            steps_by_id[sid].system_name = sys_name

        elif op == "attach_data":
            sid = raw_op.get("step_id")
            dname = (raw_op.get("data_name") or "").strip()
            direction = raw_op.get("direction", "in")
            kind = raw_op.get("kind", "dataObject")
            sensitivity = raw_op.get("sensitivity")
            if not sid or sid not in steps_by_id or not dname:
                raise ValidationError("attach_data requires valid 'step_id' and non-empty 'data_name'.")
            existing_d = next((d for d in patched.data_objects if d.name.lower() == dname.lower()), None)
            if not existing_d:
                did = _next_id("DataStore" if kind == "dataStore" else "DataObj", set(data_by_id.keys()))
                existing_d = BPMNDataObject(
                    id=did,
                    name=dname,
                    kind="dataStore" if kind == "dataStore" else "dataObject",
                    sensitivity=sensitivity,
                    linked_step_ids=[sid],
                )
                patched.data_objects.append(existing_d)
                data_by_id[did] = existing_d
            else:
                if sid not in existing_d.linked_step_ids:
                    existing_d.linked_step_ids.append(sid)
                if sensitivity:
                    existing_d.sensitivity = sensitivity

            target_step = steps_by_id[sid]
            if direction == "out":
                if existing_d.id not in target_step.data_out:
                    target_step.data_out.append(existing_d.id)
            else:
                if existing_d.id not in target_step.data_in:
                    target_step.data_in.append(existing_d.id)

        elif op == "attach_gap":
            gap_id = (raw_op.get("gap_id") or "").strip()
            target_sids = raw_op.get("step_ids") or ([raw_op["step_id"]] if raw_op.get("step_id") else [])
            if not gap_id:
                raise ValidationError("attach_gap requires 'gap_id'.")
            for sid in target_sids:
                if sid in steps_by_id and gap_id not in steps_by_id[sid].gap_ids:
                    steps_by_id[sid].gap_ids.append(gap_id)

        else:
            raise ValidationError(f"Unsupported patch operation: {op!r}")

    return patched


def describe_bpmn_for_user(
    ir: BPMNProcessIR,
    *,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a clear plain-English description, inventory, and missing metadata gaps from BPMNProcessIR."""
    step_map = {s.id: s for s in ir.steps}
    adj: Dict[str, List[BPMNFlow]] = {s.id: [] for s in ir.steps}
    for f in ir.flows:
        if f.sourceRef in adj:
            adj[f.sourceRef].append(f)

    # Topological BFS traversal for readable chronological summary
    ordered_steps: List[BPMNStep] = []
    starts = [s for s in ir.steps if s.type == "startEvent"]
    queue = [s.id for s in starts] if starts else ([ir.steps[0].id] if ir.steps else [])
    seen: Set[str] = set()
    while queue:
        curr_id = queue.pop(0)
        if curr_id in seen or curr_id not in step_map:
            continue
        seen.add(curr_id)
        ordered_steps.append(step_map[curr_id])
        for flow in adj.get(curr_id, []):
            if flow.targetRef not in seen:
                queue.append(flow.targetRef)
    for s in ir.steps:
        if s.id not in seen:
            ordered_steps.append(s)

    actors = [l for l in ir.lanes if l and l.lower() not in ("general process", "default lane", "unassigned")]
    for cp in ir.collapsed_pools:
        if cp.name not in actors:
            actors.append(f"{cp.name} (External)")
    systems = sorted({s.system_name for s in ir.steps if s.system_name})
    data_assets = [d.name for d in ir.data_objects]

    # Deterministic plain-English step walkthrough
    sentences: List[str] = [f"Process '{ir.process_title}' covers {len(ir.steps)} diagram elements."]
    for st in ordered_steps:
        if st.type == "startEvent":
            sentences.append(f"It starts when '{st.name}' occurs (lane: {st.lane}).")
        elif st.type == "endEvent":
            sentences.append(f"It finishes at '{st.name}' ({st.lane}).")
        elif st.type == "exclusiveGateway":
            branches = [f.name or step_map[f.targetRef].name for f in adj.get(st.id, []) if f.targetRef in step_map]
            sentences.append(f"At decision '{st.name}', the flow branches between: {', '.join(branches) or 'outgoing paths'}.")
        elif st.type == "parallelGateway":
            sentences.append(f"Parallel gateway '{st.name}' splits or synchronizes concurrent work.")
        else:
            sys_part = f" using {st.system_name}" if st.system_name else ""
            sentences.append(f"{st.lane} performs '{st.name}'{sys_part}.")

    plain_description = " ".join(sentences)

    # Detect missing FIM / BSI metadata so DMN agenda asks targeted questions
    missing_metadata: List[Dict[str, Any]] = []
    unassigned_steps = [
        s for s in ir.steps
        if s.type in ("userTask", "serviceTask", "callActivity")
        and (not s.lane or s.lane.lower() in ("general process", "default lane", "unassigned"))
    ]
    if unassigned_steps:
        missing_metadata.append({
            "kind": "MISSING_ACTORS",
            "step_ids": [s.id for s in unassigned_steps],
            "message": f"{len(unassigned_steps)} task(s) lack a specific department or role swimlane.",
        })

    if not ir.data_objects:
        missing_metadata.append({
            "kind": "MISSING_DATA_ASSETS",
            "step_ids": [s.id for s in ir.steps if s.type in ("userTask", "serviceTask")][:3],
            "message": "No input or output data objects / data stores are modeled on the diagram yet.",
        })

    # Check decision/verification steps lacking a fallback/exception gateway
    has_xor = any(s.type == "exclusiveGateway" for s in ir.steps)
    review_steps = [
        s for s in ir.steps
        if s.type in ("userTask", "serviceTask")
        and any(k in s.name.lower() for k in ("check", "verify", "review", "approv", "prüf", "genehmig", "freigeb"))
    ]
    if review_steps and not has_xor:
        missing_metadata.append({
            "kind": "MISSING_EXCEPTION_BRANCH",
            "step_ids": [s.id for s in review_steps],
            "message": "Review/approval task(s) lack an explicit exception or rejection branch.",
        })

    steps_without_sys = [
        s for s in ir.steps if s.type == "serviceTask" and not s.system_name
    ]
    if steps_without_sys:
        missing_metadata.append({
            "kind": "UNLINKED_SERVICE_TASK",
            "step_ids": [s.id for s in steps_without_sys],
            "message": f"{len(steps_without_sys)} automated/service task(s) have no named IT system linked.",
        })

    # Optional LLM narrative polish when a valid key is configured
    llm_used = False
    try:
        from app.services.llm_client import LLMClient, ProviderError
        from app.services.document_service import DocumentService

        LLMClient.resolve(provider, api_key, model_name, base_url)

        class _NarrativeOut(BaseModel):
            plain_english_summary: str

        safe_notes = DocumentService.scan_for_pii(plain_description)["redacted_text"]
        out = LLMClient.generate_structured(
            prompt=(
                f"Rewrite this structured BPMN process walkthrough into 3-4 warm, clear plain-English sentences "
                f"for a municipal coordinator. Keep all actor, system, and step names accurate:\n\n{safe_notes}"
            ),
            response_model=_NarrativeOut,
            system_instruction="You translate BPMN 2.0 diagrams into plain, supportive prose without inventing facts.",
            api_key=api_key,
            provider=provider,
            model_name=model_name,
            base_url=base_url,
        )
        if out.plain_english_summary.strip():
            plain_description = out.plain_english_summary.strip()
            llm_used = True
    except Exception as exc:
        logger.debug("Using deterministic BPMN narrative description (%s)", exc)

    return {
        "process_title": ir.process_title,
        "plain_english_description": plain_description,
        "actors": actors,
        "systems": systems,
        "data_assets": data_assets,
        "missing_metadata": missing_metadata,
        "llm_enhanced": llm_used,
        "step_count": len(ir.steps),
        "flow_count": len(ir.flows),
    }


def render_ir_to_svg(ir: BPMNProcessIR, gap_counts_by_step: Optional[Dict[str, Tuple[int, str]]] = None) -> str:
    """Render a clean, self-contained inline SVG diagram directly from BPMNProcessIR layout."""
    layout = _compute_layout(ir)
    active_lanes: List[str] = layout["active_lanes"]
    pool_top: int = layout["pool_top"]
    lane_heights: Dict[str, int] = layout["lane_heights"]
    lane_y: Dict[str, int] = layout["lane_y"]
    canvas_width: int = layout["canvas_width"]
    canvas_height: int = layout["canvas_height"]
    step_coords: Dict[str, Dict[str, int]] = layout["step_coords"]
    data_coords: Dict[str, Dict[str, int]] = layout["data_coords"]
    gap_map = gap_counts_by_step or {}

    vb_x = 150
    vb_w = canvas_width + 30
    vb_h = canvas_height + 20

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x} 0 {vb_w} {vb_h}" width="100%" style="max-height: 440px; font-family: Plus Jakarta Sans, Segoe UI, sans-serif;">',
        '  <defs><marker id="pmga-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#475569"/></marker></defs>',
    ]

    # Pool & Lanes
    parts.append(
        f'  <rect x="160" y="{pool_top}" width="{canvas_width}" height="{canvas_height - pool_top}" rx="6" fill="#FAF8F4" stroke="#CBD5E1" stroke-width="1.5"/>'
    )
    for l_name in active_lanes:
        ly = lane_y[l_name]
        lh = lane_heights[l_name]
        parts.append(f'  <rect x="190" y="{ly}" width="{canvas_width - 30}" height="{lh}" fill="#FFFFFF" stroke="#E2E8F0" stroke-width="1"/>')
        parts.append(f'  <text x="200" y="{ly + 20}" font-size="11" font-weight="700" fill="#334155">{_xml_esc(l_name[:28])}</text>')

    # Edges
    for f in ir.flows:
        sc = step_coords.get(f.sourceRef)
        tc = step_coords.get(f.targetRef)
        if not sc or not tc:
            continue
        if tc["x"] < sc["x"]:
            sx, sy = sc["x"] + sc["w"] // 2, sc["y"] + sc["h"]
            tx, ty = tc["x"] + tc["w"] // 2, tc["y"] + tc["h"]
            dy = max(sy, ty) + 26
            d_attr = f"M {sx} {sy} L {sx} {dy} L {tx} {dy} L {tx} {ty}"
        else:
            sx, sy = sc["x"] + sc["w"], sc["y"] + sc["h"] // 2
            tx, ty = tc["x"], tc["y"] + tc["h"] // 2
            if sy != ty:
                mx = (sx + tx) // 2
                d_attr = f"M {sx} {sy} L {mx} {sy} L {mx} {ty} L {tx} {ty}"
            else:
                d_attr = f"M {sx} {sy} L {tx} {ty}"
        parts.append(f'  <path d="{d_attr}" fill="none" stroke="#475569" stroke-width="1.5" marker-end="url(#pmga-arrow)"/>')
        if f.name:
            lx = (sc["x"] + sc["w"] + tc["x"]) // 2
            ly = min(sc["y"], tc["y"]) + 18
            parts.append(f'  <text x="{lx}" y="{ly}" font-size="10" text-anchor="middle" fill="#64748B">{_xml_esc(f.name[:24])}</text>')

    # Steps
    for s in ir.steps:
        c = step_coords[s.id]
        cx, cy, cw, ch = c["x"], c["y"], c["w"], c["h"]
        if s.type == "startEvent":
            parts.append(f'  <circle cx="{cx + 18}" cy="{cy + 18}" r="16" fill="#ECFDF5" stroke="#059669" stroke-width="2"/>')
            parts.append(f'  <text x="{cx + 18}" y="{cy + 48}" font-size="10" text-anchor="middle" fill="#334155">{_xml_esc(s.name[:24])}</text>')
        elif s.type == "endEvent":
            parts.append(f'  <circle cx="{cx + 18}" cy="{cy + 18}" r="16" fill="#FEF2F2" stroke="#DC2626" stroke-width="3"/>')
            parts.append(f'  <text x="{cx + 18}" y="{cy + 48}" font-size="10" text-anchor="middle" fill="#334155">{_xml_esc(s.name[:24])}</text>')
        elif "Gateway" in s.type:
            mx, my = cx + cw // 2, cy + ch // 2
            pts = f"{mx},{cy} {cx + cw},{my} {mx},{cy + ch} {cx},{my}"
            sym = "+" if s.type == "parallelGateway" else "×"
            parts.append(f'  <polygon points="{pts}" fill="#FFFBEB" stroke="#D97706" stroke-width="2"/>')
            parts.append(f'  <text x="{mx}" y="{my + 5}" font-size="16" font-weight="700" text-anchor="middle" fill="#92400E">{sym}</text>')
        else:
            stroke = "#0F766E" if s.type == "serviceTask" else "#1E4E68"
            fill = "#F0FDFA" if s.type == "serviceTask" else "#F8FAFC"
            parts.append(f'  <rect x="{cx}" y="{cy}" width="{cw}" height="{ch}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
            label = s.name[:28] + ("…" if len(s.name) > 28 else "")
            parts.append(f'  <text x="{cx + cw // 2}" y="{cy + ch // 2 + 4}" font-size="11" font-weight="600" text-anchor="middle" fill="#0F172A">{_xml_esc(label)}</text>')
            if s.system_name:
                parts.append(f'  <text x="{cx + cw // 2}" y="{cy + ch - 10}" font-size="9" text-anchor="middle" fill="#0F766E">[{_xml_esc(s.system_name[:22])}]</text>')

        # Gap badge overlay on SVG
        g_cnt, g_sev = gap_map.get(s.id, (len(s.gap_ids), "HIGH"))
        if g_cnt > 0:
            badge_fill = "#DC2626" if g_sev == "CRITICAL" else "#D97706"
            parts.append(f'  <circle cx="{cx + cw - 4}" cy="{cy + 4}" r="11" fill="{badge_fill}" stroke="#FFFFFF" stroke-width="1.5"/>')
            parts.append(f'  <text x="{cx + cw - 4}" y="{cy + 8}" font-size="10" font-weight="700" text-anchor="middle" fill="#FFFFFF">{g_cnt}</text>')

    # Data objects
    for d in ir.data_objects:
        dc_box = data_coords[d.id]
        dx, dy, dw, dh = dc_box["x"], dc_box["y"], dc_box["w"], dc_box["h"]
        parts.append(f'  <rect x="{dx}" y="{dy}" width="{dw}" height="{dh}" rx="3" fill="#FFF7ED" stroke="#C86D44" stroke-width="1.4"/>')
        parts.append(f'  <text x="{dx + dw // 2}" y="{dy + dh + 12}" font-size="9" text-anchor="middle" fill="#7C2D12">{_xml_esc(d.name[:20])}</text>')

    parts.append('</svg>')
    return "\n".join(parts)
