from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pkm_workflow.ai_daily_luna import MODEL, run_luna_stage
from pkm_workflow.cadence import sections_for
from pkm_workflow.daily_brief import SECTIONS
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel

DAY = date(2026, 9, 7)
DAILY_SECTIONS = sections_for(DAY, "daily")
GENERATOR_ID = "11111111-1111-4111-8111-111111111111"
REVIEWER_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def workflow(tmp_path):
    vault = tmp_path / "vault" / "30-Daily"
    vault.mkdir(parents=True)
    candidates = tuple(Candidate(
        evidence_id=f"evidence-{section}", source="Research Lab", title="Agent tool reuse",
        link=f"https://example.com/{section}", published=DAY.isoformat(),
        summary="The agent reuses browser tool calls and reports failure states.",
        content_type="research", fulltext_enriched=True, source_complete=True,
        story_type=section,
        evidence_role="PAPER_PRIMARY" if section == "research" else "PRIMARY_OR_EXPERT",
    ) for section in DAILY_SECTIONS)
    context = {"fields": {"projects": ["Agent research"]}, "user_context_hash": "sha256:" + "a" * 64}
    def call(stage, **kwargs):
        return run_luna_stage(
            stage, runtime_root=tmp_path, vault_daily_dir=vault, today=DAY,
            collect=lambda _day: CollectionResult(CoverageLevel.A, 14, 14, candidates),
            context_loader=lambda: context, **kwargs,
        )
    return call, vault


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def draft():
    return {"model": MODEL, "session_id": GENERATOR_ID, "draft": {"stories": [
        {"section": section, "evidence_id": f"evidence-{section}",
         "title": "浏览器工具开始复用", "body": ["Agent 把重复操作整理为工具，并保留失败状态。"],
         "takeaway": "复用边界同时包含成功和失败，调用者才能少写重复处理。",
         "connection": "这一接口划分可以帮助理解 Agent 研究中的工具边界。",
         "context_refs": ["projects[0]"], "action": None, "priority": "USEFUL"}
        for section in DAILY_SECTIONS
    ]}}


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
    import sys
    assert "pkm_workflow.deepseek_provider" not in sys.modules
    call, vault = workflow
    prepared, _, _ = ready(workflow)
    shadow = call("finalize", run_id=prepared["run_id"])
    assert shadow["status"] == "PUBLISHED"
    assert shadow["vault_write"] is False
    assert "浏览器工具开始复用" in Path(shadow["markdown_path"]).read_text(encoding="utf-8")
    markdown = Path(shadow["markdown_path"]).read_text(encoding="utf-8")
    assert shadow["story_count"] == 2
    assert markdown.count("\n## ") == 2
    assert all(SECTIONS[key] in markdown for key in DAILY_SECTIONS)
    assert "★★★★☆" in markdown
    assert "🧪" in markdown and "🛠️" in markdown
    final = call("finalize", mode="production", run_id=prepared["run_id"], confirm_vault_write=True)
    assert final["vault_write"] is True
    assert (vault / f"AI-Daily-{DAY}.md").exists()
    assert call("prepare", mode="production")["status"] == "ALREADY_EXISTS"


def test_unread_classics_exclude_verified_history_older_than_thirty_days(workflow, tmp_path):
    from pkm_workflow.ai_daily_luna import _published_urls
    call, vault = workflow
    prepared, _, _ = ready(workflow)
    published = call("finalize", mode="production", run_id=prepared["run_id"], confirm_vault_write=True)
    assert published["vault_write"] is True
    assert _published_urls(tmp_path, vault, DAY + timedelta(days=61)) == {
        f"https://example.com/{section}" for section in DAILY_SECTIONS
    }


def test_duplicate_prepare_is_busy(workflow):
    call, _ = workflow
    first = call("prepare")
    assert call("prepare")["status"] == "PRODUCTION_BUSY"
    assert call("prepare", run_id=first["run_id"])["run_id"] == first["run_id"]


def test_interrupted_review_handoff_resumes_without_rewriting_input(workflow, monkeypatch):
    from pkm_workflow import ai_daily_production as publishing
    call, _ = workflow
    prepared = call("prepare")
    save(prepared["draft_path"], draft())
    original = publishing._write_new
    interrupted = False
    def fail_once(path, content):
        nonlocal interrupted
        if path.name == "reviewer-instructions.txt" and not interrupted:
            interrupted = True
            raise OSError("injected interruption")
        return original(path, content)
    monkeypatch.setattr(publishing, "_write_new", fail_once)
    assert call("review", run_id=prepared["run_id"])["exit_code"] == 4
    run = Path(prepared["draft_path"]).parent
    original_input = (run / "review-input.json").read_bytes()
    resumed = call("review", run_id=prepared["run_id"])
    assert resumed["status"] == "REVIEWER_READY"
    assert (run / "review-input.json").read_bytes() == original_input
    assert not (run / "repair.json").exists()


def test_interrupted_shadow_render_preserves_report_and_model_counts(workflow, monkeypatch):
    from pkm_workflow import ai_daily_production as publishing
    call, _ = workflow
    prepared, _, _ = ready(workflow)
    original = publishing._write_new
    interrupted = False
    def fail_once(path, content):
        nonlocal interrupted
        if path.name == "rendered-content.txt" and not interrupted:
            interrupted = True
            raise OSError("injected interruption")
        return original(path, content)
    monkeypatch.setattr(publishing, "_write_new", fail_once)
    assert call("finalize", run_id=prepared["run_id"])["exit_code"] == 4
    run = Path(prepared["draft_path"]).parent
    original_report = (run / "run-report.json").read_bytes()
    resumed = call("finalize", run_id=prepared["run_id"])
    assert resumed["status"] == "PUBLISHED"
    assert resumed["role_execution_count"] == 2
    assert resumed["repair_count"] == 0
    assert (run / "run-report.json").read_bytes() == original_report
    assert Path(resumed["markdown_path"]).is_file()


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
        updated["draft"]["stories"][0]["title"] = "修改后的未审核标题"
        save(prepared["draft_path"], updated)
    save(reviewed["review_path"], response)
    result = call("finalize", run_id=prepared["run_id"])
    assert result["exit_code"] != 0
    assert not list(Path(prepared["draft_path"]).parent.glob("AI-Daily-*.md"))
    assert not list(vault.glob("*.md"))


def test_rejected_material_claim_requires_one_revision_then_fresh_review(workflow):
    call, _ = workflow
    prepared, reviewed, response = ready(workflow)
    response["decisions"][1].update(decision="REJECT", reason_code="CONTRADICTED_BY_SEALED_EVIDENCE")
    save(reviewed["review_path"], response)
    result = call("finalize", run_id=prepared["run_id"])
    assert result["status"] == "EDITORIAL_REVISION_REQUIRED"
    assert not list(Path(prepared["draft_path"]).parent.glob("AI-Daily-*.md"))
    save(result["output_path"], draft())
    next_review = call("review", run_id=prepared["run_id"])
    packet = json.loads(Path(next_review["review_input_path"]).read_text(encoding="utf-8"))
    response["review_request_hash"] = next_review["review_request_hash"]
    response["session_id"] = "33333333-3333-4333-8333-333333333333"
    response["decisions"] = [
        {"claim_id": row["claim_id"], "evidence_ids": row["evidence_ids"],
         "review_requirement_hash": row["review_requirement_hash"],
         "decision": "REJECT" if row["claim_id"] == "research-body-1" else "ACCEPT",
         "reason_code": "CONTRADICTED_BY_SEALED_EVIDENCE" if row["claim_id"] == "research-body-1" else "SUPPORTED_BY_SEALED_EVIDENCE"}
        for row in packet["rubric"]["requirements"]
    ]
    save(next_review["review_path"], response)
    exhausted = call("finalize", run_id=prepared["run_id"])
    assert exhausted["status"] == "MODULE_REVIEW_INCOMPLETE"
    assert exhausted["missing_modules"] == ["research"]
    assert exhausted["markdown_path"] is None
    assert exhausted["role_execution_count"] == 4


@pytest.mark.parametrize("coverage,evidence,expected", [
    (CoverageLevel.INSUFFICIENT, CoverageLevel.A, "COVERAGE_INSUFFICIENT"),
    (CoverageLevel.A, CoverageLevel.INSUFFICIENT, "EVIDENCE_INSUFFICIENT"),
    (CoverageLevel.A, CoverageLevel.A, "MODULE_SOURCES_INCOMPLETE"),
    (CoverageLevel.B, CoverageLevel.A, "MODULE_SOURCES_INCOMPLETE"),
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


def test_plain_action_review_does_not_invent_empty_legacy_fields(workflow):
    call, _ = workflow
    prepared = call("prepare")
    generated = draft()
    generated["draft"]["stories"][0]["action"] = "记录一次浏览器工具失败轨迹，确认错误原因被保留。"
    save(prepared["draft_path"], generated)
    reviewed = call("review", run_id=prepared["run_id"])
    packet = json.loads(Path(reviewed["review_input_path"]).read_text(encoding="utf-8"))
    action = next(row for row in packet["rubric"]["requirements"] if row["claim_id"] == "research-action")
    assert action["statement"] == generated["draft"]["stories"][0]["action"]
    assert action["context_refs"] == ["projects[0]"]
    assert not {"action_object", "action_type", "success_criterion"} & action.keys()


def test_reviewer_reason_pair_requires_explicit_single_repair(workflow):
    call, _ = workflow
    prepared, reviewed, response = ready(workflow)
    response["decisions"][0].update(decision="REJECT", reason_code="INSUFFICIENT_EVIDENCE")
    save(reviewed["review_path"], response)
    repair = call("finalize", run_id=prepared["run_id"])
    assert repair["status"] == "SCHEMA_REPAIR_REQUIRED"
    assert repair["error_code"] == "REVIEW_REASON_MISMATCH"
    assert not list(Path(prepared["draft_path"]).parent.glob("AI-Daily-*.md"))
    save(repair["output_path"], response)
    assert call("finalize", run_id=prepared["run_id"])["status"] == "SCHEMA_REPAIR_EXHAUSTED"


def test_single_structure_repair_budget(workflow):
    call, _ = workflow
    prepared = call("prepare")
    invalid = draft()
    invalid["draft"]["stories"][0]["body"] = "not an array"
    save(prepared["draft_path"], invalid)
    repair = call("review", run_id=prepared["run_id"])
    assert repair["status"] == "SCHEMA_REPAIR_REQUIRED"
    save(repair["output_path"], invalid)
    exhausted = call("review", run_id=prepared["run_id"])
    assert exhausted["status"] == "SCHEMA_REPAIR_EXHAUSTED"


def test_delivered_schema_describes_full_role_envelope_and_bare_draft_can_repair_once(workflow):
    from pkm_workflow.daily_brief import matches_schema
    call, _ = workflow
    prepared = call("prepare")
    schema = json.loads(Path(prepared["schema_path"]).read_text(encoding="utf-8"))
    assert matches_schema(draft(), schema)
    assert not matches_schema(draft()["draft"], schema)
    save(prepared["draft_path"], draft()["draft"])
    repair = call("review", run_id=prepared["run_id"])
    assert repair["status"] == "SCHEMA_REPAIR_REQUIRED"
    save(repair["output_path"], draft())
    assert call("review", run_id=prepared["run_id"])["status"] == "REVIEWER_READY"


def test_model_name_cannot_stand_in_for_real_role_identity(workflow):
    call, _ = workflow
    prepared = call("prepare")
    generated = draft()
    generated["session_id"] = MODEL
    save(prepared["draft_path"], generated)
    repair = call("review", run_id=prepared["run_id"])
    assert repair["status"] == "SCHEMA_REPAIR_REQUIRED"
    assert repair["error_code"] == "ROLE_SESSION_ID_REQUIRED"
    assert not list(Path(prepared["draft_path"]).parent.glob("AI-Daily-*.md"))
    generated["session_id"] = GENERATOR_ID
    save(repair["output_path"], generated)
    assert call("review", run_id=prepared["run_id"])["status"] == "REVIEWER_READY"


def test_bad_evidence_is_not_repaired_or_published(workflow):
    call, vault = workflow
    prepared = call("prepare")
    invalid = draft()
    invalid["draft"]["stories"][0]["evidence_id"] = "unbound"
    save(prepared["draft_path"], invalid)
    result = call("review", run_id=prepared["run_id"])
    assert result["status"] == "GENERATOR_EVIDENCE_BINDING_INVALID"
    assert not list(vault.glob("*.md"))


def test_research_slot_rejects_institutional_news_even_when_keywords_match():
    from dataclasses import replace

    from pkm_workflow.daily_brief import validate_draft
    candidates = {section: Candidate(
        evidence_id=f"evidence-{section}", source="Research institute", title="Agent benchmark security update",
        link=f"https://example.com/{section}", published=DAY.isoformat(), summary="Agent evaluation",
        content_type="news", fulltext_enriched=True, story_type=section,
    ) for section in SECTIONS}
    context = {"fields": {"projects": ["Agent research"]}}
    evidence = {row.evidence_id: row for row in candidates.values()}
    with pytest.raises(ValueError, match="PRIMARY_RESEARCH_PAPER_REQUIRED"):
        validate_draft(draft()["draft"], evidence, context, requested_sections=DAILY_SECTIONS)
    evidence["evidence-research"] = replace(candidates["research"], evidence_role="PAPER_PRIMARY")
    assert len(validate_draft(draft()["draft"], evidence, context, requested_sections=DAILY_SECTIONS)) == 2


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
