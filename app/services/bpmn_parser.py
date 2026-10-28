import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional, Set
from app.services.bpmn_engine import BPMNProcessGraph, BPMNNode, BPMNEdge

class BPMNParser:
    """
    Bidirectional BPMN 2.0 XML -> Canonical JSON Graph (BPMNProcessGraph) Parser.
    Grounded in BPMN Assistant (arXiv:2509.24592) and Matei et al. (arXiv:2604.12105).
    Extracts mathematical ground-truth graphs G* = (V*, E*, L*) from any standard .bpmn XML file.
    """

    SUPPORTED_TASK_TAGS = {
        "task": "userTask",
        "userTask": "userTask",
        "manualTask": "userTask",
        "sendTask": "userTask",
        "receiveTask": "userTask",
        "serviceTask": "serviceTask",
        "scriptTask": "serviceTask",
        "businessRuleTask": "serviceTask"
    }

    SUPPORTED_GATEWAY_TAGS = {
        "exclusiveGateway": "exclusiveGateway",
        "eventBasedGateway": "exclusiveGateway",
        "parallelGateway": "parallelGateway",
        "inclusiveGateway": "parallelGateway"
    }

    @staticmethod
    def _strip_ns(tag: str) -> str:
        """Removes XML namespace prefix (e.g. '{http://...}userTask' -> 'userTask')."""
        if "}" in tag:
            return tag.split("}", 1)[1]
        if ":" in tag:
            return tag.split(":", 1)[1]
        return tag

    @staticmethod
    def parse_xml_to_graph(xml_content: str, fallback_title: str = "Imported BPMN Process") -> BPMNProcessGraph:
        """
        Parses any standard BPMN 2.0 XML string into our canonical BPMNProcessGraph.
        Namespace-agnostic: works with OMG, Camunda, Signavio, or unprefixed BPMN files.
        """
        root = ET.fromstring(xml_content)

        # 1. Locate <process> and <collaboration>
        process_elem = None
        process_title = fallback_title
        lanes_map: Dict[str, str] = {} # node_id -> lane_name
        discovered_lanes: List[str] = []

        # Find collaboration / participant name if available
        for elem in root.iter():
            local_tag = BPMNParser._strip_ns(elem.tag)
            if local_tag == "participant" and elem.get("name"):
                process_title = elem.get("name")
            if local_tag == "process" and process_elem is None:
                process_elem = elem
                if elem.get("name"):
                    process_title = elem.get("name")

        if process_elem is None:
            raise ValueError("No <process> element found in the provided BPMN XML file.")

        # 2. Extract Lanes from <laneSet> -> <lane>
        for elem in process_elem.iter():
            local_tag = BPMNParser._strip_ns(elem.tag)
            if local_tag == "lane":
                lane_name = elem.get("name") or elem.get("id") or "Default Lane"
                if lane_name not in discovered_lanes:
                    discovered_lanes.append(lane_name)
                # Map flowNodeRef children
                for child in elem:
                    if BPMNParser._strip_ns(child.tag) == "flowNodeRef" and child.text:
                        lanes_map[child.text.strip()] = lane_name

        if not discovered_lanes:
            discovered_lanes = ["General Process"]

        default_lane = discovered_lanes[0]

        # 3. Extract Nodes (startEvent, endEvent, tasks, gateways)
        nodes: List[BPMNNode] = []
        edges: List[BPMNEdge] = []
        node_ids_seen: Set[str] = set()

        for child in process_elem:
            tag = BPMNParser._strip_ns(child.tag)
            nid = child.get("id")
            if not nid:
                continue

            name = child.get("name") or nid

            # Start Event
            if tag in ["startEvent"]:
                nodes.append(BPMNNode(
                    id=nid,
                    name=name,
                    type="startEvent",
                    lane=lanes_map.get(nid, default_lane),
                    source_type="FORMAL_POLICY"
                ))
                node_ids_seen.add(nid)

            # End Event
            elif tag in ["endEvent", "terminateEndEvent"]:
                nodes.append(BPMNNode(
                    id=nid,
                    name=name,
                    type="endEvent",
                    lane=lanes_map.get(nid, default_lane),
                    source_type="FORMAL_POLICY"
                ))
                node_ids_seen.add(nid)

            # Tasks
            elif tag in BPMNParser.SUPPORTED_TASK_TAGS:
                canonical_type = BPMNParser.SUPPORTED_TASK_TAGS[tag]
                nodes.append(BPMNNode(
                    id=nid,
                    name=name,
                    type=canonical_type,
                    lane=lanes_map.get(nid, default_lane),
                    source_type="ACTUAL_OPERATION"
                ))
                node_ids_seen.add(nid)

            # Gateways
            elif tag in BPMNParser.SUPPORTED_GATEWAY_TAGS:
                canonical_type = BPMNParser.SUPPORTED_GATEWAY_TAGS[tag]
                nodes.append(BPMNNode(
                    id=nid,
                    name=name,
                    type=canonical_type,
                    lane=lanes_map.get(nid, default_lane),
                    source_type="ACTUAL_OPERATION"
                ))
                node_ids_seen.add(nid)

            # Sequence Flows
            elif tag == "sequenceFlow":
                s_ref = child.get("sourceRef")
                t_ref = child.get("targetRef")
                flow_name = child.get("name") or None
                if not flow_name:
                    # Check for nested conditionExpression
                    for cond in child:
                        if BPMNParser._strip_ns(cond.tag) == "conditionExpression" and cond.text:
                            flow_name = cond.text.strip()
                if s_ref and t_ref:
                    edges.append(BPMNEdge(
                        id=nid,
                        sourceRef=s_ref,
                        targetRef=t_ref,
                        name=flow_name
                    ))

        # Filter edges to only those connecting valid discovered nodes
        valid_edges = [
            e for e in edges
            if e.sourceRef in node_ids_seen and e.targetRef in node_ids_seen
        ]

        return BPMNProcessGraph(
            process_title=process_title,
            lanes=discovered_lanes,
            nodes=nodes,
            edges=valid_edges
        )

    @staticmethod
    def parse_file(file_path: str) -> BPMNProcessGraph:
        """Parses a .bpmn file from a local filesystem path."""
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return BPMNParser.parse_xml_to_graph(content)
