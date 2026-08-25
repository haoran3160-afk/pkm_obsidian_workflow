from __future__ import annotations

import hashlib
import json

from pkm_workflow.deepseek_mvp_strategy import (
    DeepSeekMvpStrategy,
    deepseek_mvp_strategy_binding,
)
from pkm_workflow.deepseek_provider import (
    API_URL,
    MODEL_NAME,
    DeepSeekShadowProvider,
    ProviderRole,
    deepseek_provider_contract_descriptor,
    deepseek_shadow_qualification_binding,
    generator_output_schema,
)


def test_v74_strategy_uses_deepseek_api_and_no_codex_model_contract() -> None:
    binding = deepseek_mvp_strategy_binding()
    assert binding["decision"] == "D-018"
    assert binding["provider_contract"] == "D-009_TRANSPORT_SCHEMA_V1"
    assert binding["request_topology"] == "SAME_PROVIDER_SAME_MODEL_FRESH_REQUESTS"
    assert binding["billing_policy"] == "EXISTING_BALANCE_NO_AUTO_RECHARGE"
    assert binding["request_budget"] == 4
    assert binding["model_contract_adapter"] == "NOT_IMPLEMENTED"
    assert binding["production_eligible"] is False
    assert binding["fallback_eligible"] is False
    assert str(binding["strategy_hash"]).startswith("sha256:")
    assert binding["strategy_hash"] == (
        "sha256:9ae19695350d022f9888743c445d1749398a900091366f445590088bbace22af"
    )
    assert API_URL == "https://api.deepseek.com/chat/completions"
    assert MODEL_NAME == "deepseek-v4-flash"
    assert DeepSeekShadowProvider.production_eligible is False
    assert DeepSeekMvpStrategy().model_contract_adapter == "NOT_IMPLEMENTED"
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


def test_strategy_factory_exposes_only_the_versioned_d009_provider_contract() -> None:
    strategy = DeepSeekMvpStrategy()
    assert isinstance(strategy.new_provider(), DeepSeekShadowProvider)
    assert strategy.generator_schema() == generator_output_schema()
    assert strategy.reviewer_schema()


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
