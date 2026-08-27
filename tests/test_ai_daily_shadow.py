from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pkm_workflow.ai_daily_shadow import (
    Candidate,
    CollectionResult,
    CoverageLevel,
    ShadowPorts,
    _healthy_fetch,
    run_ai_daily_shadow,
)
from pkm_workflow.deepseek_provider import DeepSeekShadowProvider, TransportResponse
from pkm_workflow.user_context_v75 import load_approved_user_context_v75


class _GenerateReviewTransport:
    def __init__(
        self,
        *,
        empty: bool = False,
        reject_claim: str = "uncertainty-claim",
        unknown_evidence: bool = False,
        uncertainty_risk: str = "low",
        include_watch_and_action: bool = False,
    ) -> None:
        self.calls = 0
        self.empty = empty
        self.reject_claim = reject_claim
        self.unknown_evidence = unknown_evidence
        self.uncertainty_risk = uncertainty_risk
        self.include_watch_and_action = include_watch_and_action

    def request(self, **kwargs: object) -> TransportResponse:
        self.calls += 1
        request = json.loads(bytes(kwargs["body"]))
        sealed = json.loads(request["messages"][1]["content"])["sealed_request"]
        if self.calls == 1:
            structured = {"stories": []} if self.empty else {
                "stories": [
                    {
                        "story_id": "story-1",
                        "rank": 1,
                        "title_claim_id": "title-claim",
                        "event_claim_ids": ["event-claim"],
                        "mechanism_claim_ids": ["mechanism-claim"],
                        "importance_claim_ids": ["importance-claim"],
                        "impact_claim_ids": ["impact-claim"],
                        "uncertainty_claim_ids": ["uncertainty-claim"],
                        "watch_claim_ids": (
                            ["watch-claim"] if self.include_watch_and_action else []
                        ),
                        "action_claim_ids": (
                            ["action-claim"] if self.include_watch_and_action else []
                        ),
                        "claims": [
                            {
                                "claim_id": "title-claim",
                                "statement": "Example AI 发布了新的 Agent 工具。",
                                "evidence_ids": [
                                    "unknown-evidence"
                                    if self.unknown_evidence
                                    else "evidence-1"
                                ],
                                "claimed_risk": "medium",
                            },
                            {
                                "claim_id": "event-claim",
                                "statement": "该工具新增了可复用的浏览器操作能力。",
                                "evidence_ids": ["evidence-1"],
                                "claimed_risk": "high",
                            },
                            {
                                "claim_id": "mechanism-claim",
                                "statement": "浏览器操作被封装为可复用工具接口。",
                                "evidence_ids": ["evidence-1"],
                                "claimed_risk": "medium",
                            },
                            {
                                "claim_id": "importance-claim",
                                "statement": "这会减少 Agent 工作流中的重复浏览器脚本。",
                                "evidence_ids": ["evidence-1"],
                                "claimed_risk": "medium",
                            },
                            {
                                "claim_id": "impact-claim",
                                "statement": "它与用户当前的 Agent 工程优先级直接相关。",
                                "evidence_ids": ["evidence-1"],
                                "claimed_risk": "medium",
                            },
                            {
                                "claim_id": "uncertainty-claim",
                                "statement": "所有网站都已完全兼容。",
                                "evidence_ids": ["evidence-1"],
                                "claimed_risk": self.uncertainty_risk,
                            },
                            *(
                                [
                                    {
                                        "claim_id": "watch-claim",
                                        "statement": "观察后续是否公布跨站点成功率。",
                                        "evidence_ids": ["evidence-1"],
                                        "claimed_risk": "low",
                                    },
                                    {
                                        "claim_id": "action-claim",
                                        "statement": "本周用一个浏览任务对比复用前后的脚本量。",
                                        "evidence_ids": ["evidence-1"],
                                        "claimed_risk": "low",
                                    },
                                ]
                                if self.include_watch_and_action
                                else []
                            ),
                        ],
                    }
                ]
            }
        else:
            structured = {
                "decisions": [
                    {
                        "claim_id": requirement["claim_id"],
                        "evidence_ids": requirement["evidence_ids"],
                        "review_requirement_hash": requirement["review_requirement_hash"],
                        "decision": (
                            "ABSTAIN"
                            if requirement["claim_id"] == self.reject_claim
                            else "ACCEPT"
                        ),
                        "reason_code": (
                            "INSUFFICIENT_EVIDENCE"
                            if requirement["claim_id"] == self.reject_claim
                            else "SUPPORTED_BY_SEALED_EVIDENCE"
                        ),
                    }
                    for requirement in sealed["rubric"]["requirements"]
                ]
            }
        envelope = {
            "id": f"response-{self.calls}",
            "object": "chat.completion",
            "created": self.calls,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(structured)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
        }
        return TransportResponse(
            status=200,
            content_type="application/json",
            content_encoding="identity",
            body=json.dumps(envelope).encode(),
        )


def test_run_ai_daily_shadow_generates_reviewed_create_new_markdown(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI Agent tool release",
        link="https://example.com/agent-release",
        published="2026-08-25",
        summary=(
            "Example AI released a reusable browser operation tool for agents. "
            "The tool reduces repeated browser scripting."
        ),
        content_type="engineering",
        fulltext_enriched=True,
        profile_refs=(),
    )
    transport = _GenerateReviewTransport(reject_claim="uncertainty-claim")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=23,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.exit_code == 0
    assert result.status == "PUBLISHED"
    assert result.story_count == 1
    assert result.request_count == 2
    assert result.markdown_path is not None and result.markdown_path.is_file()
    assert result.report_path.is_file()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["model_metrics"]["total_tokens"] == 280
    assert report["personalization_refs"] == ["priorities[0].topics[0]"]
    assert len(report["git_head"]) == 40
    assert report["audit"]["selected_urls"] == ["https://example.com/agent-release"]
    assert report["audit"]["generator_result_hash"].startswith("sha256:")
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Example AI 发布了新的 Agent 工具" in markdown
    assert "该工具新增了可复用的浏览器操作能力" in markdown
    assert "所有网站都已完全兼容" not in markdown
    assert "原文" in markdown
    assert "近 72 小时补漏" in markdown
    assert "发布 2026-08-25" in markdown
    assert "https://example.com/agent-release" in markdown
    assert "无需行动" not in markdown
    assert "## Top 2" not in markdown
    assert transport.calls == 2


def test_v75_personalization_uses_exact_prior_knowledge_field_path(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="SWE-bench evaluation exposes coding-agent reliability gap",
        link="https://example.com/swe-bench-eval",
        published="2026-08-26",
        summary="A SWE-bench evaluation reports a coding-agent reliability gap.",
        content_type="research",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=9,
            healthy_sources=9,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(reject_claim=""),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["personalization_refs"] == ["prior_knowledge[1].topic"]


def test_coverage_a_without_candidates_is_credible_no_news_without_markdown(
    tmp_path: Path,
) -> None:
    transport = _GenerateReviewTransport()
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=24,
            candidates=(),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.exit_code == 0
    assert result.status == "NO_SIGNIFICANT_NEWS"
    assert result.markdown_path is None
    assert result.report_path.is_file()
    assert transport.calls == 0


@pytest.mark.parametrize(
    ("coverage", "evidence_level", "expected_status", "expected_error"),
    [
        (
            CoverageLevel.B,
            CoverageLevel.A,
            "COVERAGE_LIMITED",
            "COVERAGE_LIMITED_NO_NEWS",
        ),
        (
            CoverageLevel.A,
            CoverageLevel.B,
            "EVIDENCE_LIMITED",
            "EVIDENCE_LIMITED_NO_NEWS",
        ),
        (
            CoverageLevel.A,
            CoverageLevel.INSUFFICIENT,
            "EVIDENCE_INSUFFICIENT",
            "EVIDENCE_INSUFFICIENT",
        ),
    ],
)
def test_no_news_requires_both_operational_and_evidence_coverage_a(
    tmp_path: Path,
    coverage: CoverageLevel,
    evidence_level: CoverageLevel,
    expected_status: str,
    expected_error: str,
) -> None:
    transport = _GenerateReviewTransport()
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=coverage,
            configured_sources=9,
            healthy_sources=8,
            candidates=(),
            evidence_level=evidence_level,
            evidence_healthy_sources=6,
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.exit_code == 4
    assert result.status == expected_status
    assert result.error_code == expected_error
    assert result.markdown_path is None
    assert transport.calls == 0


def test_rejected_optional_claim_is_not_published_but_keeps_accepted_story(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI Agent tool release",
        link="https://example.com/agent-release",
        published="2026-08-25",
        summary="Example AI released a reusable browser operation tool for agents.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=24,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(uncertainty_risk="low"),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    assert result.story_count == 1
    assert "所有网站都已完全兼容" not in result.markdown_path.read_text(encoding="utf-8")


def test_rejected_event_claim_drops_the_entire_story(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI Agent tool release",
        link="https://example.com/agent-release",
        published="2026-08-25",
        summary="Example AI released a reusable browser operation tool for agents.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=9,
            healthy_sources=9,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(reject_claim="event-claim"),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.story_count == 0
    assert result.markdown_path is None


def test_renderer_keeps_all_accepted_visible_claim_roles(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI Agent tool release",
        link="https://example.com/agent-release",
        published="2026-08-25",
        summary="Example AI released a reusable browser operation tool for agents.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=9,
            healthy_sources=9,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", include_watch_and_action=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "它与用户当前的 Agent 工程优先级直接相关" in markdown
    assert "所有网站都已完全兼容" in markdown
    assert "观察后续是否公布跨站点成功率" in markdown
    assert "本周用一个浏览任务对比复用前后的脚本量" in markdown
    assert "无需行动" not in markdown


def test_only_success_or_zero_yield_counts_as_healthy_coverage() -> None:
    assert _healthy_fetch({"status": "ok", "reason_code": "OK"}) is True
    assert _healthy_fetch({"status": "warn", "reason_code": "ZERO_YIELD"}) is True
    assert _healthy_fetch({"status": "warn", "reason_code": "NETWORK_ERROR"}) is False


def test_report_failure_cannot_leave_a_markdown_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI Agent tool release",
        link="https://example.com/agent-release",
        published="2026-08-25",
        summary="Example AI released a reusable browser operation tool for agents.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=24,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(reject_claim=""),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )
    original_open = Path.open

    def fail_report(path: Path, *args: object, **kwargs: object):
        if path.name == "run-report.json":
            raise OSError("injected report failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_report)

    with pytest.raises(OSError, match="injected report failure"):
        run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert list(tmp_path.rglob("*.md")) == []


def test_link_failure_leaves_only_a_nonterminal_ready_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI Agent tool release",
        link="https://example.com/agent-release",
        published="2026-08-25",
        summary="Example AI released a reusable browser operation tool for agents.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=24,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(reject_claim=""),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    monkeypatch.setattr(
        Path,
        "hardlink_to",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected link failure")),
    )

    with pytest.raises(OSError, match="injected link failure"):
        run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert list(tmp_path.rglob("*.md")) == []
    report_paths = list(tmp_path.rglob("run-report.json"))
    assert len(report_paths) == 1
    report = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert report["status"] == "READY_FOR_SHADOW_LINK"


def test_auth_failure_returns_exit_8_and_never_creates_markdown(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example release",
        link="https://example.com/release",
        published="2026-08-25",
        summary="Example AI released a relevant engineering update.",
        content_type="engineering",
        fulltext_enriched=False,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=24,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: None,
            transport=_GenerateReviewTransport(),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.exit_code == 8
    assert result.status == "AUTH_REQUIRED"
    assert result.markdown_path is None
    assert result.report_path.is_file()


def test_unknown_generator_evidence_fails_closed_without_markdown(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example release",
        link="https://example.com/release",
        published="2026-08-25",
        summary="Example AI released a relevant engineering update.",
        content_type="engineering",
        fulltext_enriched=False,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=24,
            healthy_sources=24,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(unknown_evidence=True),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.exit_code == 4
    assert result.status == "SCHEMA_INVALID"
    assert result.markdown_path is None
    assert result.report_path.is_file()
