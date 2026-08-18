from __future__ import annotations

import hashlib
import inspect
from collections import Counter
from datetime import date

import pytest

from pkm_workflow.provenance import (
    CoverageProof,
    ObservationOutcome,
    ObservationScope,
    ObservationSet,
    PolicyState,
    SourcePolicyEntry,
    SourcePolicyGeneration,
    SourceSnapshot,
    TerminalObservation,
    Workflow,
)
from pkm_workflow.source_governance import (
    CoverageDecision,
    CoverageEvaluator,
    CoverageLevel,
    SourceLane,
    ensure_additive_multiset,
    stable_source_id,
)
from pkm_workflow.source_import import SourceBaseline, build_source_baseline


def _baseline(*, copies: int = 1, include_disabled: bool = True) -> SourceBaseline:
    feeds: list[dict[str, object]] = []
    for lane in SourceLane:
        for group_index in range(2):
            endpoint = f"https://example.com/{lane.value}/{group_index}"
            for copy_index in range(copies):
                feeds.append(
                    {
                        "name": f"{lane.value}-{group_index}-{copy_index}",
                        "url": endpoint,
                    }
                )
    if include_disabled:
        feeds.append(
            {
                "name": "legacy disabled",
                "url": "https://example.com/disabled",
                "enabled": False,
            }
        )
    return build_source_baseline(
        (
            (
                "synthetic",
                {"rss_feeds": feeds},
                hashlib.sha256(b"sources").hexdigest(),
            ),
        )
    )


def _lane_for_endpoint(endpoint: str) -> SourceLane:
    return next(lane for lane in SourceLane if f"/{lane.value}/" in endpoint)


def _policy(
    baseline: SourceBaseline,
    *,
    weight_bps: int = 1_000,
) -> SourcePolicyGeneration:
    entries: list[SourcePolicyEntry] = []
    groups: dict[str, str] = {}
    for record in baseline.records:
        if not record.enabled:
            entries.append(
                SourcePolicyEntry(
                    source_id=record.source_id,
                    workflows=(),
                    lane=None,
                    origin_group="legacy-disabled",
                    weight_bps=weight_bps,
                    endpoint_generation="endpoint-gen-1",
                    evidence_role="legacy-preserved",
                    state=PolicyState.LEGACY_DISABLED_PRESERVED,
                )
            )
            continue
        lane = _lane_for_endpoint(record.endpoint)
        origin_group = groups.setdefault(
            record.endpoint,
            f"{lane.value}-group-{record.endpoint.rsplit('/', 1)[-1]}",
        )
        entries.append(
            SourcePolicyEntry(
                source_id=record.source_id,
                workflows=(Workflow.AI, Workflow.WEEKLY),
                lane=lane,
                origin_group=origin_group,
                weight_bps=weight_bps,
                endpoint_generation="endpoint-gen-1",
                evidence_role="primary",
                state=PolicyState.ACTIVE,
            )
        )
    return SourcePolicyGeneration.create(
        baseline,
        entries=tuple(entries),
        approval_generation="approval-1",
    )


def _snapshot(policy: SourcePolicyGeneration) -> SourceSnapshot:
    return SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 10),
    )


def _observations(
    snapshot: SourceSnapshot,
    *,
    outcomes: dict[str, ObservationOutcome] | None = None,
) -> tuple[TerminalObservation, ...]:
    outcomes = outcomes or {}
    result = []
    for expected in snapshot.expected_endpoints:
        outcome = outcomes.get(
            expected.endpoint_id,
            ObservationOutcome.COMPLETE_ZERO,
        )
        result.append(
            TerminalObservation(
                source_id=expected.source_id,
                source_generation=expected.source_generation,
                endpoint_id=expected.endpoint_id,
                endpoint_generation=expected.endpoint_generation,
                window_start=snapshot.window_start,
                cutoff_at=snapshot.cutoff_at,
                scope=ObservationScope.PRODUCTION,
                outcome=outcome,
                item_count=(1 if outcome is ObservationOutcome.COMPLETE_WITH_ITEMS else 0),
            )
        )
    return tuple(result)


def _proof(policy: SourcePolicyGeneration) -> CoverageProof:
    snapshot = _snapshot(policy)
    observations = ObservationSet.create(snapshot, _observations(snapshot))
    return CoverageProof.create(policy, snapshot, observations)


def test_evaluator_public_seam_accepts_only_a_complete_proof() -> None:
    assert tuple(inspect.signature(CoverageEvaluator.evaluate).parameters) == (
        "self",
        "proof",
    )
    with pytest.raises(TypeError, match="CoverageProof"):
        CoverageEvaluator().evaluate(())  # type: ignore[arg-type]


def test_complete_four_lane_proof_reaches_coverage_a() -> None:
    result = CoverageEvaluator().evaluate(_proof(_policy(_baseline())))

    assert result.level is CoverageLevel.A
    assert result.disposition is CoverageDecision.PUBLISH
    assert result.weighted_coverage_bps == 10_000
    assert result.may_publish_credible_no_news is True
    assert all(count == 2 for _lane, count in result.healthy_groups)
    assert result.coverage_result_hash.startswith("sha256:")


def test_failure_observation_is_complete_but_reduces_health() -> None:
    policy = _policy(_baseline())
    snapshot = _snapshot(policy)
    failed = snapshot.expected_endpoints[0]
    observations = ObservationSet.create(
        snapshot,
        _observations(
            snapshot,
            outcomes={failed.endpoint_id: ObservationOutcome.FETCH_ERROR},
        ),
    )

    result = CoverageEvaluator().evaluate(CoverageProof.create(policy, snapshot, observations))

    assert result.level is CoverageLevel.B
    assert result.may_publish_verified_stories is True
    assert result.may_publish_credible_no_news is False
    assert result.weighted_coverage_bps == 8_750


def test_repeated_rows_in_one_origin_group_do_not_create_independence() -> None:
    baseline = _baseline(copies=5, include_disabled=False)
    policy = _policy(baseline)

    result = CoverageEvaluator().evaluate(_proof(policy))

    assert result.level is CoverageLevel.A
    assert all(count == 2 for _lane, count in result.healthy_groups)
    assert len(policy.active_entries_for(Workflow.AI)) == 40


def test_stable_source_identity_and_additive_multiset_preserve_duplicates() -> None:
    raw_hash = hashlib.sha256(b"same raw row").hexdigest()
    first = stable_source_id("private-rss", 0, raw_hash)
    duplicate = stable_source_id("private-rss", 1, raw_hash)

    assert first != duplicate
    assert first == stable_source_id("private-rss", 0, raw_hash)
    ensure_additive_multiset(
        Counter({"same": 2}),
        Counter({"same": 3, "new": 1}),
    )
    with pytest.raises(ValueError, match="would remove"):
        ensure_additive_multiset(
            Counter({"same": 2}),
            Counter({"same": 1}),
        )


@pytest.mark.parametrize(
    ("origin", "index", "raw_hash", "message"),
    [
        ("", 0, "a" * 64, "origin"),
        ("rss", -1, "a" * 64, "index"),
        ("rss", 0, "A" * 64, "lowercase SHA-256"),
    ],
)
def test_stable_source_identity_rejects_unsealed_inputs(
    origin: str,
    index: int,
    raw_hash: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        stable_source_id(origin, index, raw_hash)
