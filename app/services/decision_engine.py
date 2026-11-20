"""Pure-Python GoRules-compatible JSON Decision Model (JDM/DMN) evaluator.

Evaluates inspectable JSON decision tables stored in ``data/decision_models/*.json``
without requiring native Rust/PyO3 extensions. Supports:
- GoRules JDM structure (`contentType: "application/vnd.gorules.decision"`,
  `nodes` with `decisionTableNode`, `hitPolicy: "first" | "collect"`,
  typed `inputs`, `outputs`, `rules`, optional `sortBy`).
- Unary tests: wildcards, booleans, null checks, numeric comparisons (`>`, `>=`,
  `<`, `<=`, `==`, `!=`), intervals (`[a..b]`, `(a..b]`, `]a..b[`), lists
  (`"A", "B"`, `in [...]`, `not in [...]`), string predicates (`contains`,
  `startsWith`, `endsWith`, `matches`), and AST-safe cross-field expressions
  (`$ >= mtpd_hours`, `availability in ["High", "Very High"]`).
- Schema validation and mtime-aware caching.
- Full evaluation tracing & structured logging.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.errors import DecisionModelError

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(
    r"^([\[\]\(\)])\s*(-?\d+(?:\.\d+)?)\s*\.\.\s*(-?\d+(?:\.\d+)?)\s*([\[\]\(\)])$"
)
_CMP_RE = re.compile(r"^(<=|>=|!=|==|<|>)\s*(.+)$")
_STRING_PRED_RE = re.compile(r"^(contains|startsWith|endsWith|matches)\s+(.+)$", re.IGNORECASE)
_IN_LIST_RE = re.compile(r"^(not\s+in|in)\s*(\[.*\])$", re.IGNORECASE)
_LIST_ITEM_SPLIT_RE = re.compile(r"""\s*,\s*(?=(?:[^"']*["'][^"']*["'])*[^"']*$)""")

_MODEL_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


def _parse_literal(raw: str) -> Any:
    s = raw.strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        if s.startswith("'"):
            return s[1:-1]
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s[1:-1]
    if s.startswith("[") or s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_float(value: Any) -> Optional[float]:
    if _is_numeric(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _equal_values(actual: Any, expected: Any) -> bool:
    # Avoid Python's 1 == True / 0 == False pitfall
    if isinstance(expected, bool):
        if isinstance(actual, bool):
            return actual is expected
        if isinstance(actual, str):
            low = actual.strip().lower()
            if low in ("true", "false"):
                return (low == "true") is expected
        return False
    if expected is None:
        return actual is None
    if actual is None:
        return False
    if _is_numeric(expected) and _is_numeric(actual):
        return float(actual) == float(expected)
    if isinstance(expected, str):
        return str(actual).strip().lower() == expected.strip().lower() if isinstance(actual, str) else str(actual) == expected
    return actual == expected


class _SafeExprEvaluator(ast.NodeVisitor):
    """AST-whitelisted evaluator for cross-field rules without eval()."""

    def __init__(self, env: Dict[str, Any]) -> None:
        self.env = env

    def eval(self, expr: str) -> Any:
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise DecisionModelError(f"Invalid decision expression '{expr}': {exc}") from exc
        return self.visit(tree.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id in ("null", "None"):
            return None
        if node.id in self.env:
            return self.env[node.id]
        raise DecisionModelError(f"Unknown field '{node.id}' in decision expression.")

    def visit_List(self, node: ast.List) -> List[Any]:
        return [self.visit(el) for el in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> Tuple[Any, ...]:
        return tuple(self.visit(el) for el in node.elts)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        val = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return not bool(val)
        if isinstance(node.op, ast.USub):
            num = _to_float(val)
            if num is None:
                return False
            return -num
        raise DecisionModelError(f"Unsupported unary operator: {type(node.op).__name__}")

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:
        if isinstance(node.op, ast.And):
            return all(bool(self.visit(v)) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(bool(self.visit(v)) for v in node.values)
        raise DecisionModelError(f"Unsupported bool operator: {type(node.op).__name__}")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left = _to_float(self.visit(node.left))
        right = _to_float(self.visit(node.right))
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if right != 0 else None
        raise DecisionModelError(f"Unsupported binary operator: {type(node.op).__name__}")

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for op, comp_node in zip(node.ops, node.comparators):
            right = self.visit(comp_node)
            ok = self._compare_pair(op, left, right)
            if not ok:
                return False
            left = right
        return True

    @staticmethod
    def _compare_pair(op: ast.cmpop, left: Any, right: Any) -> bool:
        if isinstance(op, ast.Is):
            return left is right
        if isinstance(op, ast.IsNot):
            return left is not right
        if isinstance(op, ast.Eq):
            return _equal_values(left, right)
        if isinstance(op, ast.NotEq):
            return not _equal_values(left, right)
        if isinstance(op, ast.In):
            if right is None:
                return False
            if isinstance(right, (list, tuple, set)):
                return any(_equal_values(left, item) for item in right)
            return str(left).lower() in str(right).lower()
        if isinstance(op, ast.NotIn):
            return not _SafeExprEvaluator._compare_pair(ast.In(), left, right)

        lnum = _to_float(left)
        rnum = _to_float(right)
        if lnum is None or rnum is None:
            return False
        if isinstance(op, ast.Lt):
            return lnum < rnum
        if isinstance(op, ast.LtE):
            return lnum <= rnum
        if isinstance(op, ast.Gt):
            return lnum > rnum
        if isinstance(op, ast.GtE):
            return lnum >= rnum
        raise DecisionModelError(f"Unsupported comparison operator: {type(op).__name__}")

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise DecisionModelError("Only direct helper calls are allowed in decision expressions.")
        fn_name = node.func.id
        args = [self.visit(a) for a in node.args]
        if fn_name == "len":
            if len(args) != 1:
                raise DecisionModelError("len() expects 1 argument.")
            return len(args[0]) if args[0] is not None else 0
        if fn_name == "lower":
            return str(args[0] or "").lower()
        if fn_name == "contains":
            if len(args) != 2 or args[0] is None:
                return False
            return str(args[1]).lower() in str(args[0]).lower()
        raise DecisionModelError(f"Unsupported function '{fn_name}' in decision expression.")

    def generic_visit(self, node: ast.AST) -> Any:
        raise DecisionModelError(f"Disallowed AST node in decision expression: {type(node).__name__}")


def _eval_unary_cell(cell_expr: Any, actual: Any, context: Dict[str, Any]) -> bool:
    """Evaluate a single GoRules JDM input condition cell against actual value and full context."""
    if cell_expr is None:
        return True
    expr = str(cell_expr).strip()
    if expr == "" or expr == "*":
        return True

    # 1. Cross-field expression prefix: "expr: ..." or "$" variable usage
    if expr.startswith("expr:") or "$" in expr:
        raw_expr = expr[5:].strip() if expr.startswith("expr:") else expr
        env = dict(context)
        env["__input__"] = actual
        normalized_expr = raw_expr.replace("$", "__input__")
        # Normalize JS-style boolean/null literals gently
        normalized_expr = re.sub(r"\bnull\b", "None", normalized_expr)
        return bool(_SafeExprEvaluator(env).eval(normalized_expr))

    # 2. Interval check: [1..10], ]1..10[, (1..10]
    iv_m = _INTERVAL_RE.match(expr)
    if iv_m:
        num = _to_float(actual)
        if num is None:
            return False
        left_br, lo_s, hi_s, right_br = iv_m.groups()
        lo, hi = float(lo_s), float(hi_s)
        lo_ok = (num >= lo) if left_br == "[" else (num > lo)
        hi_ok = (num <= hi) if right_br == "]" else (num < hi)
        return lo_ok and hi_ok

    # 3. `in [...]` / `not in [...]`
    in_m = _IN_LIST_RE.match(expr)
    if in_m:
        op_kw, list_raw = in_m.groups()
        try:
            items = json.loads(list_raw)
        except json.JSONDecodeError as exc:
            raise DecisionModelError(f"Invalid list literal in JDM cell '{expr}': {exc}") from exc
        matched = any(_equal_values(actual, item) for item in items)
        return (not matched) if op_kw.lower().startswith("not") else matched

    # 4. String / regex predicates: contains "x", matches "regex", startsWith "x", endsWith "x"
    str_m = _STRING_PRED_RE.match(expr)
    if str_m:
        pred, arg_raw = str_m.groups()
        pred_low = pred.lower()
        target_val = _parse_literal(arg_raw)
        target = str(target_val if target_val is not None else _strip_quotes(arg_raw))
        actual_str = "" if actual is None else str(actual)
        if pred_low == "contains":
            return target.lower() in actual_str.lower()
        if pred_low == "startswith":
            return actual_str.lower().startswith(target.lower())
        if pred_low == "endswith":
            return actual_str.lower().endswith(target.lower())
        if pred_low == "matches":
            try:
                return re.search(target, actual_str) is not None
            except re.error as exc:
                raise DecisionModelError(f"Invalid regex '{target}' in JDM rule: {exc}") from exc

    # 5. Comparisons: >, >=, <, <=, ==, !=
    cmp_m = _CMP_RE.match(expr)
    if cmp_m:
        op_str, rhs_raw = cmp_m.groups()
        rhs = _parse_literal(rhs_raw)
        if op_str == "==":
            return _equal_values(actual, rhs)
        if op_str == "!=":
            return not _equal_values(actual, rhs)
        lnum = _to_float(actual)
        rnum = _to_float(rhs)
        if lnum is None or rnum is None:
            return False
        if op_str == ">":
            return lnum > rnum
        if op_str == ">=":
            return lnum >= rnum
        if op_str == "<":
            return lnum < rnum
        if op_str == "<=":
            return lnum <= rnum

    # 6. Negated literal: ! "foo"
    if expr.startswith("!") and not expr.startswith("!="):
        rhs = _parse_literal(expr[1:].strip())
        return not _equal_values(actual, rhs)

    # 7. Comma-separated OR list: "SYSTEM", "PROCESS"
    parts = [p.strip() for p in _LIST_ITEM_SPLIT_RE.split(expr) if p.strip()]
    if len(parts) > 1:
        return any(_eval_unary_cell(part, actual, context) for part in parts)

    # 8. Single literal match
    expected = _parse_literal(expr)
    return _equal_values(actual, expected)


def _resolve_output_value(raw_val: Any) -> Any:
    if raw_val is None:
        return None
    if isinstance(raw_val, (bool, int, float, list, dict)):
        return raw_val
    if isinstance(raw_val, str):
        s = raw_val.strip()
        if s == "":
            return ""
        return _parse_literal(s)
    return raw_val


class DecisionEngine:
    """Deterministic GoRules JDM JSON decision table loader and evaluator."""

    @staticmethod
    def clear_cache() -> None:
        _MODEL_CACHE.clear()

    @staticmethod
    def _resolve_model_path(model_name: str) -> str:
        clean = (model_name or "").strip()
        if not clean or "/" in clean or "\\" in clean or ".." in clean:
            raise DecisionModelError(f"Invalid decision model name: {model_name!r}")
        filename = clean if clean.endswith(".json") else f"{clean}.json"
        path = os.path.join(settings.decision_models_path, filename)
        if not os.path.exists(path):
            raise DecisionModelError(f"Decision model '{model_name}' not found at {path}")
        return path

    @staticmethod
    def _load_and_validate(model_name: str) -> Dict[str, Any]:
        path = DecisionEngine._resolve_model_path(model_name)
        mtime = os.path.getmtime(path)
        cached = _MODEL_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]

        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise DecisionModelError(f"Failed to read decision model '{model_name}': {exc}") from exc

        if not isinstance(doc, dict):
            raise DecisionModelError(f"Decision model '{model_name}' root must be a JSON object.")

        nodes = doc.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise DecisionModelError(f"Decision model '{model_name}' must contain a non-empty 'nodes' list.")

        dt_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") == "decisionTableNode"]
        if not dt_nodes:
            raise DecisionModelError(f"Decision model '{model_name}' has no 'decisionTableNode'.")

        content = dt_nodes[0].get("content")
        if not isinstance(content, dict):
            raise DecisionModelError(f"Decision model '{model_name}' decisionTableNode is missing 'content'.")

        hit_policy = content.get("hitPolicy")
        if hit_policy not in ("first", "collect"):
            raise DecisionModelError(
                f"Decision model '{model_name}' has unsupported hitPolicy {hit_policy!r}; expected 'first' or 'collect'."
            )

        inputs = content.get("inputs")
        outputs = content.get("outputs")
        rules = content.get("rules")
        if not isinstance(inputs, list) or not isinstance(outputs, list) or not isinstance(rules, list):
            raise DecisionModelError(
                f"Decision model '{model_name}' must define 'inputs', 'outputs', and 'rules' arrays."
            )

        for col in inputs + outputs:
            if not isinstance(col, dict) or not col.get("id") or not col.get("field"):
                raise DecisionModelError(
                    f"Decision model '{model_name}' has malformed input/output column definition: {col!r}"
                )

        valid_col_ids = {col["id"] for col in inputs + outputs}
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise DecisionModelError(f"Decision model '{model_name}' rule #{idx} must be an object.")
            extra = set(rule.keys()) - valid_col_ids - {"_id", "_description"}
            if extra:
                raise DecisionModelError(
                    f"Decision model '{model_name}' rule #{idx} references unknown column id(s): {sorted(extra)}"
                )

        parsed = {
            "name": model_name,
            "path": path,
            "hitPolicy": hit_policy,
            "sortBy": content.get("sortBy"),
            "sortOrder": (content.get("sortOrder") or "asc").lower(),
            "inputs": inputs,
            "outputs": outputs,
            "rules": rules,
        }
        _MODEL_CACHE[path] = (mtime, parsed)
        return parsed

    @staticmethod
    def list_models() -> List[str]:
        base = settings.decision_models_path
        if not os.path.isdir(base):
            return []
        return sorted(f[:-5] for f in os.listdir(base) if f.endswith(".json"))

    @staticmethod
    def evaluate_with_trace(model_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a decision table and return full trace metadata alongside matched outputs."""
        if not isinstance(context, dict):
            raise DecisionModelError("DecisionEngine context must be a dictionary.")

        started = time.perf_counter()
        table = DecisionEngine._load_and_validate(model_name)
        hit_policy = table["hitPolicy"]
        inputs = table["inputs"]
        outputs = table["outputs"]
        rules = table["rules"]

        matched_results: List[Dict[str, Any]] = []
        matched_rule_ids: List[str] = []

        for idx, rule in enumerate(rules):
            rule_id = str(rule.get("_id") or f"rule_{idx + 1}")
            all_match = True
            for inp in inputs:
                col_id = inp["id"]
                field_name = inp["field"]
                cell_expr = rule.get(col_id, "")
                actual_val = context.get(field_name)
                if not _eval_unary_cell(cell_expr, actual_val, context):
                    all_match = False
                    break

            if all_match:
                out_row: Dict[str, Any] = {}
                for out_col in outputs:
                    col_id = out_col["id"]
                    field_name = out_col["field"]
                    if col_id in rule:
                        out_row[field_name] = _resolve_output_value(rule[col_id])
                    elif "defaultValue" in out_col:
                        out_row[field_name] = out_col["defaultValue"]
                    else:
                        out_row[field_name] = None
                out_row["_rule_id"] = rule_id
                matched_results.append(out_row)
                matched_rule_ids.append(rule_id)
                if hit_policy == "first":
                    break

        sort_by = table.get("sortBy")
        if hit_policy == "collect" and matched_results:
            if not sort_by and any("priority" in row and _is_numeric(row["priority"]) for row in matched_results):
                sort_by = "priority"
            if sort_by and any(sort_by in row for row in matched_results):
                reverse = table.get("sortOrder") == "desc"
                matched_results.sort(
                    key=lambda r: (r.get(sort_by) is None, r.get(sort_by) if r.get(sort_by) is not None else 0),
                    reverse=reverse,
                )

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        logger.debug(
            "DMN evaluated model=%s hitPolicy=%s matched=%d rules=%s duration_ms=%.3f",
            model_name,
            hit_policy,
            len(matched_results),
            matched_rule_ids,
            duration_ms,
        )
        return {
            "model_name": model_name,
            "hit_policy": hit_policy,
            "matched_rule_ids": matched_rule_ids,
            "results": matched_results,
            "duration_ms": duration_ms,
        }

    @staticmethod
    def evaluate(model_name: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Deterministic evaluation of a JSON Decision Table (FIRST or COLLECT hit policy)."""
        trace = DecisionEngine.evaluate_with_trace(model_name, context)
        # Strip internal _rule_id key? Keep it harmless or provide clean dicts?
        # Keep _rule_id out unless needed or expose clean outputs; wait - callers only read declared output fields.
        clean: List[Dict[str, Any]] = []
        for row in trace["results"]:
            clean_row = {k: v for k, v in row.items() if k != "_rule_id"}
            clean.append(clean_row)
        return clean
