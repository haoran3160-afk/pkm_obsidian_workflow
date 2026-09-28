"""Weekly findings are selected across interest lanes and backed by original links."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from pkm_workflow import weekly_brief
from pkm_workflow.ai_daily_luna import _published_urls, run_luna_stage
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel
from pkm_workflow.weekly_collection import collect_weekly

SUNDAY = date(2026, 9, 13)
GENERATOR_ID = "11111111-1111-4111-8111-111111111111"
REVIEWER_ID = "22222222-2222-4222-8222-222222222222"
CONTEXT = {"fields": {"projects": ["Agent research"]},
           "user_context_hash": "sha256:" + "a" * 64}


def sources():
    return (
        Candidate(
            evidence_id="paper", source="Research Lab", source_id="research-lab",
            canonical_origin_id="research-lab", story_type="research", evidence_role="PAPER_PRIMARY",
            title="Agent evaluation protocol", link="https://example.com/paper",
            published="2026-09-09", summary="The protocol records agent tool failures for repeatable evaluation.",
            content_type="weekly_excerpt", fulltext_enriched=True,
            source_links=(("Research Lab · 2026-09-09", "https://example.com/paper"),),
        ),
        Candidate(
            evidence_id="tool", source="Builder", source_id="builder",
            canonical_origin_id="builder", story_type="ai_practice",
            title="Agent workflow review", link="https://example.com/tool",
            published="2026-09-10", summary="The builder reviews failed tool calls before accepting output.",
            content_type="evergreen", fulltext_enriched=True,
            source_links=(("Builder · 2026-09-10", "https://example.com/tool"),),
        ),
    )


def entry(text, *ids):
    return {"text": text, "evidence_ids": list(ids)}


def weekly_draft():
    return {
        "lead": entry("本周两份材料都把失败记录放进 Agent 的改进循环。", "paper", "tool"),
        "signals": [{
            "kind": "PATTERN",
            "title": entry("失败记录开始进入评测与验收", "paper", "tool"),
            "body": [
                entry("研究原文记录工具调用失败，用于可重复评测。", "paper"),
                entry("Builder 在验收输出前检查失败的工具调用。", "tool"),
            ],
            "takeaway": entry("两种场景都把失败轨迹作为下一次决策的输入，样本仍有限。", "paper", "tool"),
            "connection": entry("你可以用这两种做法设计 Agent 研究的错误分析。", "paper", "tool")
            | {"context_refs": ["projects[0]"]},
            "watch": None,
        }],
    }


def test_weekly_pattern_requires_independent_evidence():
    draft = weekly_draft()
    for key in ("title", "takeaway", "connection"):
        draft["signals"][0][key]["evidence_ids"] = ["paper"]
    draft["signals"][0]["body"] = [entry("研究原文记录工具调用失败。", "paper")]
    with pytest.raises(ValueError, match="WEEKLY_PATTERN_EVIDENCE_INSUFFICIENT"):
        weekly_brief.validate_draft(draft, {"paper": sources()[0]}, CONTEXT, SUNDAY)


def test_same_origin_old_articles_do_not_qualify_as_this_week_development():
    paper, tool = sources()
    evidence = {
        "paper": replace(paper, canonical_origin_id="one-publisher", published="2024-09-09"),
        "tool": replace(tool, canonical_origin_id="one-publisher", published="2025-09-10"),
    }
    with pytest.raises(ValueError, match="WEEKLY_PATTERN_EVIDENCE_INSUFFICIENT"):
        weekly_brief.validate_draft(weekly_draft(), evidence, CONTEXT, SUNDAY)


def test_weekly_drops_a_rejected_finding_without_losing_approved_case():
    draft = weekly_draft()
    draft["signals"].append({
        "kind": "CASE", "title": entry("一个可复核的评测接口", "paper"),
        "body": [entry("原文记录了工具调用失败。", "paper")],
        "takeaway": entry("这一做法值得作为单个案例研究。", "paper"),
        "connection": entry("你可用它检查研究中的错误分析。", "paper")
        | {"context_refs": ["projects[0]"]},
        "watch": None,
    })
    stories = weekly_brief.validate_draft(
        draft, {source.evidence_id: source for source in sources()}, CONTEXT, SUNDAY,
    )
    decisions = {"decisions": [
        {"claim_id": row["claim_id"], "decision": (
            "REJECT" if row["claim_id"] == "weekly-1-title" else "ACCEPT"
        )}
        for story in stories for row in story["claims"]
    ]}
    accepted = weekly_brief.accepted_signals(stories, decisions)
    markdown = weekly_brief.render(
        SUNDAY, CoverageLevel.A, CoverageLevel.A, accepted,
        {source.evidence_id: source for source in sources()},
    )
    assert len(accepted) == 1
    assert "一个可复核的评测接口" in markdown
    assert "失败记录开始进入评测与验收" not in markdown
    assert "本周两份材料" not in markdown
    assert "https://example.com/tool" not in markdown


def test_weekly_optional_watchpoints_stop_at_two_without_discarding_signals():
    draft = weekly_draft()
    first = draft["signals"][0]
    first["kind"] = "CASE"
    first["watch"] = entry("核对下一次工具失败的记录。", "paper")
    for index in (2, 3):
        extra = json.loads(json.dumps(first))
        extra["title"]["text"] = f"另一条独立信号 {index}"
        extra["watch"]["text"] = f"观察额外信号 {index}"
        draft["signals"].append(extra)
    normalized = weekly_brief.validate_draft(
        draft, {source.evidence_id: source for source in sources()}, CONTEXT, SUNDAY,
    )
    assert len(normalized) == 3
    assert sum(len(signal["watch_claim_ids"]) for signal in normalized) == 2


def test_weekly_stages_publish_cross_lane_signal_without_six_headings(tmp_path):
    vault = tmp_path / "vault" / "30-Daily"
    vault.mkdir(parents=True)
    evidence = sources()

    def stage(name, **kwargs):
        return run_luna_stage(
            name, today=SUNDAY, mode="production", runtime_root=tmp_path,
            vault_daily_dir=vault,
            collect=lambda _day: CollectionResult(CoverageLevel.A, 6, 2, evidence),
            context_loader=lambda: CONTEXT, **kwargs,
        )

    prepared = stage("prepare")
    assert prepared["status"] == "GENERATOR_READY"
    assert prepared["required_model"] == "gpt-6-luna"
    envelope = {"model": "gpt-6-luna", "session_id": GENERATOR_ID, "draft": weekly_draft()}
    Path(prepared["draft_path"]).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    reviewed = stage("review", run_id=prepared["run_id"])
    assert reviewed["status"] == "REVIEWER_READY"
    packet = json.loads(Path(reviewed["review_input_path"]).read_text(encoding="utf-8"))
    answer = {
        "model": "gpt-6-luna", "session_id": REVIEWER_ID,
        "review_request_hash": reviewed["review_request_hash"],
        "decisions": [{
            "claim_id": row["claim_id"], "evidence_ids": row["evidence_ids"],
            "review_requirement_hash": row["review_requirement_hash"],
            "decision": "ACCEPT", "reason_code": "SUPPORTED_BY_SEALED_EVIDENCE",
        } for row in packet["rubric"]["requirements"]],
    }
    Path(reviewed["review_path"]).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    result = stage("finalize", run_id=prepared["run_id"], confirm_vault_write=True)
    assert result["vault_write"] is True
    assert result["story_count"] == 1
    note = Path(result["vault_path"]).read_text(encoding="utf-8")
    assert "失败记录开始进入评测与验收" in note
    assert "https://example.com/paper" in note and "https://example.com/tool" in note
    assert "[本周新增阅读 · Builder](https://example.com/tool)" in note
    assert "本周保留" not in note
    assert "学术研究 ·" not in note and "AI 实践 ·" not in note
    assert stage("prepare")["status"] == "ALREADY_EXISTS"
    assert _published_urls(tmp_path, vault, SUNDAY + timedelta(days=1)) == {
        "https://example.com/tool",
    }


def test_weekly_considers_verified_daily_candidate_not_published_in_daily(tmp_path):
    monday = date(2026, 9, 14)
    week_end = date(2026, 9, 20)
    vault = tmp_path / "vault" / "30-Daily"
    vault.mkdir(parents=True)
    research = sources()[0]
    practice = sources()[1]
    alternative = Candidate(
        evidence_id="unread", source="Independent Lab", source_id="independent-lab",
        canonical_origin_id="independent-lab", story_type="research", evidence_role="PAPER_PRIMARY",
        title="A second agent evaluation", link="https://example.com/unread",
        published="2026-09-14", summary="The paper audits tool-use failures in a second setting.",
        content_type="evergreen", fulltext_enriched=True,
    )
    invitation = Candidate(
        evidence_id="invitation", source="Event Host", source_id="event-host",
        canonical_origin_id="event-host", story_type="ai_practice",
        title="Register for an Agentic Engineering meetup",
        link="https://example.com/invitation", published="2026-09-14",
        summary="The page invites readers to an evening meetup.",
        content_type="news", fulltext_enriched=True, editorial_score=20000,
    )

    def daily_stage(name, **kwargs):
        return run_luna_stage(
            name, today=monday, mode="production", runtime_root=tmp_path,
            vault_daily_dir=vault,
            collect=lambda _day: CollectionResult(
                CoverageLevel.A, 6, 4, (research, practice, alternative, invitation),
            ),
            context_loader=lambda: CONTEXT, **kwargs,
        )

    prepared = daily_stage("prepare")
    stories = [{
        "section": section, "evidence_id": eid, "title": "可核验的 Agent 工作流",
        "body": ["原文记录了一种可核验的工具调用流程。"],
        "takeaway": "具体失败记录有助于检查评测边界。",
        "connection": "它可帮助你的 Agent 研究进行错误分析。",
        "context_refs": ["projects[0]"], "action": None, "priority": "USEFUL",
    } for section, eid in (("research", "paper"), ("ai_practice", "tool"))]
    Path(prepared["draft_path"]).write_text(json.dumps({
        "model": "gpt-6-luna", "session_id": GENERATOR_ID, "draft": {"stories": stories},
    }, ensure_ascii=False), encoding="utf-8")
    reviewed = daily_stage("review", run_id=prepared["run_id"])
    packet = json.loads(Path(reviewed["review_input_path"]).read_text(encoding="utf-8"))
    Path(reviewed["review_path"]).write_text(json.dumps({
        "model": "gpt-6-luna", "session_id": REVIEWER_ID,
        "review_request_hash": reviewed["review_request_hash"],
        "decisions": [{
            "claim_id": row["claim_id"], "evidence_ids": row["evidence_ids"],
            "review_requirement_hash": row["review_requirement_hash"],
            "decision": "ACCEPT", "reason_code": "SUPPORTED_BY_SEALED_EVIDENCE",
        } for row in packet["rubric"]["requirements"]],
    }), encoding="utf-8")
    final = daily_stage("finalize", run_id=prepared["run_id"], confirm_vault_write=True)
    assert final["vault_write"] is True
    report = json.loads(Path(final["shadow_report_path"]).read_text(encoding="utf-8"))
    assert {row["link"] for row in report["weekly_candidate_snapshot"]} == {
        "https://example.com/paper", "https://example.com/tool", "https://example.com/unread",
        "https://example.com/invitation",
    }
    assert "https://example.com/unread" not in _published_urls(tmp_path, vault, week_end)
    weekly = collect_weekly(week_end, tmp_path, vault)
    by_link = {candidate.link: candidate for candidate in weekly.candidates}
    assert "https://example.com/unread" in by_link
    assert "https://example.com/invitation" not in by_link
    assert by_link["https://example.com/unread"].content_type == "evergreen"
    assert by_link["https://example.com/paper"].content_type == "weekly_excerpt"
