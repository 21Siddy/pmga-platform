import os
import json
import re
from typing import Dict, List, Any, Optional
from app.services.bpmn_engine import BPMNProcessGraph

class PerturbationEngine:
    """
    Implements Matei et al. (arXiv:2604.12105) forward text generation and style analysis.
    Takes any canonical BPMNProcessGraph G* and generates 4 distinct linguistic styles:
    1. 'formal'           - Sequential, grammatical textbook English.
    2. 'shorthand'        - Fragmented, bullet/arrow telegraphic notes (realistic field logs).
    3. 'out_of_order'     - Non-monotonic chronology with explicit temporal/causal anchors.
    4. 'parallel_and_loop'- Concurrent branches ('at the same time') and rework cycles.
    Also provides zero-cost input style classification and BYOK Playbook lookup.
    """

    _cached_playbook: Optional[Dict[str, Any]] = None

    @classmethod
    def load_playbook(cls, playbook_path: str = "data/corpus/byok_playbook.json") -> Dict[str, Any]:
        """Loads and caches the BYOK Playbook from disk."""
        if cls._cached_playbook is not None:
            return cls._cached_playbook

        if os.path.exists(playbook_path):
            try:
                with open(playbook_path, "r", encoding="utf-8") as f:
                    cls._cached_playbook = json.load(f)
                    return cls._cached_playbook
            except Exception:
                pass

        # Robust built-in fallback if file is not on disk
        cls._cached_playbook = {
            "version": "1.0.0",
            "archetype_rules": {
                "out_of_order": {
                    "detection_keywords": ["before", "prior to", "only after", "precedes", "earlier", "previously"],
                    "rule": "Temporal Inversion Rule: Re-order steps by prerequisite. If 'Before task B, task A occurs', sequence flow must strictly be Task A -> Task B.",
                    "few_shot": None
                },
                "parallel_and_loops": {
                    "detection_keywords": ["simultaneously", "at the same time", "in parallel", "meanwhile", "both ... and", "concurrently", "repeat", "until approved", "resubmit", "send back"],
                    "rule": "Parallel Split/Join and Rework Rule: Concurrent activities must branch from a parallelGateway (split) and converge into a parallelGateway (join). Cyclic rework loops must route back to the source task.",
                    "few_shot": None
                },
                "shorthand": {
                    "detection_keywords": ["->", "=>", ";"],
                    "rule": "Shorthand Rule: Split steps across arrows and delimiters. Preserve lane prefixes ('clerk: verify').",
                    "few_shot": None
                },
                "exclusive_xor": {
                    "detection_keywords": ["if", "whether", "depending on", "in case of", "either", "or"],
                    "rule": "Exclusive Gateway (XOR) Rule: Conditional decision branches must use an exclusiveGateway with labeled outgoing flows.",
                    "few_shot": None
                }
            }
        }
        return cls._cached_playbook

    @classmethod
    def detect_input_style(cls, text: str) -> Dict[str, Any]:
        """
        Classifies input text into one of 4 archetypes:
        'out_of_order', 'parallel_and_loops', 'shorthand', or 'formal'.
        Zero API calls. Uses lexical indicators and structural delimiters.
        """
        playbook = cls.load_playbook()
        rules = playbook.get("archetype_rules", {})
        lowered = text.lower()

        scores: Dict[str, int] = {
            "out_of_order": 0,
            "parallel_and_loops": 0,
            "shorthand": 0,
            "exclusive_xor": 0
        }
        matched_cues: Dict[str, List[str]] = {k: [] for k in scores}

        # Check shorthand symbols
        if "->" in text or "=>" in text or "-->" in text:
            scores["shorthand"] += 5
            matched_cues["shorthand"].append("arrow_transition")
        if re.search(r'\b[a-zA-Z]{3,}:\s', text):
            scores["shorthand"] += 3
            matched_cues["shorthand"].append("role_prefix_colon")

        # Check keyword matches
        for arch_key, arch_data in rules.items():
            for kw in arch_data.get("detection_keywords", []):
                if kw in lowered:
                    scores[arch_key] += 2
                    matched_cues[arch_key].append(kw)

        # Determine dominant archetype
        best_arch = max(scores, key=scores.get)
        if scores[best_arch] >= 2:
            detected_style = best_arch
        else:
            detected_style = "formal"

        arch_entry = rules.get(detected_style, {})
        return {
            "detected_style": detected_style,
            "confidence_score": min(1.0, 0.4 + 0.15 * scores.get(best_arch, 0)),
            "matched_cues": matched_cues.get(detected_style, []),
            "playbook_rule": arch_entry.get("rule", ""),
            "playbook_few_shot": arch_entry.get("few_shot", None)
        }


    @staticmethod
    def generate_all_styles(graph: BPMNProcessGraph) -> Dict[str, str]:
        return {
            "formal": PerturbationEngine.generate_formal(graph),
            "shorthand": PerturbationEngine.generate_shorthand(graph),
            "out_of_order": PerturbationEngine.generate_out_of_order(graph),
            "parallel_and_loop": PerturbationEngine.generate_parallel_and_loop(graph)
        }

    @staticmethod
    def _get_topological_tasks(graph: BPMNProcessGraph) -> List[Dict[str, Any]]:
        """Extracts substantive tasks in approximate topological execution order."""
        task_nodes = [n for n in graph.nodes if n.type in ["userTask", "serviceTask"]]
        return task_nodes

    @staticmethod
    def generate_formal(graph: BPMNProcessGraph) -> str:
        """Style 1: Clean, grammatical sentences in standard chronological order."""
        tasks = PerturbationEngine._get_topological_tasks(graph)
        if not tasks:
            return f"The process starts with {graph.process_title} and concludes when completed."

        sentences = []
        for i, t in enumerate(tasks):
            actor = t.lane if t.lane != "Default Lane" else "the assigned operator"
            if i == 0:
                sentences.append(f"First, {actor} initiates the workflow by performing {t.name.lower()}.")
            elif i == len(tasks) - 1:
                sentences.append(f"Finally, {actor} completes {t.name.lower()}, bringing the case to a close.")
            else:
                sentences.append(f"Next, {actor} proceeds to {t.name.lower()}.")

        return " ".join(sentences)

    @staticmethod
    def generate_shorthand(graph: BPMNProcessGraph) -> str:
        """Style 2: Fragmented shorthand, arrows (->), missing articles, and shift notes."""
        tasks = PerturbationEngine._get_topological_tasks(graph)
        if not tasks:
            return "start -> initial review -> finish"

        fragments = []
        for t in tasks:
            clean_name = t.name.replace("Perform ", "").replace("Conduct ", "").replace("Execute ", "")
            fragments.append(f"{t.lane.lower()}: {clean_name.lower()}")

        return " -> ".join(fragments)

    @staticmethod
    def generate_out_of_order(graph: BPMNProcessGraph) -> str:
        """
        Style 3: Out-of-order presentation with explicit temporal anchors.
        Tests causal ordering resolution (A occurs before B, B depends on A).
        """
        tasks = PerturbationEngine._get_topological_tasks(graph)
        if len(tasks) < 3:
            return PerturbationEngine.generate_formal(graph)

        # Scramble presentation: talk about later steps first with explicit 'after'/'prior to'
        s1 = f"Prior to closing the process with {tasks[-1].name.lower()}, {tasks[-2].lane} must finish {tasks[-2].name.lower()}."
        s2 = f"This review occurs only after {tasks[0].lane} initially conducts {tasks[0].name.lower()}."
        middle = []
        for t in tasks[1:-2]:
            middle.append(f"In between these phases, {t.lane} completes {t.name.lower()}.")

        return f"{s1} {' '.join(middle)} {s2}"

    @staticmethod
    def generate_parallel_and_loop(graph: BPMNProcessGraph) -> str:
        """
        Style 4: Highlights concurrency ('meanwhile', 'simultaneously') and rework conditions.
        """
        gateways = [n for n in graph.nodes if "gateway" in n.type.lower()]
        has_parallel = any(n.type == "parallelGateway" for n in gateways)
        tasks = PerturbationEngine._get_topological_tasks(graph)

        if not tasks:
            return PerturbationEngine.generate_formal(graph)

        if has_parallel and len(tasks) >= 3:
            s_head = f"Upon receipt, {tasks[0].lane} executes {tasks[0].name.lower()}."
            s_par = f"Simultaneously and in parallel, {tasks[1].lane} handles {tasks[1].name.lower()} while {tasks[2].lane} carries out {tasks[2].name.lower()}."
            s_join = f"Once all parallel measures finish, the workflow proceeds to {tasks[-1].name.lower()}."
            return f"{s_head} {s_par} {s_join}"

        # Otherwise describe feedback / rework loop
        steps = [f"{t.lane} handles {t.name.lower()}" for t in tasks]
        return f"{' then '.join(steps)}. If defects are discovered during review, the work is sent back to be repeated until verified."
