from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from workflow_contracts import (
    AuthorizationContext,
    CurationDecision,
    CurationDecisionKind,
    EventType,
    ExecutionMode,
    FinalManifest,
    PublicationState,
    RawItem,
    RunStatus,
    SourceHealth,
    SourceSpec,
    StageStatus,
    assert_run_transition,
)

NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def _authorization(mode: ExecutionMode = ExecutionMode.SHADOW) -> AuthorizationContext:
    return AuthorizationContext(
        effective_mode=mode,
        mode_epoch="mode-0001",
        release_manifest_hash="a" * 64,
        spec_hash="b" * 64,
        config_hash="c" * 64,
        source_baseline_hash="d" * 64,
        authorization_lineage_id="lineage-0001",
    )


def test_source_identity_is_per_observed_record_not_per_endpoint() -> None:
    shared = {
        "fetch_target_id": "fetch-example",
        "name": "Example",
        "adapter": "rss",
        "enabled": True,
        "intent": ["ai_engineering"],
        "trust_tier": "primary",
        "status": SourceHealth.ACTIVE,
        "endpoint": "https://example.com/feed.xml",
        "endpoint_version": 1,
        "owner": "local-user",
        "created_at": NOW,
    }

    first = SourceSpec(
        source_id="source-a",
        legacy_record_id="private-config:0",
        observed_record_id="observed-a",
        origin_config_path="private/pkm_config.json",
        origin_record_index=0,
        **shared,
    )
    second = SourceSpec(
        source_id="source-b",
        legacy_record_id="public-config:0",
        observed_record_id="observed-b",
        origin_config_path="public/pkm_config.json",
        origin_record_index=0,
        **shared,
    )

    assert first.endpoint == second.endpoint
    assert first.source_id != second.source_id
    assert first.observed_record_id != second.observed_record_id


def test_contract_models_are_frozen_and_reject_unknown_fields() -> None:
    source = SourceSpec(
        source_id="source-a",
        legacy_record_id="private-config:0",
        observed_record_id="observed-a",
        fetch_target_id="fetch-a",
        origin_config_path="private/pkm_config.json",
        origin_record_index=0,
        name="Example",
        adapter="rss",
        enabled=True,
        intent=["research"],
        trust_tier="primary",
        status=SourceHealth.ACTIVE,
        endpoint="https://example.com/feed.xml",
        endpoint_version=1,
        owner="local-user",
        created_at=NOW,
    )

    with pytest.raises(ValidationError):
        SourceSpec.model_validate({**source.model_dump(), "unexpected": True})

    with pytest.raises(ValidationError):
        source.endpoint_version = 2  # type: ignore[misc]


def test_aborted_is_an_event_not_a_run_status() -> None:
    assert EventType.ABORTED.value == "ABORTED"
    assert "ABORTED" not in {status.value for status in RunStatus}
    assert {status.value for status in PublicationState} == {
        "PENDING__",
        "PARTIAL__",
        "COMMITTED",
    }
    assert all(len(status.value.encode("ascii")) == 9 for status in PublicationState)


def test_committed_manifest_requires_every_required_stage_to_succeed() -> None:
    raw = RawItem(
        item_id="item-1",
        source_id="source-a",
        canonical_url="https://example.com/article",
        guid="guid-1",
        title="Evidence",
        published_at=NOW,
        fetched_at=NOW,
        content_hash="e" * 64,
        raw_ref="runtime/raw/item-1.json",
        evidence_spans=["paragraph:1"],
    )
    decision = CurationDecision(
        run_id="run-1",
        item_ids=[raw.item_id],
        cluster_id="cluster-1",
        decision=CurationDecisionKind.SELECTED,
        reason_codes=["hard-signal"],
        score_components={"evidence": 1.0},
        evidence_refs=[raw.raw_ref],
        policy_version="policy-v1",
        prompt_version="prompt-v1",
        model="test-model",
        confidence=0.9,
    )

    with pytest.raises(ValidationError, match="required stages"):
        FinalManifest(
            run_id=decision.run_id,
            idempotency_key="daily:2026-07-26",
            fencing_token=1,
            started_at=NOW,
            stages={
                "ingest": StageStatus.SUCCEEDED,
                "curate": StageStatus.SUCCEEDED,
                "validate": StageStatus.SUCCEEDED,
                "commit": StageStatus.FAILED,
                "verify": StageStatus.SUCCEEDED,
            },
            counts={"selected": 1},
            durations={"ingest": 1.0},
            source_health_summary={SourceHealth.ACTIVE.value: 1},
            overall_status=RunStatus.COMMITTED,
            authorization=_authorization(),
        )


def test_terminal_run_state_cannot_transition() -> None:
    for terminal in (
        RunStatus.PARTIAL,
        RunStatus.COMMITTED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    ):
        with pytest.raises(ValueError, match="terminal"):
            assert_run_transition(terminal, RunStatus.PENDING)

    assert_run_transition(RunStatus.PENDING, RunStatus.PARTIAL)
