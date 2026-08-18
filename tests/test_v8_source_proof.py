from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, timedelta

import pytest

from pkm_workflow.provenance import (
    ApprovedSourceGeneration,
    CoverageProof,
    ObservationOutcome,
    ObservationScope,
    ObservationSet,
    PolicyState,
    RegistryClassificationRequired,
    SourcePolicyEntry,
    SourcePolicyGeneration,
    SourceSnapshot,
    TerminalObservation,
    Workflow,
    canonical_json_bytes,
)
from pkm_workflow.source_governance import (
    CoverageEvaluator,
    CoverageLevel,
    SourceLane,
)
from pkm_workflow.source_import import SourceBaseline, build_source_baseline


def _baseline(*, endpoint_suffix: str = "") -> SourceBaseline:
    feeds: list[dict[str, object]] = []
    for lane in SourceLane:
        for group_index in range(2):
            feeds.append(
                {
                    "name": f"{lane.value}-{group_index}",
                    "url": (f"https://example.com/{lane.value}/{group_index}{endpoint_suffix}"),
                }
            )
    feeds.extend(
        (
            {
                "name": "official duplicate",
                "url": f"https://example.com/official-labs/0{endpoint_suffix}",
            },
            {
                "name": "legacy disabled",
                "url": "https://example.com/disabled",
                "enabled": False,
            },
        )
    )
    document = {
        "rss_feeds": feeds,
        "ielts_resources": {"books": ["Synthetic IELTS book"]},
    }
    return build_source_baseline(
        (
            (
                "synthetic",
                document,
                hashlib.sha256(canonical_document(document)).hexdigest(),
            ),
        )
    )


def canonical_document(document: object) -> bytes:
    import json

    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _lane_for_endpoint(endpoint: str) -> SourceLane:
    return next(lane for lane in SourceLane if f"/{lane.value}/" in endpoint)


def _entries(
    baseline: SourceBaseline,
    *,
    weight_bps: object = 1_000,
) -> tuple[SourcePolicyEntry, ...]:
    entries: list[SourcePolicyEntry] = []
    endpoint_groups: dict[str, str] = {}
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
        elif record.collection.startswith("ielts_resources"):
            entries.append(
                SourcePolicyEntry(
                    source_id=record.source_id,
                    workflows=(Workflow.IELTS,),
                    lane=None,
                    origin_group="ielts-book",
                    weight_bps=weight_bps,
                    endpoint_generation="endpoint-gen-1",
                    evidence_role="practice",
                    state=PolicyState.ACTIVE,
                )
            )
        else:
            lane = _lane_for_endpoint(record.endpoint)
            group_index = record.endpoint.rstrip("/").rsplit("/", 1)[-1]
            origin_group = endpoint_groups.setdefault(
                record.endpoint,
                f"{lane.value}-group-{group_index}",
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
    return tuple(entries)


def _policy(
    baseline: SourceBaseline,
    entries: tuple[SourcePolicyEntry, ...] | None = None,
) -> SourcePolicyGeneration:
    return SourcePolicyGeneration.create(
        baseline,
        entries=_entries(baseline) if entries is None else entries,
        approval_generation="approval-1",
    )


def _snapshot(policy: SourcePolicyGeneration) -> SourceSnapshot:
    return SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 10),
    )


def _observation(
    expected: object,
    snapshot: SourceSnapshot,
    **changes: object,
) -> TerminalObservation:
    values: dict[str, object] = {
        "source_id": expected.source_id,
        "source_generation": expected.source_generation,
        "endpoint_id": expected.endpoint_id,
        "endpoint_generation": expected.endpoint_generation,
        "window_start": snapshot.window_start,
        "cutoff_at": snapshot.cutoff_at,
        "scope": ObservationScope.PRODUCTION,
        "outcome": ObservationOutcome.COMPLETE_ZERO,
        "item_count": 0,
    }
    values.update(changes)
    return TerminalObservation(**values)


def _observations(snapshot: SourceSnapshot) -> tuple[TerminalObservation, ...]:
    return tuple(_observation(expected, snapshot) for expected in snapshot.expected_endpoints)


def _proof(policy: SourcePolicyGeneration) -> CoverageProof:
    snapshot = _snapshot(policy)
    return CoverageProof.create(
        policy,
        snapshot,
        ObservationSet.create(snapshot, _observations(snapshot)),
    )


def test_unclassified_baseline_cannot_create_policy_or_coverage() -> None:
    baseline = _baseline()
    with pytest.raises(
        RegistryClassificationRequired,
        match="REGISTRY_CLASSIFICATION_REQUIRED",
    ):
        SourcePolicyGeneration.create(
            baseline,
            entries=None,
            approval_generation="approval-1",
        )


@pytest.mark.parametrize("mutation", ("missing", "unknown", "duplicate"))
def test_policy_must_cover_every_baseline_source_exactly_once(
    mutation: str,
) -> None:
    baseline = _baseline()
    entries = list(_entries(baseline))
    if mutation == "missing":
        entries.pop()
    elif mutation == "unknown":
        entries[-1] = replace(
            entries[-1],
            source_id="source-" + "f" * 32,
        )
    else:
        entries[-1] = entries[0]

    with pytest.raises(ValueError, match="policy source IDs"):
        _policy(baseline, tuple(entries))


def test_enabled_sources_require_explicit_workflow_and_ai_lane() -> None:
    baseline = _baseline()
    entry = next(item for item in _entries(baseline) if item.lane is not None)

    with pytest.raises(ValueError, match="at least one workflow"):
        replace(entry, workflows=())
    with pytest.raises(ValueError, match="four-lane"):
        replace(entry, lane=None)


def test_disabled_record_is_preserved_but_never_expected_or_counted() -> None:
    baseline = _baseline()
    entries = list(_entries(baseline))
    disabled_index = next(
        index for index, record in enumerate(baseline.records) if not record.enabled
    )
    entries[disabled_index] = replace(
        entries[disabled_index],
        weight_bps=10_000,
    )
    policy = _policy(baseline, tuple(entries))
    snapshot = _snapshot(policy)
    disabled = baseline.records[disabled_index]

    assert policy.entry_for(disabled.source_id).state is PolicyState.LEGACY_DISABLED_PRESERVED
    assert disabled.source_id in snapshot.preserved_disabled_source_ids
    assert disabled.source_id not in {entry.source_id for entry in snapshot.expected_endpoints}
    result = CoverageEvaluator().evaluate(_proof(policy))
    assert result.level is CoverageLevel.A
    assert result.weighted_coverage_bps == 10_000


def test_duplicate_canonical_endpoint_cannot_claim_different_groups() -> None:
    baseline = _baseline()
    entries = list(_entries(baseline))
    duplicate_endpoint = "https://example.com/official-labs/0"
    indices = [
        index
        for index, record in enumerate(baseline.records)
        if record.endpoint == duplicate_endpoint
    ]
    assert len(indices) == 2
    entries[indices[1]] = replace(
        entries[indices[1]],
        origin_group="fake-independent-group",
    )

    with pytest.raises(ValueError, match="canonical endpoint.*origin group"):
        _policy(baseline, tuple(entries))


def test_duplicate_canonical_endpoint_cannot_be_copied_across_lanes() -> None:
    baseline = _baseline()
    entries = list(_entries(baseline))
    duplicate_endpoint = "https://example.com/official-labs/0"
    indices = [
        index
        for index, record in enumerate(baseline.records)
        if record.endpoint == duplicate_endpoint
    ]
    entries[indices[1]] = replace(
        entries[indices[1]],
        lane=SourceLane.RESEARCH,
    )

    with pytest.raises(ValueError, match="canonical endpoint.*lane"):
        _policy(baseline, tuple(entries))


def test_disabled_duplicate_keeps_group_without_becoming_an_active_lane() -> None:
    document = {
        "rss_feeds": [
            {"url": "https://example.com/official-labs/0"},
            {
                "url": "https://example.com/official-labs/0",
                "enabled": False,
            },
        ]
    }
    baseline = build_source_baseline(
        (
            (
                "disabled-duplicate",
                document,
                hashlib.sha256(canonical_document(document)).hexdigest(),
            ),
        )
    )
    entries = (
        SourcePolicyEntry(
            source_id=baseline.records[0].source_id,
            workflows=(Workflow.AI,),
            lane=SourceLane.OFFICIAL_LABS,
            origin_group="same-origin",
            weight_bps=1_000,
            endpoint_generation="endpoint-gen-1",
            evidence_role="primary",
            state=PolicyState.ACTIVE,
        ),
        SourcePolicyEntry(
            source_id=baseline.records[1].source_id,
            workflows=(),
            lane=None,
            origin_group="same-origin",
            weight_bps=1_000,
            endpoint_generation="endpoint-gen-1",
            evidence_role="legacy-preserved",
            state=PolicyState.LEGACY_DISABLED_PRESERVED,
        ),
    )

    policy = _policy(baseline, entries)
    assert len(policy.active_entries_for(Workflow.AI)) == 1


def test_url_normalization_cannot_turn_one_endpoint_into_two_origins() -> None:
    document = {
        "rss_feeds": [
            {"url": "https://EXAMPLE.com:443/official-labs/0#first"},
            {"url": "https://example.com/official-labs/0"},
        ]
    }
    baseline = build_source_baseline(
        (
            (
                "canonical-duplicate",
                document,
                hashlib.sha256(canonical_document(document)).hexdigest(),
            ),
        )
    )
    entries = tuple(
        SourcePolicyEntry(
            source_id=record.source_id,
            workflows=(Workflow.AI,),
            lane=SourceLane.OFFICIAL_LABS,
            origin_group=f"claimed-group-{index}",
            weight_bps=1_000,
            endpoint_generation="endpoint-gen-1",
            evidence_role="primary",
            state=PolicyState.ACTIVE,
        )
        for index, record in enumerate(baseline.records)
    )

    with pytest.raises(ValueError, match="canonical endpoint.*origin group"):
        _policy(baseline, entries)


def test_approved_append_generation_is_part_of_registry_and_policy_hashes() -> None:
    baseline = _baseline()
    append_document = {
        "rss_feeds": [
            {"url": "https://append.example/official-labs/2"},
        ]
    }
    append_baseline = build_source_baseline(
        (
            (
                "approved-append",
                append_document,
                hashlib.sha256(canonical_document(append_document)).hexdigest(),
            ),
        )
    )
    approved = ApprovedSourceGeneration.create(
        generation_id="source-append-gen-1",
        approval_generation="append-approval-1",
        records=append_baseline.records,
    )
    append_entry = SourcePolicyEntry(
        source_id=append_baseline.records[0].source_id,
        workflows=(Workflow.AI,),
        lane=SourceLane.OFFICIAL_LABS,
        origin_group="approved-append-origin",
        weight_bps=1_000,
        endpoint_generation="endpoint-gen-1",
        evidence_role="primary",
        state=PolicyState.ACTIVE,
    )
    entries = (*_entries(baseline), append_entry)

    with pytest.raises(ValueError, match="policy source IDs"):
        _policy(baseline, entries)

    policy = SourcePolicyGeneration.create(
        baseline,
        entries=entries,
        approval_generation="approval-1",
        approved_appends=(approved,),
    )
    changed_approval = ApprovedSourceGeneration.create(
        generation_id="source-append-gen-1",
        approval_generation="append-approval-2",
        records=append_baseline.records,
    )
    changed_policy = SourcePolicyGeneration.create(
        baseline,
        entries=entries,
        approval_generation="approval-1",
        approved_appends=(changed_approval,),
    )

    snapshot = _snapshot(policy)
    assert append_entry.source_id in {
        expected.source_id for expected in snapshot.expected_endpoints
    }
    assert changed_policy.source_registry_hash != policy.source_registry_hash
    assert changed_policy.source_policy_hash != policy.source_policy_hash

    tampered_record = replace(
        append_baseline.records[0],
        source_id="source-" + "f" * 32,
    )
    with pytest.raises(ValueError, match="identity"):
        ApprovedSourceGeneration.create(
            generation_id="source-append-gen-tampered",
            approval_generation="append-approval-1",
            records=(tampered_record,),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "extra",
        "duplicate",
        "source_generation",
        "endpoint_generation",
        "window",
        "probe",
    ),
)
def test_observation_set_is_an_exact_terminal_set(mutation: str) -> None:
    snapshot = _snapshot(_policy(_baseline()))
    observations = list(_observations(snapshot))
    if mutation == "missing":
        observations.pop()
    elif mutation == "extra":
        observations.append(
            replace(
                observations[0],
                endpoint_id="endpoint-" + "f" * 32,
            )
        )
    elif mutation == "duplicate":
        observations.append(observations[0])
    elif mutation == "source_generation":
        observations[0] = replace(
            observations[0],
            source_generation="source-generation-other",
        )
    elif mutation == "endpoint_generation":
        observations[0] = replace(
            observations[0],
            endpoint_generation="endpoint-gen-other",
        )
    elif mutation == "window":
        observations[0] = replace(
            observations[0],
            window_start=(observations[0].window_start + timedelta(minutes=1)),
        )
    else:
        observations[0] = replace(
            observations[0],
            scope=ObservationScope.PROBE,
        )

    with pytest.raises(ValueError):
        ObservationSet.create(snapshot, tuple(observations))


@pytest.mark.parametrize(
    ("outcome", "healthy"),
    (
        (ObservationOutcome.COMPLETE_ZERO, True),
        (ObservationOutcome.COMPLETE_WITH_ITEMS, True),
        (ObservationOutcome.FETCH_ERROR, False),
        (ObservationOutcome.PARSE_ERROR, False),
    ),
)
def test_every_terminal_outcome_completes_proof_but_only_success_is_healthy(
    outcome: ObservationOutcome,
    healthy: bool,
) -> None:
    policy = _policy(_baseline())
    snapshot = _snapshot(policy)
    observations = list(_observations(snapshot))
    failure_index = next(
        index
        for index, expected in enumerate(snapshot.expected_endpoints)
        if expected.lane is SourceLane.RESEARCH
    )
    observations[failure_index] = replace(
        observations[failure_index],
        outcome=outcome,
        item_count=(1 if outcome is ObservationOutcome.COMPLETE_WITH_ITEMS else 0),
    )

    proof = CoverageProof.create(
        policy,
        snapshot,
        ObservationSet.create(snapshot, tuple(observations)),
    )
    result = CoverageEvaluator().evaluate(proof)

    if healthy:
        assert result.level is CoverageLevel.A
        assert result.weighted_coverage_bps == 10_000
    else:
        assert result.level is CoverageLevel.B
        assert result.weighted_coverage_bps == 8_750


@pytest.mark.parametrize(
    "invalid_weight",
    (
        True,
        False,
        0,
        -1,
        10_001,
        1.0,
        float("nan"),
        float("inf"),
        -float("inf"),
    ),
)
def test_canonical_weights_are_strict_integer_basis_points(
    invalid_weight: object,
) -> None:
    baseline = _baseline()
    with pytest.raises(
        (TypeError, ValueError),
        match="weight_bps",
    ):
        _entries(baseline, weight_bps=invalid_weight)


def test_policy_hash_is_order_invariant_and_field_sensitive() -> None:
    baseline = _baseline()
    entries = _entries(baseline)
    policy = _policy(baseline, entries)
    reordered = _policy(baseline, tuple(reversed(entries)))

    assert reordered.source_registry_hash == policy.source_registry_hash
    assert reordered.source_policy_hash == policy.source_policy_hash

    changed_weight = _policy(
        baseline,
        (replace(entries[0], weight_bps=1_001), *entries[1:]),
    )
    changed_endpoint_generation = _policy(
        baseline,
        (
            replace(
                entries[0],
                endpoint_generation="endpoint-gen-2",
            ),
            *entries[1:],
        ),
    )
    assert changed_weight.source_registry_hash == policy.source_registry_hash
    assert changed_weight.source_policy_hash != policy.source_policy_hash
    assert changed_endpoint_generation.source_policy_hash != policy.source_policy_hash


def test_source_window_observation_and_result_mutations_change_hash_chain() -> None:
    policy = _policy(_baseline())
    changed_source_policy = _policy(_baseline(endpoint_suffix="?v=2"))
    assert changed_source_policy.source_registry_hash != policy.source_registry_hash

    snapshot = _snapshot(policy)
    changed_window = SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 11),
    )
    assert changed_window.source_snapshot_hash != snapshot.source_snapshot_hash

    observations = _observations(snapshot)
    observation_set = ObservationSet.create(snapshot, observations)
    reordered_set = ObservationSet.create(
        snapshot,
        tuple(reversed(observations)),
    )
    assert reordered_set.observation_set_hash == observation_set.observation_set_hash

    changed_outcome = replace(
        observations[0],
        outcome=ObservationOutcome.FETCH_ERROR,
    )
    changed_set = ObservationSet.create(
        snapshot,
        (changed_outcome, *observations[1:]),
    )
    assert changed_set.observation_set_hash != observation_set.observation_set_hash

    result = CoverageEvaluator().evaluate(CoverageProof.create(policy, snapshot, observation_set))
    changed_result = CoverageEvaluator().evaluate(
        CoverageProof.create(policy, snapshot, changed_set)
    )
    assert changed_result.coverage_result_hash != result.coverage_result_hash


def test_proof_and_result_echo_the_continuous_hash_chain() -> None:
    proof = _proof(_policy(_baseline()))
    result = CoverageEvaluator().evaluate(proof)

    assert proof.source_registry_hash == result.source_registry_hash
    assert proof.source_policy_hash == result.source_policy_hash
    assert proof.source_snapshot_hash == result.source_snapshot_hash
    assert proof.observation_set_hash == result.observation_set_hash
    assert proof.coverage_proof_hash == result.coverage_proof_hash
    assert proof.coverage_proof_hash.startswith("sha256:")
    assert result.coverage_result_hash.startswith("sha256:")


def test_removing_expected_policy_or_observation_input_fails_closed() -> None:
    baseline = _baseline()
    entries = _entries(baseline)
    policy = _policy(baseline)
    snapshot = _snapshot(policy)

    with pytest.raises(ValueError, match="policy source IDs"):
        _policy(baseline, entries[:-1])
    with pytest.raises(ValueError, match="exactly match"):
        ObservationSet.create(snapshot, _observations(snapshot)[:-1])


def test_directly_forged_incomplete_observation_set_is_rejected_by_proof() -> None:
    policy = _policy(_baseline())
    snapshot = _snapshot(policy)
    complete = ObservationSet.create(snapshot, _observations(snapshot))
    missing = complete.observations[:-1]
    forged_payload = {
        "source_snapshot_hash": snapshot.source_snapshot_hash,
        "observations": [observation.payload() for observation in missing],
    }
    forged = replace(
        complete,
        observations=missing,
        observation_set_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(forged_payload)).hexdigest()
        ),
    )

    with pytest.raises(ValueError):
        CoverageProof.create(policy, snapshot, forged)


def test_rehashed_snapshot_source_omission_is_rejected_by_policy_projection() -> None:
    policy = _policy(_baseline())
    snapshot = _snapshot(policy)
    missing_snapshot = replace(
        snapshot,
        expected_endpoints=snapshot.expected_endpoints[:-1],
    )
    missing_snapshot = replace(
        missing_snapshot,
        source_snapshot_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(missing_snapshot.payload())).hexdigest()
        ),
    )
    observations = ObservationSet.create(
        missing_snapshot,
        _observations(missing_snapshot),
    )

    with pytest.raises(ValueError, match="policy projection"):
        CoverageProof.create(
            policy,
            missing_snapshot,
            observations,
        )


def test_rehashed_policy_registry_omission_is_rejected_by_baseline() -> None:
    policy = _policy(_baseline())
    missing_registry = policy.registry_sources[:-1]
    missing_source_id = policy.registry_sources[-1].source_id
    missing_entries = tuple(
        entry for entry in policy.entries if entry.source_id != missing_source_id
    )
    registry_payload = {
        "baseline_id": policy.baseline_id,
        "approved_append_generations": [],
        "sources": [record.payload() for record in missing_registry],
    }
    registry_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(registry_payload)).hexdigest()
    policy_payload = {
        "baseline_id": policy.baseline_id,
        "approval_generation": policy.approval_generation,
        "source_registry_hash": registry_hash,
        "entries": [entry.payload() for entry in missing_entries],
    }
    forged = replace(
        policy,
        registry_sources=missing_registry,
        entries=missing_entries,
        source_registry_hash=registry_hash,
        source_policy_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(policy_payload)).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="baseline"):
        SourceSnapshot.create(
            forged,
            workflow=Workflow.AI,
            content_date=date(2026, 8, 10),
        )
