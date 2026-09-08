from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pkm_workflow.ai_daily_luna import MODEL, run_luna_stage
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel

DAY = date(2026, 9, 8)
GENERATOR_ID = "11111111-1111-4111-8111-111111111111"
REVIEWER_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def workflow(tmp_path):
    vault = tmp_path / "vault" / "30-Daily"
    vault.mkdir(parents=True)
    candidate = Candidate(
        evidence_id="evidence-1", source="Research Lab", title="Agent tool reuse",
        link="https://example.com/research", published=DAY.isoformat(),
        summary="The agent reuses browser tool calls and reports failure states.",
        content_type="research", fulltext_enriched=True, source_complete=True,
    )
    context = {"fields": {"schema_version": "7.5"}, "user_context_hash": "sha256:" + "a" * 64}
    def call(stage, **kwargs):
        return run_luna_stage(
            stage, runtime_root=tmp_path, vault_daily_dir=vault, today=DAY,
            collect=lambda _day: CollectionResult(CoverageLevel.A, 14, 14, (candidate,)),
            context_loader=lambda: context, **kwargs,
        )
    return call, vault


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def draft():
    claims = [
        {"claim_id": key, "statement": text, "evidence_ids": ["evidence-1"],
         "claimed_risk": "high" if key == "event" else "medium",
         "claim_kind": "editorial_inference" if key == "importance" else "fact",
         "context_refs": [], "action_type": "NONE", "action_object": "", "success_criterion": ""}
        for key, text in [
            ("title", "浏览器工具开始复用"),
            ("event", "Agent 将重复的浏览器操作整理为工具。"),
            ("mechanism1", "同类操作共享接口。"),
            ("mechanism2", "失败状态被保留，调用者可以判断是否重试。"),
            ("importance", "复用边界同时包含成功结果和失败状态，才能减少调用者重复处理。"),
        ]
    ]
    return {"model": MODEL, "session_id": GENERATOR_ID, "draft": {"stories": [{
        "story_id": "story-1", "rank": 1, "title_claim_id": "title",
        "event_claim_ids": ["event"], "mechanism_claim_ids": ["mechanism1", "mechanism2"],
        "importance_claim_ids": ["importance"], "impact_claim_ids": [],
        "uncertainty_claim_ids": [], "watch_claim_ids": [], "action_claim_ids": [],
        "claims": claims,
    }]}}


def ready(workflow):
    call, _ = workflow
    prepared = call("prepare")
    assert prepared["status"] == "GENERATOR_READY"
    save(prepared["draft_path"], draft())
    reviewed = call("review", run_id=prepared["run_id"])
    assert reviewed["status"] == "REVIEWER_READY"
    packet = json.loads(Path(reviewed["review_input_path"]).read_text(encoding="utf-8"))
    response = {
        "model": MODEL, "session_id": REVIEWER_ID,
        "review_request_hash": reviewed["review_request_hash"],
        "decisions": [
            {"claim_id": row["claim_id"], "evidence_ids": row["evidence_ids"],
             "review_requirement_hash": row["review_requirement_hash"],
             "decision": "ACCEPT", "reason_code": "SUPPORTED_BY_SEALED_EVIDENCE"}
            for row in packet["rubric"]["requirements"]
        ],
    }
    save(reviewed["review_path"], response)
    return prepared, reviewed, response


def test_luna_stages_render_and_publish_without_provider(workflow, monkeypatch):
    from pkm_workflow.deepseek_provider import DeepSeekShadowProvider
    def forbidden(*args, **kwargs):
        raise AssertionError("DeepSeek must not be constructed")
    monkeypatch.setattr(DeepSeekShadowProvider, "__init__", forbidden)
    call, vault = workflow
    prepared, _, _ = ready(workflow)
    shadow = call("finalize", run_id=prepared["run_id"])
    assert shadow["status"] == "PUBLISHED"
    assert shadow["vault_write"] is False
    assert "浏览器工具开始复用" in Path(shadow["markdown_path"]).read_text(encoding="utf-8")
    final = call("finalize", mode="production", run_id=prepared["run_id"], confirm_vault_write=True)
    assert final["vault_write"] is True
    assert (vault / f"AI-Daily-{DAY}.md").exists()
    assert call("prepare", mode="production")["status"] == "ALREADY_EXISTS"


def test_duplicate_prepare_is_busy(workflow):
    call, _ = workflow
    first = call("prepare")
    assert call("prepare")["status"] == "PRODUCTION_BUSY"
    assert call("prepare", run_id=first["run_id"])["run_id"] == first["run_id"]


@pytest.mark.parametrize("problem", ["missing", "duplicate", "hash", "self_review", "model", "changed_draft"])
def test_invalid_review_cannot_create_markdown(workflow, problem):
    call, vault = workflow
    prepared, reviewed, response = ready(workflow)
    if problem == "missing":
        response["decisions"].pop()
    elif problem == "duplicate":
        response["decisions"].append(response["decisions"][0])
    elif problem == "hash":
        response["review_request_hash"] = "sha256:" + "0" * 64
    elif problem == "self_review":
        response["session_id"] = GENERATOR_ID
    elif problem == "model":
        response["model"] = "deepseek"
    else:
        updated = draft()
        updated["draft"]["stories"][0]["claims"][0]["statement"] = "修改后的未审核标题"
        save(prepared["draft_path"], updated)
    save(reviewed["review_path"], response)
    result = call("finalize", run_id=prepared["run_id"])
    assert result["exit_code"] != 0
    assert not list(Path(prepared["draft_path"]).parent.glob("AI-Daily-*.md"))
    assert not list(vault.glob("*.md"))


def test_rejected_material_claim_drops_story(workflow):
    call, _ = workflow
    prepared, reviewed, response = ready(workflow)
    response["decisions"][1].update(decision="REJECT", reason_code="CONTRADICTED_BY_SEALED_EVIDENCE")
    save(reviewed["review_path"], response)
    result = call("finalize", run_id=prepared["run_id"])
    assert result["status"] == "NO_SIGNIFICANT_NEWS"
    assert result["markdown_path"] is None


@pytest.mark.parametrize("coverage,evidence,expected", [
    (CoverageLevel.INSUFFICIENT, CoverageLevel.A, "COVERAGE_INSUFFICIENT"),
    (CoverageLevel.A, CoverageLevel.INSUFFICIENT, "EVIDENCE_INSUFFICIENT"),
    (CoverageLevel.A, CoverageLevel.A, "NO_SIGNIFICANT_NEWS"),
    (CoverageLevel.B, CoverageLevel.A, "COVERAGE_LIMITED"),
])
def test_coverage_and_empty_feed_skip_models(tmp_path, coverage, evidence, expected):
    result = run_luna_stage(
        "prepare", runtime_root=tmp_path, vault_daily_dir=tmp_path, today=DAY,
        collect=lambda _day: CollectionResult(coverage, 14, 8, (), evidence_level=evidence),
        context_loader=lambda: {"fields": {}, "user_context_hash": "sha256:" + "a" * 64},
    )
    assert result["status"] == expected
    assert result["role_execution_count"] == 0
    assert result["markdown_path"] is None


def test_single_structure_repair_budget(workflow):
    call, _ = workflow
    prepared = call("prepare")
    invalid = draft()
    invalid["draft"]["stories"][0]["rank"] = "first"
    save(prepared["draft_path"], invalid)
    repair = call("review", run_id=prepared["run_id"])
    assert repair["status"] == "SCHEMA_REPAIR_REQUIRED"
    save(repair["output_path"], invalid)
    exhausted = call("review", run_id=prepared["run_id"])
    assert exhausted["status"] == "SCHEMA_REPAIR_EXHAUSTED"


def test_bad_evidence_is_not_repaired_or_published(workflow):
    call, vault = workflow
    prepared = call("prepare")
    invalid = draft()
    invalid["draft"]["stories"][0]["claims"][0]["evidence_ids"] = ["unbound"]
    save(prepared["draft_path"], invalid)
    result = call("review", run_id=prepared["run_id"])
    assert result["status"] == "GENERATOR_EVIDENCE_BINDING_INVALID"
    assert not list(vault.glob("*.md"))


def test_old_cli_never_calls_provider():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_workflow.py"),
         "--workflow", "ai", "--mode", "production", "--confirm-vault-write"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert process.returncode == 4
    assert json.loads(process.stdout)["status"] == "LUNA_AUTOMATION_REQUIRED"


def test_cross_day_run_cannot_finalize(workflow, tmp_path):
    call, vault = workflow
    prepared, _, _ = ready(workflow)
    result = run_luna_stage(
        "finalize", run_id=prepared["run_id"], runtime_root=tmp_path,
        vault_daily_dir=vault, today=date(2026, 9, 9),
    )
    assert result["status"] == "RUN_DATE_EXPIRED"
    assert not list(vault.glob("*.md"))


def test_two_processes_cannot_prepare_the_same_daily(workflow, tmp_path):
    from pkm_workflow.ai_daily_production import acquire_production_lock, release_production_lock
    call, _ = workflow
    handle = acquire_production_lock(tmp_path / "ai-daily-production.lock")
    try:
        assert call("prepare")["status"] == "PRODUCTION_BUSY"
    finally:
        release_production_lock(handle)
    assert call("prepare")["status"] == "GENERATOR_READY"


def test_collection_failure_keeps_a_report_and_no_markdown(tmp_path):
    def fail(_day):
        raise TimeoutError("private network details")
    result = run_luna_stage(
        "prepare", runtime_root=tmp_path, vault_daily_dir=tmp_path,
        today=DAY, collect=fail,
    )
    assert result["status"] == "COLLECTION_FAILED"
    assert Path(result["report_path"]).is_file()
    assert "private network" not in json.dumps(result)
    assert not list(tmp_path.glob("scratch/runs/*/AI-Daily-*.md"))


def test_unknown_fields_do_not_receive_a_repair(workflow):
    call, _ = workflow
    prepared = call("prepare")
    invalid = draft()
    invalid["draft"]["stories"][0]["extra"] = "unknown"
    save(prepared["draft_path"], invalid)
    result = call("review", run_id=prepared["run_id"])
    assert result["status"] == "OUTPUT_SCHEMA_INVALID"
    assert "output_path" not in result


def test_reviewer_repair_does_not_bypass_complete_claim_review(workflow):
    call, _ = workflow
    prepared, reviewed, response = ready(workflow)
    response["decisions"][0]["decision"] = "APPROVED"
    save(reviewed["review_path"], response)
    repair = call("finalize", run_id=prepared["run_id"])
    assert repair["status"] == "SCHEMA_REPAIR_REQUIRED"
    response["decisions"][0]["decision"] = "ACCEPT"
    save(repair["output_path"], response)
    result = call("finalize", run_id=prepared["run_id"])
    assert result["status"] == "PUBLISHED"
    assert result["repair_count"] == 1
    assert result["role_execution_count"] == 3


def test_codex_canonical_agent_identity_is_preserved(workflow):
    call, _ = workflow
    prepared = call("prepare")
    envelope = draft()
    envelope["session_id"] = "/root/luna_daily_generator"
    save(prepared["draft_path"], envelope)
    assert call("review", run_id=prepared["run_id"])["status"] == "REVIEWER_READY"


def test_luna_cli_redacts_unclassified_stage_error(monkeypatch, capsys):
    from pkm_workflow import cli
    def fail(*args, **kwargs):
        raise RuntimeError("private path and credential")
    monkeypatch.setattr(cli, "run_luna_stage", fail)
    assert cli.main(["--workflow", "ai", "--mode", "shadow", "--stage", "prepare"]) == 10
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert "private" not in json.dumps(payload)
