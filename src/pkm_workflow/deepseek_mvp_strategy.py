"""Versioned D-018 strategy over the frozen D-009 DeepSeek provider.

The D-009 provider remains byte-for-byte attributable to its historical
transport/schema qualification.  This module records the user's new MVP
provider choice without pretending that the Codex model contract is already
adapted to the provider's schemas.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .deepseek_provider import (
    API_URL,
    MODEL_NAME,
    MVP_CONTRACT_VERSION,
    DeepSeekShadowProvider,
    ProviderRole,
    deepseek_provider_contract_descriptor,
    deepseek_shadow_qualification_binding,
    mvp_generator_output_schema,
    mvp_prompt_schedule_hash,
    mvp_reviewer_output_schema,
    reviewer_output_schema,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


_D009_BINDING = deepseek_shadow_qualification_binding()
_PROVIDER_CONTRACT = deepseek_provider_contract_descriptor()
_D018_BASE: dict[str, object] = {
    "decision": "D-018",
    "provider": "deepseek",
    "endpoint": API_URL,
    "generator_model": MODEL_NAME,
    "reviewer_model": MODEL_NAME,
    "request_topology": "SAME_PROVIDER_SAME_MODEL_FRESH_REQUESTS",
    "provider_contract": "D-009_TRANSPORT_SCHEMA_V1",
    "provider_binding_hash": _hash(_D009_BINDING),
    "provider_contract_hash": _PROVIDER_CONTRACT["descriptor_hash"],
    "billing_policy": "EXISTING_BALANCE_NO_AUTO_RECHARGE",
    "request_budget": 4,
    "production_eligible": False,
    "fallback_eligible": False,
    "model_contract_adapter": MVP_CONTRACT_VERSION,
    "generator_prompt_hash": mvp_prompt_schedule_hash(ProviderRole.GENERATOR),
    "reviewer_prompt_hash": mvp_prompt_schedule_hash(ProviderRole.REVIEWER),
    "generator_schema_hash": _hash(json.loads(mvp_generator_output_schema())),
    "reviewer_schema_hash": _hash(json.loads(reviewer_output_schema())),
}
_D018_BINDING = {**_D018_BASE, "strategy_hash": _hash(_D018_BASE)}


def deepseek_mvp_strategy_binding() -> dict[str, object]:
    """Return a fresh copy of the current shadow-only D-018 strategy."""

    return dict(_D018_BINDING)


@dataclass(frozen=True)
class DeepSeekMvpStrategy:
    """Factory surface for the shadow-only AI Daily model contract."""

    production_eligible: bool = False
    model_contract_adapter: str = MVP_CONTRACT_VERSION

    def new_provider(self) -> DeepSeekShadowProvider:
        return DeepSeekShadowProvider()

    @staticmethod
    def generator_schema() -> bytes:
        return mvp_generator_output_schema()

    @staticmethod
    def reviewer_schema() -> bytes:
        return mvp_reviewer_output_schema()
