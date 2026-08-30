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
    _model_safe_summary,
    run_ai_daily_shadow,
)
from pkm_workflow.deepseek_provider import DeepSeekShadowProvider, TransportResponse
from pkm_workflow.user_context_v75 import load_approved_user_context_v75


def test_model_evidence_omits_sentence_with_unscoped_rate() -> None:
    value = (
        "The attack uses a local struct.py file. "
        "The researcher claims it works 80% of the time without a sample count. "
        "Auto Mode then blocked the cleanup command."
    )

    result = _model_safe_summary(value)

    assert "80%" not in result
    assert "researcher claims" not in result
    assert "local struct.py" in result
    assert "blocked the cleanup" in result


class _GenerateReviewTransport:
    def __init__(
        self,
        *,
        empty: bool = False,
        reject_claim: str = "uncertainty-claim",
        unknown_evidence: bool = False,
        uncertainty_risk: str = "low",
        include_watch_and_action: bool = False,
        invalid_context_ref: bool = False,
        omit_impact: bool = False,
        uncertainty_statement: str = "所有网站都已完全兼容。",
        context_ref_override: str | None = None,
        reviewer_http_status: int = 200,
        duplicate_story_evidence: bool = False,
        importance_statement: str = "这会减少 Agent 工作流中的重复浏览器脚本。",
        single_mechanism: bool = False,
        mixed_evidence_id: str | None = None,
        title_statement: str = "Example AI 发布了新的 Agent 工具。",
        action_statement: str = "本周用一个浏览任务对比复用前后的脚本量。",
        action_object: str = "一个真实浏览任务",
        action_success_criterion: str = "复用后重复脚本行数下降",
        story_rank: int = 1,
    ) -> None:
        self.calls = 0
        self.empty = empty
        self.reject_claim = reject_claim
        self.unknown_evidence = unknown_evidence
        self.uncertainty_risk = uncertainty_risk
        self.include_watch_and_action = include_watch_and_action
        self.invalid_context_ref = invalid_context_ref
        self.omit_impact = omit_impact
        self.uncertainty_statement = uncertainty_statement
        self.context_ref_override = context_ref_override
        self.reviewer_http_status = reviewer_http_status
        self.duplicate_story_evidence = duplicate_story_evidence
        self.importance_statement = importance_statement
        self.single_mechanism = single_mechanism
        self.mixed_evidence_id = mixed_evidence_id
        self.title_statement = title_statement
        self.action_statement = action_statement
        self.action_object = action_object
        self.action_success_criterion = action_success_criterion
        self.story_rank = story_rank
        self.generator_profile_refs: list[str] = []
        self.generator_rubric: dict[str, object] = {}
        self.generator_calls = 0
        self.generator_candidate_ids: list[list[str]] = []
        self.generator_candidate_summaries: list[list[str]] = []
        self.semantic_corrections: list[str | None] = []

    def request(self, **kwargs: object) -> TransportResponse:
        self.calls += 1
        request = json.loads(bytes(kwargs["body"]))
        sealed = json.loads(request["messages"][1]["content"])["sealed_request"]
        if "approved_user_context" in sealed:
            self.generator_calls += 1
            self.generator_rubric = dict(sealed["rubric"])
            self.generator_candidate_ids.append(
                [
                    str(candidate["evidence_id"])
                    for candidate in sealed["sealed_evidence"]["candidates"]
                ]
            )
            self.generator_candidate_summaries.append(
                [
                    str(candidate["summary"])
                    for candidate in sealed["sealed_evidence"]["candidates"]
                ]
            )
            self.semantic_corrections.append(sealed["rubric"]["semantic_correction"])
            self.generator_profile_refs = list(
                sealed["sealed_evidence"]["candidates"][0]["profile_refs"]
            )
            fields = sealed["approved_user_context"]["fields"]
            priorities = fields.get("priorities", [])
            prior_knowledge = fields.get("prior_knowledge", [])
            context_ref = None
            if priorities and priorities[0].get("topics"):
                context_ref = "priorities[0].topics[0]"
            elif any(row.get("topic") == "SWE-bench" for row in prior_knowledge):
                context_ref = "prior_knowledge[1].topic"
            if self.invalid_context_ref:
                context_ref = "private_inference.unapproved"
            elif self.context_ref_override is not None:
                context_ref = self.context_ref_override

            def claim(
                claim_id: str,
                statement: str,
                risk: str,
                *,
                kind: str = "fact",
                context_refs: list[str] | None = None,
                action_type: str = "NONE",
                action_object: str = "",
                success_criterion: str = "",
            ) -> dict[str, object]:
                return {
                    "claim_id": claim_id,
                    "statement": statement,
                    "evidence_ids": [
                        (
                            "unknown-evidence"
                            if self.unknown_evidence and claim_id == "title-claim"
                            else self.mixed_evidence_id
                            if self.mixed_evidence_id is not None
                            and claim_id == "mechanism-claim-two"
                            else "evidence-1"
                        )
                    ],
                    "claimed_risk": risk,
                    "claim_kind": kind,
                    "context_refs": context_refs or [],
                    "action_type": action_type,
                    "action_object": action_object,
                    "success_criterion": success_criterion,
                }

            claims = [
                claim("title-claim", self.title_statement, "medium"),
                claim(
                    "event-claim",
                    "该工具新增了可复用的浏览器操作能力。",
                    "high",
                ),
                claim(
                    "mechanism-claim",
                    "浏览器操作被封装为可复用工具接口。",
                    "medium",
                ),
                *(
                    []
                    if self.single_mechanism
                    else [
                        claim(
                            "mechanism-claim-two",
                            "工具接口保留了可验证的操作结果与失败状态。",
                            "medium",
                        )
                    ]
                ),
                claim(
                    "importance-claim",
                    self.importance_statement,
                    "medium",
                ),
                *(
                    []
                    if self.include_watch_and_action
                    else [
                        claim(
                            "uncertainty-claim",
                            self.uncertainty_statement,
                            self.uncertainty_risk,
                        )
                    ]
                ),
            ]
            if context_ref and not self.omit_impact:
                claims.append(
                    claim(
                        "impact-claim",
                        "它与用户当前的 Agent 工程优先级直接相关。",
                        "medium",
                        kind="editorial_inference",
                        context_refs=[context_ref],
                    )
                )
            if self.include_watch_and_action:
                claims.extend(
                    [
                        claim(
                            "watch-claim",
                            "观察后续是否公布跨站点成功率。",
                            "low",
                        ),
                        claim(
                            "action-claim",
                            self.action_statement,
                            "low",
                            kind="action",
                            context_refs=[context_ref] if context_ref else [],
                            action_type="THIS_WEEK_EXPERIMENT",
                            action_object=self.action_object,
                            success_criterion=self.action_success_criterion,
                        ),
                    ]
                )
            structured = {"stories": []} if self.empty else {
                "stories": [
                    {
                        "story_id": "story-1",
                        "rank": self.story_rank,
                        "title_claim_id": "title-claim",
                        "event_claim_ids": ["event-claim"],
                        "mechanism_claim_ids": (
                            ["mechanism-claim"]
                            if self.single_mechanism
                            else ["mechanism-claim", "mechanism-claim-two"]
                        ),
                        "importance_claim_ids": ["importance-claim"],
                        "impact_claim_ids": (
                            ["impact-claim"] if context_ref and not self.omit_impact else []
                        ),
                        "uncertainty_claim_ids": (
                            [] if self.include_watch_and_action else ["uncertainty-claim"]
                        ),
                        "watch_claim_ids": (
                            ["watch-claim"] if self.include_watch_and_action else []
                        ),
                        "action_claim_ids": (
                            ["action-claim"] if self.include_watch_and_action else []
                        ),
                        "claims": claims,
                    }
                ]
            }
            if self.duplicate_story_evidence and structured["stories"]:
                duplicate = json.loads(json.dumps(structured["stories"][0]))
                id_map = {
                    claim["claim_id"]: f"{claim['claim_id']}-duplicate"
                    for claim in duplicate["claims"]
                }
                duplicate["story_id"] = "story-2"
                duplicate["rank"] = 2
                duplicate["title_claim_id"] = id_map[duplicate["title_claim_id"]]
                for field in (
                    "event_claim_ids",
                    "mechanism_claim_ids",
                    "importance_claim_ids",
                    "impact_claim_ids",
                    "uncertainty_claim_ids",
                    "watch_claim_ids",
                    "action_claim_ids",
                ):
                    duplicate[field] = [id_map[claim_id] for claim_id in duplicate[field]]
                for claim in duplicate["claims"]:
                    claim["claim_id"] = id_map[claim["claim_id"]]
                structured["stories"].append(duplicate)
        else:
            if self.reviewer_http_status != 200:
                return TransportResponse(
                    status=self.reviewer_http_status,
                    content_type="application/json",
                    content_encoding="identity",
                    body=b"{}",
                )
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
    transport = _GenerateReviewTransport(
        reject_claim="",
        uncertainty_statement="该能力只在来源描述的任务范围内成立。",
    )
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
    assert report["personalization_refs"] == []
    assert len(report["git_head"]) == 40
    assert report["audit"]["selected_urls"] == ["https://example.com/agent-release"]
    assert report["audit"]["generator_result_hash"].startswith("sha256:")
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Example AI 发布了新的 Agent 工具" in markdown
    assert "该工具新增了可复用的浏览器操作能力" in markdown
    assert "所有网站都已完全兼容" not in markdown
    assert "**原文** [Example AI](https://example.com/agent-release)" in markdown
    assert "evidence_date_start: 2026-08-25" in markdown
    assert "· 2026-08-25" in markdown
    assert "https://example.com/agent-release" in markdown
    assert "无需行动" not in markdown
    assert "## Top 2" not in markdown
    assert transport.calls == 2


def test_generator_receives_one_bounded_packet_that_keeps_late_high_value_sections(
    tmp_path: Path,
) -> None:
    filler = "作者反复描述视觉主题和背景制作过程。" * 400
    limitation = (
        "Limitations\n"
        "The coding agent grew the main file beyond 3,000 lines and left more than "
        "20 files in one flat directory. It then wrote Python scripts to edit the "
        "oversized file, which made each change slower and wasted tokens. The builder "
        "added CLAUDE.md rules for file size, directory structure, comments, and testing."
    )
    candidates = tuple(
        Candidate(
            evidence_id=f"evidence-{index + 1}",
            source=f"Builder {index + 1}",
            title=f"A long coding-agent build log {index + 1}",
            link=f"https://example.com/build-log-{index + 1}",
            published="2026-08-29",
            summary=f"{filler}\n{limitation}",
            content_type="engineering",
            fulltext_enriched=True,
            story_type="ai_practice",
        )
        for index in range(10)
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=candidates,
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.status == "PUBLISHED"
    packet = transport.generator_candidate_summaries[0]
    assert len(packet) == 10
    assert sum(len(summary) for summary in packet) <= 24_000
    assert all(len(summary) <= 4_000 for summary in packet)
    assert "3,000 lines" in packet[0]
    assert "CLAUDE.md" in packet[0]


def test_generator_sees_evidence_rich_candidates_before_thin_high_score_quotes(
    tmp_path: Path,
) -> None:
    thin_quote = Candidate(
        evidence_id="evidence-quote",
        source="Quote Blog",
        title="Quoting a model comment",
        link="https://example.com/quote",
        published="2026-08-29",
        summary="A short attributed quotation about coding models. " * 15,
        content_type="news",
        fulltext_enriched=True,
        story_type="ai_practice",
        metadata_score=9500,
        editorial_score=12000,
    )
    rich_report = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="A full benchmark and failure analysis",
        link="https://example.com/full-report",
        published="2026-08-28",
        summary=(
            "The report explains the benchmark, method, results, failure analysis, "
            "and operating limits in separate sections. " * 100
        ),
        content_type="research",
        fulltext_enriched=True,
        story_type="ai_practice",
        metadata_score=7000,
        editorial_score=8000,
    )
    transport = _GenerateReviewTransport(
        reject_claim="",
        uncertainty_statement="该结果只覆盖来源描述的任务范围。",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(thin_quote, rich_report),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.status == "PUBLISHED"
    assert transport.generator_candidate_ids[0][0] == "evidence-1"


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
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=9,
            healthy_sources=9,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert "prior_knowledge[1].topic" in report["personalization_refs"]
    assert "pillars[1].purpose" in transport.generator_profile_refs


def test_generator_receives_close_cross_type_diversity_target(tmp_path: Path) -> None:
    research = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="Agent harness evaluation release",
        link="https://example.com/research",
        published="2026-08-27",
        summary="Agent harness evaluation evidence and benchmark details.",
        content_type="research",
        fulltext_enriched=True,
        story_type="ai_practice",
        pillar_exposure=True,
        metadata_score=8000,
        editorial_score=10000,
    )
    builder = Candidate(
        evidence_id="evidence-builder",
        source="Builder",
        title="Agent product validation case study",
        link="https://example.com/builder",
        published="2026-08-27",
        summary=(
            "Builder launched through a public directory and reports 120 users, "
            "8 paid customers, and the acquisition channel."
        ),
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS",),
        pillar_exposure=True,
        metadata_score=7000,
        editorial_score=8500,
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(research, builder),
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

    result = run_ai_daily_shadow(date(2026, 8, 28), ports=ports)

    assert result.status == "PUBLISHED"
    assert transport.generator_rubric["diversity_evidence_id"] is None


def test_builder_without_observed_validation_is_not_forced_for_diversity(
    tmp_path: Path,
) -> None:
    research = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="Agent harness evaluation release",
        link="https://example.com/research",
        published="2026-08-27",
        summary="Agent harness evaluation evidence and benchmark details.",
        content_type="research",
        fulltext_enriched=True,
        story_type="ai_practice",
        pillar_exposure=True,
        metadata_score=9700,
        editorial_score=12200,
    )
    builder = Candidate(
        evidence_id="evidence-builder",
        source="Builder",
        title="A new hotel rating product",
        link="https://example.com/builder",
        published="2026-08-27",
        summary=(
            "The builder describes the product idea, normalized ratings, and an AI "
            "visual model, but gives no observed adoption or result."
        ),
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS",),
        pillar_exposure=True,
        metadata_score=6914,
        editorial_score=9414,
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(research, builder),
            audit={"diversity_metadata_gap": 1500},
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

    result = run_ai_daily_shadow(date(2026, 8, 28), ports=ports)

    assert result.status == "PUBLISHED"
    assert transport.generator_rubric["diversity_evidence_id"] is None


def test_builder_brief_can_publish_without_a_forced_action(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Builder",
        title="A builder ships an AI product",
        link="https://example.com/builder-product",
        published="2026-08-29",
        summary="The builder reports 120 users, 8 paid customers, and a launch channel.",
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS",),
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.story_count == 1
    assert result.markdown_path is not None
    assert "## 今天可以做" not in result.markdown_path.read_text(encoding="utf-8")
    assert transport.generator_calls == 1


def test_schema_repair_does_not_trigger_an_extra_semantic_generation(
    tmp_path: Path,
) -> None:
    class RepairThenActionTransport(_GenerateReviewTransport):
        def __init__(self) -> None:
            super().__init__(reject_claim="")
            self.injected_structural_failure = False
            self.single_mechanism = True

        def request(self, **kwargs: object) -> TransportResponse:
            request = json.loads(bytes(kwargs["body"]))
            sealed = json.loads(request["messages"][1]["content"])["sealed_request"]
            if (
                "approved_user_context" in sealed
                and not self.injected_structural_failure
            ):
                self.injected_structural_failure = True
                self.calls += 1
                envelope = {
                    "id": "response-structural-failure",
                    "object": "chat.completion",
                    "created": self.calls,
                    "model": "deepseek-v4-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"stories": "invalid"}),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 40,
                        "total_tokens": 140,
                    },
                }
                return TransportResponse(
                    status=200,
                    content_type="application/json",
                    content_encoding="identity",
                    body=json.dumps(envelope).encode(),
                )
            if sealed.get("rubric", {}).get("semantic_correction") is not None:
                self.single_mechanism = False
                self.include_watch_and_action = True
            return super().request(**kwargs)

    research = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="Agent harness evaluation release",
        link="https://example.com/research",
        published="2026-08-29",
        summary="Agent harness benchmark method and evaluation results.",
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
        pillars=("AGENTIC_RESEARCH",),
        pillar_exposure=True,
        metadata_score=9700,
        editorial_score=12200,
    )
    cognition = Candidate(
        evidence_id="evidence-cognition",
        source="Long-form Essay",
        title="A framework for institutional progress",
        link="https://example.com/cognition",
        published="2026-08-09",
        summary="A deep framework challenges assumptions about incentives and progress.",
        content_type="essay",
        fulltext_enriched=True,
        story_type="cognition",
        pillars=("COGNITION",),
        pillar_exposure=True,
        metadata_score=6000,
        editorial_score=8500,
    )
    builder = Candidate(
        evidence_id="evidence-builder",
        source="Builder",
        title="A builder ships an AI product",
        link="https://example.com/builder-product",
        published="2026-08-29",
        summary="The builder reports 120 users, 8 paid customers, and a launch channel.",
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS",),
        pillar_exposure=False,
        metadata_score=7500,
        editorial_score=8000,
    )
    transport = RepairThenActionTransport()
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(research, cognition, builder),
            audit={"diversity_metadata_gap": 1500},
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.status == "NO_SIGNIFICANT_NEWS"
    assert result.error_code is None
    assert result.request_count == 2
    assert result.markdown_path is None
    assert transport.generator_calls == 1


def test_exposed_cognition_can_be_the_cross_layer_diversity_target(
    tmp_path: Path,
) -> None:
    research = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="Agent harness evaluation release",
        link="https://example.com/research",
        published="2026-08-29",
        summary="Agent harness benchmark method and evaluation results.",
        content_type="research",
        fulltext_enriched=True,
        story_type="ai_practice",
        pillar_exposure=True,
        metadata_score=9700,
        editorial_score=12200,
    )
    cognition = Candidate(
        evidence_id="evidence-cognition",
        source="Long-form Essay",
        title="A framework for institutional progress",
        link="https://example.com/cognition",
        published="2026-08-09",
        summary="A deep framework challenges assumptions about incentives and progress.",
        content_type="essay",
        fulltext_enriched=True,
        story_type="cognition",
        pillars=("COGNITION",),
        pillar_exposure=True,
        metadata_score=6000,
        editorial_score=8500,
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(research, cognition),
            audit={"diversity_metadata_gap": 1500},
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

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.status == "PUBLISHED"
    assert transport.generator_rubric["diversity_evidence_id"] is None


def test_v75_story_without_personal_impact_remains_publishable(tmp_path: Path) -> None:
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
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                omit_impact=True,
                uncertainty_statement="该结果只覆盖来源描述的评测范围。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.story_count == 1
    assert result.markdown_path is not None


def test_percentage_without_sample_scope_never_reaches_reviewer_or_markdown(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="Prompt injection attack report",
        link="https://example.com/attack",
        published="2026-08-27",
        summary="The researcher claims the attack works 80% of the time.",
        content_type="research",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    transport = _GenerateReviewTransport(
        reject_claim="",
        title_statement="提示注入攻击的成功率约80%。",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 28), ports=ports)

    assert result.status == "SCHEMA_INVALID"
    assert result.error_code == "GENERATOR_UNSCOPED_RATE"
    assert result.markdown_path is None
    assert transport.generator_calls == 1


def test_v75_story_without_two_distinct_details_is_dropped_not_repaired(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="SWE-bench harness evaluation study",
        link="https://example.com/harness-study",
        published="2026-08-26",
        summary="A full harness evaluation study with methods and benchmark evidence.",
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", single_mechanism=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "NO_SIGNIFICANT_NEWS"
    assert result.error_code is None
    assert result.story_count == 0
    assert result.markdown_path is None


def test_research_story_renders_as_a_human_brief_not_a_claim_checklist(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="SWE-bench harness evaluation study",
        link="https://example.com/harness-study",
        published="2026-08-26",
        summary="A full harness evaluation study with methods and benchmark evidence.",
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", include_watch_and_action=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "### 1｜研究｜" in markdown
    assert "**简报判断**" in markdown
    assert "浏览器操作被封装为可复用工具接口。" in markdown
    assert "工具接口保留了可验证的操作结果与失败状态。" in markdown
    assert "## 今天可以做" in markdown
    assert "**原文** [Research Lab](https://example.com/harness-study)" in markdown
    for old_label in (
        "**研究发现**",
        "**方法与证据**",
        "**相对已有工作的增量**",
        "**证据**",
    ):
        assert old_label not in markdown


def test_public_markdown_uses_natural_editorial_copy_without_operational_sections(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="A coding-agent evaluation with concrete failure analysis",
        link="https://example.com/coding-agent-evaluation",
        published="2026-08-29",
        summary=(
            "The study evaluates a coding agent on a controlled repository task. "
            "It reports the failure path, verification method, and bounded result. " * 20
        ),
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                include_watch_and_action=True,
                context_ref_override="prior_knowledge[1].topic",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "### 1｜研究｜" in markdown
    assert "**简报判断**" in markdown
    assert "**原文** [Research Lab](https://example.com/coding-agent-evaluation)" in markdown
    assert "## 今天可以做" in markdown
    for old_surface in (
        "## 今日深读",
        "## 分层情报雷达",
        "**Takeaway**",
        "**对你的启发**",
        "慢层回顾",
        "> 信息范围",
        "> 今日覆盖",
        "> 已检查但未达质量门",
    ):
        assert old_surface not in markdown


def test_quote_curator_page_is_labeled_as_a_quote_page_not_original_source(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Simon Willison",
        title="Quoting Paul Dix",
        link="https://example.com/quoting-paul-dix",
        published="2026-08-29",
        summary="Simon Willison reproduces an attributed Paul Dix quotation. " * 30,
        content_type="news",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                uncertainty_statement="该判断只来自被收录的单段评论。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "**引用页** [Simon Willison]" in markdown
    assert "**原文** [Simon Willison]" not in markdown


def test_lower_rank_brief_keeps_judgment_without_repeating_the_section_label(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="A brief model-routing observation",
        link="https://example.com/model-routing",
        published="2026-08-29",
        summary="A bounded observation about model routing and cost. " * 30,
        content_type="engineering",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                story_rank=3,
                uncertainty_statement="该观察只覆盖来源描述的团队。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "这会减少 Agent 工作流中的重复浏览器脚本" in markdown
    assert "**简报判断**" not in markdown


@pytest.mark.parametrize(
    "claim_id",
    [
        "title-claim",
        "event-claim",
        "mechanism-claim",
        "importance-claim",
        "impact-claim",
    ],
)
def test_any_non_accept_material_claim_drops_the_whole_story(
    tmp_path: Path,
    claim_id: str,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Agent workflow evaluation",
        link="https://example.com/agent-workflow",
        published="2026-08-29",
        summary="A complete workflow evaluation with concrete implementation detail. " * 30,
        content_type="engineering",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim=claim_id,
                context_ref_override="prior_knowledge[1].topic",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.story_count == 0
    assert result.markdown_path is None


@pytest.mark.parametrize(
    ("claim_id", "include_watch_and_action", "rejected_text"),
    [
        ("uncertainty-claim", False, "所有网站都已完全兼容"),
        ("watch-claim", True, "观察后续是否公布跨站点成功率"),
        ("action-claim", True, "本周用一个浏览任务对比复用前后的脚本量"),
    ],
)
def test_non_accept_optional_claim_is_omitted_without_dropping_story(
    tmp_path: Path,
    claim_id: str,
    include_watch_and_action: bool,
    rejected_text: str,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Agent workflow evaluation",
        link="https://example.com/agent-workflow",
        published="2026-08-29",
        summary="A complete workflow evaluation with concrete implementation detail. " * 30,
        content_type="engineering",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim=claim_id,
                include_watch_and_action=include_watch_and_action,
                context_ref_override="prior_knowledge[1].topic",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.story_count == 1
    assert result.markdown_path is not None
    assert rejected_text not in result.markdown_path.read_text(encoding="utf-8")


def test_interest_only_context_does_not_render_a_personal_connection(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Agent workflow evaluation",
        link="https://example.com/agent-workflow",
        published="2026-08-29",
        summary="A complete workflow evaluation with concrete implementation detail. " * 30,
        content_type="engineering",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(reject_claim=""),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "用户当前" not in markdown
    assert "对你的启发" not in markdown


def test_reader_directed_copy_cannot_bypass_the_personalization_role(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Agent workflow evaluation",
        link="https://example.com/agent-workflow",
        published="2026-08-29",
        summary="A complete workflow evaluation with concrete implementation detail. " * 30,
        content_type="engineering",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                importance_statement="这会直接改善你的当前项目。",
                uncertainty_statement="该结果只覆盖来源描述的任务范围。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.story_count == 0
    assert result.markdown_path is None


def test_stock_editorial_leadin_is_removed_before_review_and_render(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Agent workflow evaluation",
        link="https://example.com/agent-workflow",
        published="2026-08-29",
        summary="A complete workflow evaluation with concrete implementation detail. " * 30,
        content_type="engineering",
        fulltext_enriched=True,
        story_type="ai_practice",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                importance_statement=(
                    "该观察表明：验证系统会改变 coding agent 的返工成本。"
                ),
                uncertainty_statement="该结果只覆盖来源描述的任务范围。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "该观察表明" not in markdown
    reader_body = markdown.split("# AI Daily", 1)[1].replace("https://", "")
    assert "：" not in reader_body
    assert "验证系统会改变 coding agent 的返工成本" in markdown


def test_first_party_builder_allegation_is_rendered_as_attributed_self_report(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Pieter Levels",
        title="Hotel review aggregation",
        link="https://example.com/hotel-reviews",
        published="2026-08-29",
        summary=(
            "Pieter Levels writes that booking platforms delete negative reviews. "
            "He built a rating aggregation product. " * 20
        ),
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        evidence_role="BUILDER_PRIMARY",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                title_statement="预订平台删除负面评价，Pieter 推出聚合评分产品。",
                importance_statement="预订平台删除差评给独立产品留下了机会。",
                uncertainty_statement="该产品目前只有作者提供的使用案例。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "据 Pieter Levels 自述" in markdown
    assert "据 Pieter Levels 自述，预订平台删除差评" in markdown
    assert "据 Pieter Levels 自述，Levels 称" not in markdown


def test_contributor_article_does_not_turn_the_hosting_platform_into_the_author(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Hugging Face Blog",
        title="Quantization-Aware Healing",
        link=(
            "https://huggingface.co/blog/MultiverseComputingCAI/"
            "quantization-aware-healing"
        ),
        published="2026-08-29",
        summary="A contributor article presents a bounded benchmark and method. " * 40,
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                title_statement=(
                    "Hugging Face 博客发布论文，提出量化感知修复方法。"
                ),
                importance_statement=(
                    "Hugging Face 的论文证明该方法改变了压缩模型的表现。"
                ),
                uncertainty_statement="结果只覆盖来源报告的基准范围。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert (
        "Hugging Face Blog 上由 MultiverseComputingCAI 署名的文章提出"
        in markdown
    )
    assert "Hugging Face 博客发布论文，提出" not in markdown
    assert "Hugging Face 的论文" not in markdown
    assert "MultiverseComputingCAI 在 Hugging Face Blog 发表的文章" in markdown


def test_generator_may_publish_one_strong_story_from_a_wider_candidate_pool(
    tmp_path: Path,
) -> None:
    candidates = tuple(
        Candidate(
            evidence_id=f"evidence-{index + 1}",
            source=f"Source {index + 1}",
            title=f"Candidate {index + 1}",
            link=f"https://example.com/candidate-{index + 1}",
            published="2026-08-29",
            summary="A complete public article with concrete implementation detail. " * 30,
            content_type="engineering",
            fulltext_enriched=True,
            story_type="research" if index == 0 else "builder",
        )
        for index in range(4)
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=candidates,
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 30), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.story_count == 1
    assert transport.generator_calls == 1


def test_cognition_investment_story_displays_both_reader_layers(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Investment Essay",
        title="Private market liquidity changes investor behavior",
        link="https://example.com/private-markets",
        published="2026-08-09",
        summary="A cognition framework about private markets, liquidity, and incentives.",
        content_type="essay",
        fulltext_enriched=True,
        story_type="cognition",
        pillars=("COGNITION", "VC"),
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", include_watch_and_action=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "### 1｜认知 × 创投｜" in markdown
    assert "慢层回顾" not in markdown


def test_research_story_without_testable_question_keeps_other_deep_takeaways(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="SWE-bench harness evaluation study",
        link="https://example.com/harness-study",
        published="2026-08-26",
        summary="A full harness evaluation study with methods and benchmark evidence.",
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    transport = _GenerateReviewTransport(reject_claim="")
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    assert result.story_count == 1
    assert transport.generator_calls == 1
    assert "**可做的复现或实验**" not in result.markdown_path.read_text(encoding="utf-8")


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


def test_rejected_optional_claim_is_omitted_from_an_accepted_story(
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
    assert "所有网站都已完全兼容" not in result.markdown_path.read_text(
        encoding="utf-8"
    )


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
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["audit"]["candidate_audit"][0]["generator_selected"] is True
    assert (
        report["audit"]["candidate_audit"][0]["disposition"]
        == "REVIEW_REJECTED_OR_STORY_DROPPED"
    )
    assert report["audit"]["review_decisions"]


def test_reviewer_rejecting_one_required_detail_drops_story(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example Agent method",
        link="https://example.com/method",
        published="2026-08-27",
        summary="A verified method with two evidence details.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(reject_claim="mechanism-claim"),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 28), ports=ports)

    assert result.story_count == 0
    assert result.markdown_path is None


def test_reviewer_rejecting_research_action_keeps_story_without_action(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Research Lab",
        title="SWE-bench Agent evaluation",
        link="https://example.com/research",
        published="2026-08-27",
        summary="A verified Agent benchmark study with method details.",
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="action-claim", include_watch_and_action=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 28), ports=ports)

    assert result.story_count == 1
    assert result.markdown_path is not None
    assert "本周用一个浏览任务" not in result.markdown_path.read_text(
        encoding="utf-8"
    )


def test_reviewer_rejecting_builder_action_keeps_story_without_action(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Builder",
        title="A builder ships an AI product",
        link="https://example.com/builder-product",
        published="2026-08-29",
        summary="The builder reports 120 users and a repeatable launch channel.",
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS",),
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="action-claim", include_watch_and_action=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.story_count == 1
    assert result.markdown_path is not None
    assert "本周用一个浏览任务" not in result.markdown_path.read_text(
        encoding="utf-8"
    )


def test_builder_action_must_operationalize_the_story_takeaway(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Investment Essay",
        title="Private market liquidity changes investor behavior",
        link="https://example.com/private-markets",
        published="2026-08-09",
        summary="An essay about private-market liquidity, lockups, and investment risk.",
        content_type="essay",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS", "VC"),
    )
    transport = _GenerateReviewTransport(
        reject_claim="",
        include_watch_and_action=True,
        importance_statement=(
            "私人市场依赖信息不对称，流动性错配会影响 AI 创业融资判断。"
        ),
        action_statement="本周阅读一份非本地报纸并记录 AI 报道，避免信息茧房。",
        action_object="一份非本地报纸",
        action_success_criterion="记录三条 AI 报道",
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.status == "NO_SIGNIFICANT_NEWS"
    assert result.story_count == 0
    assert result.markdown_path is None
    assert transport.generator_calls == 1


def test_security_story_never_publishes_an_active_exploit_experiment(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Security Researcher",
        title="Coding agent turns a vulnerability hint into an exploit",
        link="https://example.com/security-research",
        published="2026-08-29",
        summary=(
            "A coding agent found a path traversal vulnerability and generated exploit "
            "code after reading a public patch discussion."
        ),
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    transport = _GenerateReviewTransport(
        reject_claim="",
        include_watch_and_action=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=transport,
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 29), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "**可做的复现或实验**" not in markdown
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert "action-claim" not in report["audit"]["accepted_claim_ids"]


def test_one_story_cannot_mix_multiple_candidate_evidence_packages(
    tmp_path: Path,
) -> None:
    research = Candidate(
        evidence_id="evidence-1",
        source="Research",
        title="Agent evaluation",
        link="https://example.com/research",
        published="2026-08-27",
        summary="Agent evaluation evidence.",
        content_type="research",
        fulltext_enriched=True,
        story_type="research",
    )
    builder = Candidate(
        evidence_id="evidence-builder",
        source="Builder",
        title="Builder case study",
        link="https://example.com/builder",
        published="2026-08-27",
        summary="Builder product evidence.",
        content_type="builder",
        fulltext_enriched=True,
        story_type="builder",
        pillars=("BUILDERS",),
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(research, builder),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", mixed_evidence_id="evidence-builder"
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 28), ports=ports)

    assert result.status == "SCHEMA_INVALID"
    assert result.error_code == "GENERATOR_MULTIPLE_CANDIDATES_PER_STORY"
    assert result.markdown_path is None


def test_renderer_keeps_supported_judgment_without_weak_personal_copy(
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
            configured_sources=9,
            healthy_sources=9,
            candidates=(candidate,),
            evidence_level=CoverageLevel.B,
            evidence_healthy_sources=7,
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
    assert "它与用户当前的 Agent 工程优先级直接相关" not in markdown
    assert "所有网站都已完全兼容" not in markdown
    assert "观察后续是否公布跨站点成功率" in markdown
    assert "本周用一个浏览任务对比复用前后的脚本量" not in markdown
    assert "无需行动" not in markdown
    assert "operational_coverage: A" in markdown
    assert "evidence_availability: B" in markdown
    assert "部分来源今天没有可确认的完整正文" not in markdown
    assert "evidence_date_start: 2026-08-25" in markdown


def test_collection_contract_failure_returns_retryable_report(tmp_path: Path) -> None:
    ports = ShadowPorts(
        collect=lambda _content_date: (_ for _ in ()).throw(
            ValueError("SOURCE_MANIFEST_SCHEMA_INVALID")
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.exit_code == 4
    assert result.status == "COLLECTION_FAILED"
    assert result.error_code == "SOURCE_MANIFEST_SCHEMA_INVALID"
    assert result.report_path.is_file()
    assert result.markdown_path is None


def test_unapproved_context_reference_fails_closed_without_markdown(tmp_path: Path) -> None:
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
            transport=_GenerateReviewTransport(invalid_context_ref=True),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 26), ports=ports)

    assert result.status == "NO_SIGNIFICANT_NEWS"
    assert result.story_count == 0
    assert result.markdown_path is None


def test_bounded_excerpt_cannot_support_absence_claim(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI security incident report",
        link="https://example.com/security-report",
        published="2026-08-26",
        summary="A bounded canonical excerpt describing a security incident.",
        content_type="engineering",
        fulltext_enriched=True,
        source_complete=False,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                uncertainty_statement="技术报告未明确漏洞利用细节。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    assert "技术报告未明确漏洞利用细节" not in result.markdown_path.read_text(
        encoding="utf-8"
    )


def test_core_absence_claim_drops_the_story_without_invalidating_the_digest(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI security incident report",
        link="https://example.com/security-report",
        published="2026-08-26",
        summary="A bounded canonical excerpt describing a security incident.",
        content_type="engineering",
        fulltext_enriched=True,
        source_complete=False,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                importance_statement="技术报告没有披露漏洞利用细节。",
                uncertainty_statement="该结果只覆盖来源描述的任务范围。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "NO_SIGNIFICANT_NEWS"
    assert result.error_code is None
    assert result.story_count == 0
    assert result.markdown_path is None


@pytest.mark.parametrize(
    "statement",
    [
        "报告没有披露漏洞利用细节。",
        "报告未提及生产 worker 数量。",
        "报告省略了漏洞利用细节。",
        "报告缺少漏洞利用细节。",
        "The report does not mention exploit details.",
        "Exploit details remain undisclosed.",
        "The report omits exploit details.",
        "No exploit details are included.",
        "没有漏洞利用细节。",
    ],
)
def test_bounded_excerpt_rejects_structural_absence_phrasing(
    tmp_path: Path, statement: str
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example AI security incident report",
        link="https://example.com/security-report",
        published="2026-08-26",
        summary="A bounded canonical excerpt describing a security incident.",
        content_type="engineering",
        fulltext_enriched=True,
        source_complete=False,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", uncertainty_statement=statement
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None
    assert statement not in result.markdown_path.read_text(encoding="utf-8")


def test_valid_approved_context_path_need_not_appear_in_source_text(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Browser tool release",
        link="https://example.com/browser-tool",
        published="2026-08-26",
        summary="A canonical excerpt about a reusable browser tool.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", context_ref_override="projects[0]"
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"projects": ["Obsidian PKM Workflow"]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "PUBLISHED"
    assert result.markdown_path is not None


def test_one_evidence_candidate_cannot_be_split_into_multiple_stories(
    tmp_path: Path,
) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="One source roundup",
        link="https://example.com/roundup",
        published="2026-08-26",
        summary="One bounded evidence candidate containing a source roundup.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", duplicate_story_evidence=True
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.status == "SCHEMA_INVALID"
    assert result.error_code == "GENERATOR_EVIDENCE_REUSED_ACROSS_STORIES"
    assert result.markdown_path is None


def test_existing_scale_cannot_prove_a_new_product_direction(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Company announces a new Agent platform direction",
        link="https://example.com/new-direction",
        published="2026-08-26",
        summary="The company announced a new Agent platform direction.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="",
                importance_statement="既有年化收入和估值反映了 Agent 平台趋势。",
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.story_count == 0
    assert result.markdown_path is None


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
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["audit"]["candidate_audit"][0]["disposition"] == "GENERATOR_FAILED"


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
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert (
        report["audit"]["candidate_audit"][0]["disposition"]
        == "GENERATOR_SCHEMA_REJECTED"
    )


def test_reviewer_service_failure_records_candidate_disposition(tmp_path: Path) -> None:
    candidate = Candidate(
        evidence_id="evidence-1",
        source="Example AI",
        title="Example Agent release",
        link="https://example.com/release",
        published="2026-08-26",
        summary="A verified canonical excerpt about an Agent release.",
        content_type="engineering",
        fulltext_enriched=True,
    )
    ports = ShadowPorts(
        collect=lambda _content_date: CollectionResult(
            coverage=CoverageLevel.A,
            configured_sources=14,
            healthy_sources=14,
            candidates=(candidate,),
        ),
        provider=DeepSeekShadowProvider(
            credential_reader=lambda: bytearray(b"test-only-secret"),
            transport=_GenerateReviewTransport(
                reject_claim="", reviewer_http_status=503
            ),
        ),
        runtime_root=tmp_path,
        load_user_context=lambda: {
            "user_context_hash": "sha256:" + "1" * 64,
            "fields": {"priorities": [{"topics": ["Agent"]}]},
        },
    )

    result = run_ai_daily_shadow(date(2026, 8, 27), ports=ports)

    assert result.exit_code == 5
    assert result.markdown_path is None
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["audit"]["candidate_audit"][0]["disposition"] == "REVIEW_FAILED"
    assert report["audit"]["review_decisions"] == []
