from __future__ import annotations

import hashlib
import json

from pkm_workflow.deepseek_mvp_strategy import (
    MVP_CONTRACT_VERSION,
    DeepSeekMvpStrategy,
    deepseek_mvp_strategy_binding,
    mvp_generator_output_schema,
    mvp_reviewer_output_schema,
)
from pkm_workflow.deepseek_provider import (
    API_URL,
    MODEL_NAME,
    DeepSeekShadowProvider,
    ProviderRole,
    RequestBudget,
    TerminalClassification,
    TransportResponse,
    deepseek_provider_contract_descriptor,
    deepseek_shadow_qualification_binding,
    generator_output_schema,
)


class _Transport:
    def __init__(self, structured: dict[str, object]) -> None:
        self.structured = structured

    def request(self, **_kwargs: object) -> TransportResponse:
        envelope = {
            "id": "response-ai-daily-v2",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(self.structured),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        return TransportResponse(
            status=200,
            content_type="application/json",
            content_encoding="identity",
            body=json.dumps(envelope).encode(),
        )


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest_payload(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def test_v74_strategy_uses_deepseek_api_and_no_codex_model_contract() -> None:
    binding = deepseek_mvp_strategy_binding()
    assert binding["decision"] == "D-018"
    assert binding["provider_contract"] == "D-009_TRANSPORT_SCHEMA_V1"
    assert binding["request_topology"] == "SAME_PROVIDER_SAME_MODEL_FRESH_REQUESTS"
    assert binding["billing_policy"] == "EXISTING_BALANCE_NO_AUTO_RECHARGE"
    assert binding["request_budget"] == 4
    assert binding["model_contract_adapter"] == MVP_CONTRACT_VERSION
    assert binding["production_eligible"] is False
    assert binding["fallback_eligible"] is False
    assert str(binding["strategy_hash"]).startswith("sha256:")
    assert binding["strategy_hash"] == (
        "sha256:9dabc93f796b14ade646a95311c03d04d5c5345b66b15305309b2daa0bb6f720"
    )
    assert API_URL == "https://api.deepseek.com/chat/completions"
    assert MODEL_NAME == "deepseek-v4-flash"
    assert DeepSeekShadowProvider.production_eligible is False
    assert DeepSeekMvpStrategy().model_contract_adapter == MVP_CONTRACT_VERSION
    historical = deepseek_shadow_qualification_binding()
    assert historical["decision"] == "D-009"
    assert historical["qualification_scope"] == "TRANSPORT_SCHEMA_CONTRACT_ONLY"


def test_d018_hash_binds_provider_prompts_schemas_request_and_credentials() -> None:
    descriptor = deepseek_provider_contract_descriptor()
    assert descriptor["descriptor_hash"] == (
        "sha256:5a20e7666b893dcb6d5cc8ebdd235f52a5b712349df63e68d2741a31fac89abb"
    )
    base = {key: value for key, value in descriptor.items() if key != "descriptor_hash"}
    encoded = json.dumps(
        base, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert descriptor["descriptor_hash"] == "sha256:" + hashlib.sha256(encoded).hexdigest()
    mutated = json.loads(json.dumps(base))
    mutated["request_config"]["max_tokens"] = 1
    mutated_hash = "sha256:" + hashlib.sha256(
        json.dumps(mutated, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert mutated_hash != descriptor["descriptor_hash"]


def test_strategy_preserves_d009_and_exposes_the_ai_daily_v2_contract() -> None:
    strategy = DeepSeekMvpStrategy()
    assert isinstance(strategy.new_provider(), DeepSeekShadowProvider)
    assert strategy.generator_schema() == mvp_generator_output_schema()
    assert strategy.reviewer_schema() == mvp_reviewer_output_schema()
    assert strategy.generator_schema() != generator_output_schema()


def test_ai_daily_generator_contract_allows_abstention_and_at_most_three_stories() -> None:
    schema = json.loads(mvp_generator_output_schema())
    stories = schema["properties"]["stories"]
    assert stories["minItems"] == 0
    assert stories["maxItems"] == 3
    story = stories["items"]
    assert set(story["required"]) == {
        "story_id",
        "rank",
        "title_claim_id",
        "event_claim_ids",
        "mechanism_claim_ids",
        "importance_claim_ids",
        "impact_claim_ids",
        "uncertainty_claim_ids",
        "watch_claim_ids",
        "action_claim_ids",
        "claims",
    }
    assert story["additionalProperties"] is False


def test_qualified_transport_accepts_ai_daily_abstention_without_repair() -> None:
    sealed = json.dumps(
        {
            "approved_user_context": {
                "fields": {"priorities": []},
                "user_context_hash": _digest("context"),
            },
            "rubric": {
                "contract_version": MVP_CONTRACT_VERSION,
                "max_stories": 3,
                "top_cap": 3,
            },
            "sealed_evidence": {
                "coverage_level": "A",
                "evidence_hash": _digest("evidence"),
                "candidates": [
                    {
                        "content_type": "news",
                        "evidence_id": "evidence-1",
                        "fulltext_enriched": False,
                        "published": "2026-08-25",
                        "source": "Example AI",
                        "summary": "A minor update with no material change.",
                        "title": "Minor update",
                    }
                ],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    provider = DeepSeekShadowProvider(
        credential_reader=lambda: bytearray(b"test-only-secret"),
        transport=_Transport({"stories": []}),
    )
    result = provider.invoke(
        ProviderRole.GENERATOR,
        sealed,
        mvp_generator_output_schema(),
        RequestBudget(limit=4),
        contract_version=MVP_CONTRACT_VERSION,
    )
    assert result.terminal_classification is TerminalClassification.VALIDATED
    assert result.validated_structured_result == {"stories": []}
    assert result.request_count == 1
    assert result.schema_repair_used is False
    assert result.prompt_hash == deepseek_mvp_strategy_binding()["generator_prompt_hash"]


def test_ai_daily_reviewer_requires_one_bound_decision_for_every_claim() -> None:
    claims = [
        {
            "claim_id": claim_id,
            "statement": statement,
            "evidence_ids": ["evidence-1"],
            "claimed_risk": "high" if claim_id == "event-claim" else "medium",
        }
        for claim_id, statement in (
            ("title-claim", "Example AI released a new model."),
            ("event-claim", "The release is available today."),
            ("importance-claim", "The release changes the available model choice."),
        )
    ]
    draft = {
        "stories": [
            {
                "story_id": "story-1",
                "rank": 1,
                "title_claim_id": "title-claim",
                "event_claim_ids": ["event-claim"],
                "mechanism_claim_ids": [],
                "importance_claim_ids": ["importance-claim"],
                "impact_claim_ids": [],
                "uncertainty_claim_ids": [],
                "watch_claim_ids": [],
                "action_claim_ids": [],
                "claims": claims,
            }
        ]
    }
    draft_hash = _digest_payload(draft)
    requirements = []
    decisions = []
    for claim in claims:
        requirement = {
            "claim_id": claim["claim_id"],
            "statement": claim["statement"],
            "evidence_ids": claim["evidence_ids"],
            "required": True,
            "generator_draft_hash": draft_hash,
        }
        requirement_hash = _digest_payload(requirement)
        requirements.append(requirement | {"review_requirement_hash": requirement_hash})
        decisions.append(
            {
                "claim_id": claim["claim_id"],
                "evidence_ids": claim["evidence_ids"],
                "review_requirement_hash": requirement_hash,
                "decision": "ACCEPT",
                "reason_code": "SUPPORTED_BY_SEALED_EVIDENCE",
            }
        )
    evidence = {
        "content_type": "news",
        "evidence_id": "evidence-1",
        "fulltext_enriched": True,
        "published": "2026-08-25",
        "source": "Example AI",
        "summary": "Example AI released a new model that is available today.",
        "title": "Example AI model release",
    }
    sealed = _canonical(
        {
            "generator_result": {"draft": draft, "structured_result_hash": draft_hash},
            "rubric": {
                "allowed_decisions": ["ACCEPT", "REJECT", "ABSTAIN"],
                "contract_version": MVP_CONTRACT_VERSION,
                "requirements": requirements,
            },
            "sealed_evidence": {
                "evidence_hash": _digest_payload([evidence]),
                "items": [evidence],
            },
        }
    )
    provider = DeepSeekShadowProvider(
        credential_reader=lambda: bytearray(b"test-only-secret"),
        transport=_Transport({"decisions": decisions}),
    )
    result = provider.invoke(
        ProviderRole.REVIEWER,
        sealed,
        mvp_reviewer_output_schema(),
        RequestBudget(limit=4),
        contract_version=MVP_CONTRACT_VERSION,
    )
    assert result.terminal_classification is TerminalClassification.VALIDATED
    assert result.validated_structured_result == {"decisions": decisions}
    assert result.request_count == 1


def test_request_body_disables_thinking_tools_and_streaming() -> None:
    provider = DeepSeekShadowProvider(
        credential_reader=lambda: bytearray(b"test-only-secret"),
        transport=object(),  # type: ignore[arg-type]
    )
    body = json.loads(
        provider._build_request_body(  # noqa: SLF001 - frozen transport contract
            role=ProviderRole.GENERATOR,
            sealed_request=b"{}",
            output_schema=generator_output_schema(),
            is_repair=False,
        )
    )
    assert body["model"] == "deepseek-v4-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    assert "tools" not in body
    assert "tool_choice" not in body
    assert generator_output_schema()
