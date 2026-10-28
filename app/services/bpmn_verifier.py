import re
from typing import List, Dict, Any, Set
from pydantic import BaseModel, Field

class VerificationTelemetry(BaseModel):
    is_valid: bool
    structural_soundness_score: float = Field(description="Score between 0.0 and 1.0 evaluating reachability and deadlock freedom")
    faithfulness_score: float = Field(description="Score between 0.0 and 1.0 evaluating action-object recall from source text")
    extracted_actions: List[str] = Field(default_factory=list, description="Atomic operational action clauses extracted from free text")
    structural_defects: List[str] = Field(default_factory=list, description="Any orphan nodes, missing end events, or broken flows")
    faithfulness_gaps: List[str] = Field(default_factory=list, description="Actions described in text that are missing in BPMN nodes")
    critique_prompt: str = Field(default="", description="Targeted critique instructions for recursive refinement")

class BPMNVerifier:
    """
    Scientific mathematical verifier for BPMN process graphs.
    Replaces 'vibes' with quantitative metrics:
    1. Structural Soundness (Graph Reachability & Deadlock Verification)
    2. Semantic Faithfulness (Action-Object Clause Recall from messy free-text)
    """

    @staticmethod
    def extract_atomic_actions(raw_text: str) -> List[str]:
        """
        Normalizes messy, ungrammatical staff field notes, shorthand, and fragments.
        Splits by arrows (->, =>), semicolons, newlines, and sentence breaks.
        """
        # Split on common free-text transition markers
        normalized = re.sub(r'[\r\n]+', '\n', raw_text)
        delimiters = r'->|=>|-->|;|(?:\d+\. )|\n'
        chunks = re.split(delimiters, normalized)

        actions = []
        for chunk in chunks:
            cleaned = chunk.strip(" -*•\t")
            # Filter noise and keep substantive phrases (3+ words or 15+ characters)
            if len(cleaned) >= 12 and not cleaned.lower().startswith(("http", "#", "directive", "applies to")):
                actions.append(cleaned[:120])
        return actions[:12] # Work with top atomic actions

    @staticmethod
    def verify_graph(
        graph_nodes: List[Dict[str, Any]],
        graph_edges: List[Dict[str, Any]],
        source_text: str
    ) -> VerificationTelemetry:
        """
        Performs mathematical graph traversal & semantic recall evaluation.
        """
        structural_defects = []
        faithfulness_gaps = []

        # 1. Structural Verification
        node_ids: Set[str] = {n.get("id") for n in graph_nodes if n.get("id")}
        start_nodes = [n for n in graph_nodes if n.get("type") == "startEvent"]
        end_nodes = [n for n in graph_nodes if n.get("type") == "endEvent"]

        if not start_nodes:
            structural_defects.append("Missing required startEvent node.")
        if not end_nodes:
            structural_defects.append("Missing required endEvent node.")

        # Build forward adjacency list
        adj_forward: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        adj_backward: Dict[str, List[str]] = {nid: [] for nid in node_ids}

        for edge in graph_edges:
            s = edge.get("sourceRef")
            t = edge.get("targetRef")
            if s in adj_forward and t in node_ids:
                adj_forward[s].append(t)
            else:
                structural_defects.append(f"Dangling edge {edge.get('id')} references non-existent node: {s} -> {t}")
            if t in adj_backward and s in node_ids:
                adj_backward[t].append(s)

        # Reachability from startEvent
        reachable_from_start: Set[str] = set()
        queue = [n["id"] for n in start_nodes if n.get("id")]
        while queue:
            curr = queue.pop(0)
            if curr not in reachable_from_start:
                reachable_from_start.add(curr)
                queue.extend(adj_forward.get(curr, []))

        unreachable = node_ids - reachable_from_start
        if unreachable:
            structural_defects.append(f"Unreachable orphan nodes detected: {', '.join(unreachable)}")

        # Can reach endEvent
        can_reach_end: Set[str] = set()
        back_queue = [n["id"] for n in end_nodes if n.get("id")]
        while back_queue:
            curr = back_queue.pop(0)
            if curr not in can_reach_end:
                can_reach_end.add(curr)
                back_queue.extend(adj_backward.get(curr, []))

        deadlocks = node_ids - can_reach_end
        if deadlocks:
            structural_defects.append(f"Deadlock traps detected (cannot reach endEvent): {', '.join(deadlocks)}")

        # Calculate structural soundness score
        total_structural_checks = max(len(node_ids), 1) + 2
        structural_errors = len(structural_defects)
        soundness_score = max(0.0, min(1.0, 1.0 - (structural_errors / total_structural_checks)))

        # 2. Semantic Faithfulness Evaluation
        extracted_actions = BPMNVerifier.extract_atomic_actions(source_text)
        node_names = " ".join([n.get("name", "").lower() for n in graph_nodes])

        matched_count = 0
        for action in extracted_actions:
            # Token match heuristic
            action_tokens = [w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', action)]
            matches = [t for t in action_tokens if t in node_names]
            if len(matches) >= max(1, len(action_tokens) // 2):
                matched_count += 1
            else:
                faithfulness_gaps.append(f"Action '{action[:60]}...' has no corresponding BPMN task.")

        faithfulness_score = (matched_count / len(extracted_actions)) if extracted_actions else 1.0

        is_valid = soundness_score >= 0.85 and faithfulness_score >= 0.70

        # Build critique prompt if refinement is needed
        critique_parts = []
        if structural_defects:
            critique_parts.append("Structural defects to fix: " + "; ".join(structural_defects))
        if faithfulness_gaps:
            critique_parts.append("Missing operational steps to incorporate: " + "; ".join(faithfulness_gaps[:3]))
        critique_prompt = " | ".join(critique_parts)

        return VerificationTelemetry(
            is_valid=is_valid,
            structural_soundness_score=round(soundness_score, 2),
            faithfulness_score=round(faithfulness_score, 2),
            extracted_actions=extracted_actions,
            structural_defects=structural_defects,
            faithfulness_gaps=faithfulness_gaps,
            critique_prompt=critique_prompt
        )
