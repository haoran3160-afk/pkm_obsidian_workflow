"""Weekly synthesis tests: thesis, mechanism, boundaries and watchlist."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pkm_workflow import weekly_brief
from pkm_workflow.ai_daily_luna import _published_urls, run_luna_stage
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel

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
        "thesis": entry("Agent 系统正在把失败轨迹变成下一次决策的输入。", "paper", "tool"),
        "themes": [{
            "kind": "TREND",
            "title": entry("失败轨迹从调试记录变成系统能力", "paper", "tool"),
            "observation": [
                entry("研究协议把工具调用失败记录下来，用于可重复评测。", "paper"),
                entry("Builder 在验收输出前检查失败的工具调用。", "tool"),
            ],
            "why_now": entry("研究评测与实际交付开始采用相同的失败反馈视角。", "paper", "tool"),
            "mechanism": entry("失败轨迹同时成为评测样本与下一次工具选择的约束。", "paper", "tool"),
            "implications": {
                "research": entry("可以把错误分析设计成 Agent 研究的主要变量。", "paper", "tool"),
                "practice": entry("工作流应保存失败轨迹，而不是只保存最终答案。", "tool"),
                "venture": None,
            },
            "counter_evidence": entry("当前只有两个来源，尚不足以证明整个行业都已转向。", "paper", "tool"),
            "connection": entry("这与 Agent 研究中寻找可重复评测信号直接相关。", "paper", "tool")
            | {"context_refs": ["projects[0]"]},
            "watch_next": entry("观察更多 Agent 评测是否把失败轨迹纳入公开指标。", "paper", "tool"),
        }],
        "missed_but_valuable": [{
            "title": entry("一份未进入日报的 Builder 评测实践", "tool"),
            "why": entry("它提供了可复现实验的具体记录方式。", "tool"),
        }],
        "watchlist": [{
            "subject": entry("Agent failure-trace benchmarks", "paper", "tool"),
            "why": entry("它可能把错误分析从附录提升为比较维度。", "paper", "tool"),
            "indicator": entry("下周出现公开数据集或 leaderboard 指标。", "paper", "tool"),
        }],
    }


def test_trend_requires_independent_evidence():
    draft = weekly_draft()
    draft["themes"][0]["title"]["evidence_ids"] = ["paper"]
    draft["themes"][0]["observation"] = [entry("研究协议记录工具失败。", "paper")]
    draft["themes"][0]["why_now"]["evidence_ids"] = ["paper"]
    draft["themes"][0]["mechanism"]["evidence_ids"] = ["paper"]
    draft["themes"][0]["connection"]["evidence_ids"] = ["paper"]
    draft["themes"][0]["implications"]["research"]["evidence_ids"] = ["paper"]
    draft["themes"][0]["implications"]["practice"] = None
    draft["themes"][0]["counter_evidence"]["evidence_ids"] = ["paper"]
    draft["themes"][0]["watch_next"]["evidence_ids"] = ["paper"]
    with pytest.raises(ValueError, match="WEEKLY_TREND_EVIDENCE_INSUFFICIENT"):
        weekly_brief.validate_draft(draft, {"paper": sources()[0]}, CONTEXT, SUNDAY)


def test_weekly_reviewer_schema_covers_the_weekly_claim_budget():
    schema = json.loads(weekly_brief.reviewer_schema())
    assert schema["properties"]["decisions"]["maxItems"] >= 65


def test_missed_reading_rejects_material_already_published_in_daily():
    draft = weekly_draft()
    draft["missed_but_valuable"][0]["title"]["evidence_ids"] = ["paper"]
    draft["missed_but_valuable"][0]["why"]["evidence_ids"] = ["paper"]
    with pytest.raises(ValueError, match="WEEKLY_MISSED_ALREADY_READ"):
        weekly_brief.validate_draft(draft, {row.evidence_id: row for row in sources()}, CONTEXT, SUNDAY)


def test_single_case_can_survive_without_being_called_a_trend():
    draft = weekly_draft()
    draft["themes"][0]["kind"] = "CASE"
    normalized = weekly_brief.validate_draft(draft, {row.evidence_id: row for row in sources()}, CONTEXT, SUNDAY)
    assert normalized[0]["kind"] == "CASE"
    assert any(row["claim_id"] == "weekly-thesis" for row in normalized[0]["claims"])


def test_rejected_theme_is_dropped_but_missed_and_watch_items_are_optional():
    draft = weekly_draft()
    draft["themes"].append({
        "kind": "CASE", "title": entry("不相关的个案", "paper"),
        "observation": [entry("原文有一个单独案例。", "paper")],
        "why_now": entry("它的边界值得记录。", "paper"),
        "mechanism": entry("案例暴露一个约束。", "paper"),
        "implications": {"research": None, "practice": None, "venture": None},
        "counter_evidence": None,
        "connection": entry("它与研究方向相关。", "paper") | {"context_refs": ["projects[0]"]},
        "watch_next": None,
    })
    normalized = weekly_brief.validate_draft(
        draft, {row.evidence_id: row for row in sources()}, CONTEXT, SUNDAY,
    )
    decisions = {"decisions": [
        {"claim_id": row["claim_id"], "decision": (
            "REJECT" if row["claim_id"].startswith("weekly-2-") else "ACCEPT"
        )}
        for signal in normalized for row in signal["claims"]
    ]}
    accepted = weekly_brief.accepted_signals(normalized, decisions)
    markdown = weekly_brief.render(
        SUNDAY, CoverageLevel.A, CoverageLevel.A, accepted,
        {row.evidence_id: row for row in sources()},
    )
    assert any(signal["story_id"] == "weekly-1" for signal in accepted)
    assert not any(signal["story_id"] == "weekly-2" for signal in accepted)
    assert "日报未选但值得补读" in markdown
    assert "下周观察" in markdown


def test_weekly_stages_cross_domain_synthesis_without_six_headings(tmp_path):
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
    note = Path(result["vault_path"]).read_text(encoding="utf-8")
    assert "本周主判断" in note
    assert "背后机制" in note and "边界与反方" in note
    assert "日报未选但值得补读" in note and "下周观察" in note
    assert "学术研究 ·" not in note and "AI 实践 ·" not in note
    assert stage("prepare")["status"] == "ALREADY_EXISTS"
    assert _published_urls(tmp_path, vault, SUNDAY + __import__("datetime").timedelta(days=1)) == {
        "https://example.com/tool",
    }
