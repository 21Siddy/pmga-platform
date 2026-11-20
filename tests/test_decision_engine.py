"""Rigorous unit and adversarial tests for Milestone 1:
- Unified SQLite schema, FK enforcement, migration guards, and GDPR purge across v2 tables
- Pure-Python GoRules-compatible JDM/DMN DecisionEngine and all 4 JSON decision models
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

import pytest

from app.config import settings
from app.database import get_db, init_db, purge_project_data
from app.errors import DecisionModelError
from app.services.decision_engine import DecisionEngine, _eval_unary_cell


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    DecisionEngine.clear_cache()
    init_db()
    yield


# ---------------------------------------------------------------------------
# 1. DecisionEngine Core Semantics & Security/Adversarial Validation
# ---------------------------------------------------------------------------

def test_decision_engine_lists_all_four_models():
    models = DecisionEngine.list_models()
    assert models == [
        "01_entry_classifier",
        "02_question_agenda",
        "03_control_applicability",
        "04_gap_severity_and_signal",
    ]


def test_unary_evaluator_edges_and_python_bool_pitfall():
    ctx = {"mtpd_hours": 12, "rto_hours": 12}
    # Ensure 1 does NOT falsely match `true` and 0 does NOT falsely match `false`
    assert _eval_unary_cell("true", True, ctx) is True
    assert _eval_unary_cell("true", 1, ctx) is False
    assert _eval_unary_cell("false", False, ctx) is True
    assert _eval_unary_cell("false", 0, ctx) is False

    # Intervals
    assert _eval_unary_cell("[1..10]", 1, ctx) is True
    assert _eval_unary_cell("[1..10]", 10, ctx) is True
    assert _eval_unary_cell("(1..10]", 1, ctx) is False
    assert _eval_unary_cell("]1..10[", 10, ctx) is False
    assert _eval_unary_cell("[1..10]", "bad", ctx) is False

    # Cross-field safe AST expression
    assert _eval_unary_cell("expr: $ >= mtpd_hours", 12, ctx) is True
    assert _eval_unary_cell("expr: $ >= mtpd_hours", 4, ctx) is False


def test_decision_engine_rejects_path_traversal_and_missing_models():
    with pytest.raises(DecisionModelError):
        DecisionEngine.evaluate("../secrets", {})
    with pytest.raises(DecisionModelError):
        DecisionEngine.evaluate("99_nonexistent", {})


def test_decision_engine_rejects_corrupt_schema_and_unsafe_expressions(tmp_path, monkeypatch):
    bad_dir = tmp_path / "decision_models"
    bad_dir.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    # 1. Corrupt JSON
    (bad_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(DecisionModelError):
        DecisionEngine.evaluate("corrupt", {})

    # 2. Unsupported hitPolicy
    bad_hp = {
        "nodes": [
            {
                "id": "dt1",
                "type": "decisionTableNode",
                "content": {
                    "hitPolicy": "unique_invalid",
                    "inputs": [{"id": "i1", "field": "x"}],
                    "outputs": [{"id": "o1", "field": "y"}],
                    "rules": [],
                },
            }
        ]
    }
    (bad_dir / "bad_hp.json").write_text(json.dumps(bad_hp), encoding="utf-8")
    with pytest.raises(DecisionModelError, match="unsupported hitPolicy"):
        DecisionEngine.evaluate("bad_hp", {})

    # 3. Unknown column id referenced in rule
    bad_col = {
        "nodes": [
            {
                "id": "dt1",
                "type": "decisionTableNode",
                "content": {
                    "hitPolicy": "first",
                    "inputs": [{"id": "i1", "field": "x"}],
                    "outputs": [{"id": "o1", "field": "y"}],
                    "rules": [{"_id": "r1", "i_typo": "1", "o1": "2"}],
                },
            }
        ]
    }
    (bad_dir / "bad_col.json").write_text(json.dumps(bad_col), encoding="utf-8")
    with pytest.raises(DecisionModelError, match="unknown column id"):
        DecisionEngine.evaluate("bad_col", {})

    # 4. Disallowed code execution in expression
    bad_expr = {
        "nodes": [
            {
                "id": "dt1",
                "type": "decisionTableNode",
                "content": {
                    "hitPolicy": "first",
                    "inputs": [{"id": "i1", "field": "x"}],
                    "outputs": [{"id": "o1", "field": "y"}],
                    "rules": [{"_id": "r1", "i1": "expr: __import__('os').system('id')", "o1": "2"}],
                },
            }
        ]
    }
    (bad_dir / "bad_expr.json").write_text(json.dumps(bad_expr), encoding="utf-8")
    with pytest.raises(DecisionModelError):
        DecisionEngine.evaluate("bad_expr", {"x": 1})


def test_decision_engine_cache_invalidation_on_file_update(tmp_path, monkeypatch):
    d = tmp_path / "decision_models"
    d.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    model_path = d / "dynamic.json"

    def write_model(out_val: str):
        payload = {
            "nodes": [
                {
                    "id": "dt1",
                    "type": "decisionTableNode",
                    "content": {
                        "hitPolicy": "first",
                        "inputs": [{"id": "i1", "field": "x"}],
                        "outputs": [{"id": "o1", "field": "res"}],
                        "rules": [{"_id": "r1", "i1": "1", "o1": json.dumps(out_val)}],
                    },
                }
            ]
        }
        model_path.write_text(json.dumps(payload), encoding="utf-8")

    write_model("v1")
    assert DecisionEngine.evaluate("dynamic", {"x": 1}) == [{"res": "v1"}]
    time.sleep(0.02)
    write_model("v2")
    os.utime(model_path, (time.time() + 1, time.time() + 1))
    assert DecisionEngine.evaluate("dynamic", {"x": 1}) == [{"res": "v2"}]


# ---------------------------------------------------------------------------
# 2. All 4 JDM Decision Models (Domain & Boundary Accuracy)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ctx,expected_type,expected_disambig,expected_unpack",
    [
        (
            {"explicit_kind_col": "", "has_app_keyword": True, "has_action_verb": False, "word_count": 1, "raw_criticality": "kritisch"},
            "SYSTEM",
            True,
            True,
        ),
        (
            {"explicit_kind_col": "", "has_app_keyword": False, "has_action_verb": True, "word_count": 4, "raw_criticality": "normal"},
            "PROCESS",
            False,
            False,
        ),
        (
            {"explicit_kind_col": "PROCESS", "has_app_keyword": True, "has_action_verb": False, "word_count": 2, "raw_criticality": "sehr hoch"},
            "PROCESS",
            False,
            True,
        ),
        (
            {"explicit_kind_col": "ACTIVITY", "has_app_keyword": False, "has_action_verb": True, "word_count": 3, "raw_criticality": "hoch"},
            "ACTIVITY",
            False,
            True,
        ),
        (
            {"explicit_kind_col": "", "has_app_keyword": False, "has_action_verb": False, "word_count": 2, "raw_criticality": ""},
            "SYSTEM",
            True,
            False,
        ),
        (
            {"explicit_kind_col": "", "has_app_keyword": False, "has_action_verb": False, "word_count": 5, "raw_criticality": "critical"},
            "PROCESS",
            False,
            True,
        ),
    ],
)
def test_model_01_entry_classifier(ctx, expected_type, expected_disambig, expected_unpack):
    res = DecisionEngine.evaluate("01_entry_classifier", ctx)
    assert len(res) == 1
    assert res[0]["entity_type"] == expected_type
    assert res[0]["needs_disambiguation"] is expected_disambig
    assert res[0]["needs_cia_unpacking"] is expected_unpack
    assert res[0]["clarification_reason"]


def test_model_02_question_agenda_priority_ordering():
    # All gaps active -> must collect all 9 sorted strictly by ascending priority
    full_ctx = {
        "unlinked_systems_count": 3,
        "processes_without_steps": 2,
        "steps_without_actors": 4,
        "steps_without_data": 5,
        "missing_exception_branches": 2,
        "vague_criticality_count": 3,
        "missing_rto_mtpd_count": 1,
        "unconfirmed_doc_claims": 2,
        "open_high_risk_controls": 4,
    }
    res = DecisionEngine.evaluate("02_question_agenda", full_ctx)
    priorities = [r["priority"] for r in res]
    assert priorities == sorted(priorities)
    assert res[0]["agenda_key"] == "LINK_SYSTEM_TO_PROCESS"
    assert res[-1]["agenda_key"] == "REVIEW_REMEDIATION_STEPS"

    # When systems are linked & steps have actors, highest remaining item moves to top
    partial_ctx = {**full_ctx, "unlinked_systems_count": 0, "processes_without_steps": 0, "steps_without_actors": 0}
    res2 = DecisionEngine.evaluate("02_question_agenda", partial_ctx)
    assert res2[0]["agenda_key"] == "IDENTIFY_DATA_ASSETS"

    # Zero gaps -> empty agenda
    zero_ctx = {k: 0 for k in full_ctx}
    assert DecisionEngine.evaluate("02_question_agenda", zero_ctx) == []


def test_model_03_control_applicability_filtering_and_rto_boundary():
    # Minimal Normal process with no risk flags -> only ownership R003 applies
    minimal = {
        "entity_type": "PROCESS",
        "confidentiality": "Normal",
        "integrity": "Normal",
        "availability": "Normal",
        "has_personal_data": False,
        "uses_email_or_usb": False,
        "uses_shared_account": False,
        "has_external_vendor": False,
        "is_legacy_system": False,
        "has_fallback_path": True,
        "rto_hours": 4,
        "mtpd_hours": 12,
    }
    res_min = DecisionEngine.evaluate("03_control_applicability", minimal)
    assert {r["req_id"] for r in res_min} == {"R003"}

    # High-risk Bayreuth / Water Utility scenario with RTO == MTPD boundary violation
    risky = {
        "entity_type": "SYSTEM",
        "confidentiality": "Very High",
        "integrity": "High",
        "availability": "Very High",
        "has_personal_data": True,
        "uses_email_or_usb": True,
        "uses_shared_account": True,
        "has_external_vendor": True,
        "is_legacy_system": True,
        "has_fallback_path": False,
        "rto_hours": 12,
        "mtpd_hours": 12,
    }
    res_risky = DecisionEngine.evaluate("03_control_applicability", risky)
    req_ids = {r["req_id"] for r in res_risky}
    assert {"R002", "R003", "R054", "R101", "R104", "R059", "R074", "R107", "R021", "R018", "R123", "R114", "R112"} <= req_ids

    # None RTO/MTPD must NOT crash or trigger R018 falsely
    none_bia = {**minimal, "rto_hours": None, "mtpd_hours": None}
    assert "R018" not in {r["req_id"] for r in DecisionEngine.evaluate("03_control_applicability", none_bia)}


@pytest.mark.parametrize(
    "ctx,expected_sev,expected_bucket",
    [
        ({"rule_status": "COMPLIANT", "inherited_cia_max": "Normal", "rto_violation": True, "policy_contradicted": False, "evidence_status": "USER_CONFIRMED"}, "CRITICAL", "BEFORE_DIGITALIZATION"),
        ({"rule_status": "PARTIAL_GAP", "inherited_cia_max": "High", "rto_violation": False, "policy_contradicted": True, "evidence_status": "CONTRADICTED"}, "CRITICAL", "QUICK_WIN"),
        ({"rule_status": "PARTIAL_GAP", "inherited_cia_max": "Normal", "rto_violation": False, "policy_contradicted": True, "evidence_status": "CONTRADICTED"}, "HIGH", "QUICK_WIN"),
        ({"rule_status": "CRITICAL_GAP", "inherited_cia_max": "Very High", "rto_violation": False, "policy_contradicted": False, "evidence_status": ""}, "CRITICAL", "BEFORE_DIGITALIZATION"),
        ({"rule_status": "CRITICAL_GAP", "inherited_cia_max": "Normal", "rto_violation": False, "policy_contradicted": False, "evidence_status": ""}, "HIGH", "BEFORE_DIGITALIZATION"),
        ({"rule_status": "PARTIAL_GAP", "inherited_cia_max": "Normal", "rto_violation": False, "policy_contradicted": False, "evidence_status": "DOCUMENT_CLAIM"}, "MODERATE", "ONGOING"),
        ({"rule_status": "COMPLIANT", "inherited_cia_max": "High", "rto_violation": False, "policy_contradicted": False, "evidence_status": "DOCUMENT_CLAIM"}, "MODERATE", "ONGOING"),
        ({"rule_status": "COMPLIANT", "inherited_cia_max": "High", "rto_violation": False, "policy_contradicted": False, "evidence_status": "USER_CONFIRMED"}, "INFO", "ONGOING"),
    ],
)
def test_model_04_gap_severity_and_signal(ctx, expected_sev, expected_bucket):
    res = DecisionEngine.evaluate("04_gap_severity_and_signal", ctx)
    assert len(res) == 1
    assert res[0]["severity"] == expected_sev
    assert res[0]["priority_bucket"] == expected_bucket
    assert res[0]["warm_tone_framing"]


# ---------------------------------------------------------------------------
# 3. Unified SQLite v2 Tables, FK Enforcement & Full GDPR Purge
# ---------------------------------------------------------------------------

def test_v2_tables_fk_constraints_and_gdpr_purge():
    pid = str(uuid.uuid4())
    pm_id = str(uuid.uuid4())
    ent_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute("INSERT INTO projects (id, title) VALUES (?, ?)", (pid, "Unified Graph Test"))
        conn.execute(
            "INSERT INTO org_entities (id, project_id, entity_type, name) VALUES (?, ?, 'PROCESS', 'Voucher Issuing')",
            (ent_id, pid),
        )
        conn.execute(
            "INSERT INTO process_models (id, project_id, entity_id, title, is_primary) VALUES (?, ?, ?, 'Voucher Issuing', 1)",
            (pm_id, pid, ent_id),
        )
        conn.execute(
            "INSERT INTO session_turns (id, project_id, thread_id, role, content, input_kind) VALUES (?, ?, ?, 'user', 'Hello', 'chat')",
            (str(uuid.uuid4()), pid, pid),
        )
        conn.execute(
            "INSERT INTO evidence_items (id, project_id, entity_id, req_id, claim_text, source_quote, status) VALUES (?, ?, ?, 'R104', 'MFA claim', 'MFA is not enforced', 'CONTRADICTED')",
            (str(uuid.uuid4()), pid, ent_id),
        )
        conn.execute(
            "INSERT INTO remediation_gaps (id, project_id, process_model_id, entity_id, req_id, standard_clause, warm_summary) VALUES (?, ?, ?, ?, 'R104', 'ISO 27002:2022 A.8.5', 'Enable MFA')",
            (str(uuid.uuid4()), pid, pm_id, ent_id),
        )
        conn.execute(
            "INSERT INTO brand_profiles (project_id, org_name, primary_color) VALUES (?, 'Stadt Bayreuth', '#123456')",
            (pid,),
        )

        # FK violation check: inserting remediation_gap for nonexistent project must raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO remediation_gaps (id, project_id, req_id, standard_clause, warm_summary) VALUES (?, 'missing-proj', 'R001', 'BSI', 'x')",
                (str(uuid.uuid4()),),
            )

    # Create a fake checkpoint DB row to verify GDPR purge removes checkpoints too
    cp_conn = sqlite3.connect(settings.checkpoint_db_path)
    cp_conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT, checkpoint_id TEXT)")
    cp_conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id) VALUES (?, 'cp-1')", (pid,))
    cp_conn.commit()
    cp_conn.close()

    purge_res = purge_project_data(pid)
    assert purge_res["project_deleted"] is True
    assert purge_res["checkpoints_purged"] == 1

    with get_db() as conn:
        for tbl in ("projects", "process_models", "session_turns", "evidence_items", "remediation_gaps", "brand_profiles"):
            cnt = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
            assert cnt == 0, f"Table {tbl} still had rows after GDPR purge!"
