from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pkm_workflow.ai_daily_shadow import (
    Candidate,
    CollectionResult,
    CoverageLevel,
    ShadowPorts,
    run_ai_daily_shadow,
)
from pkm_workflow.deepseek_provider import DeepSeekShadowProvider, TransportResponse


class _GenerateReviewTransport:
    def __init__(
        self,
        *,
        empty: bool = False,
        reject_claim: str = "uncertainty-claim",
        unknown_evidence: bool = False,
    ) -> None:
        self.calls = 0
        self.empty = empty
        self.reject_claim = reject_claim
        self.unknown_evidence = unknown_evidence

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
                        "mechanism_claim_ids": [],
                        "importance_claim_ids": ["importance-claim"],
                        "impact_claim_ids": ["impact-claim"],
                        "uncertainty_claim_ids": ["uncertainty-claim"],
                        "watch_claim_ids": [],
                        "action_claim_ids": [],
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
                                "claimed_risk": "high",
                            },
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
        profile_refs=("priorities[0].topics[0]",),
    )
    transport = _GenerateReviewTransport()
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
            "fields": {"priorities": [{"topics": ["Agent Engineering"]}]},
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
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Example AI 发布了新的 Agent 工具" in markdown
    assert "该工具新增了可复用的浏览器操作能力" in markdown
    assert "所有网站都已完全兼容" not in markdown
    assert "原文" in markdown
    assert "https://example.com/agent-release" in markdown
    assert "无需行动" in markdown
    assert "## Top 2" not in markdown
    assert transport.calls == 2


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
