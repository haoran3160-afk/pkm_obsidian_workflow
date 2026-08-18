from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pkm_workflow.curation import (
    CurationDisposition,
    CurationEngine,
    CurationPolicyGeneration,
    UserContextContract,
)
from pkm_workflow.evidence import EvidencePackage, EvidenceParser
from pkm_workflow.fetch_service import (
    FetchBatch,
    FetchLimits,
    FetchService,
    WireRequest,
    WireResponse,
)
from pkm_workflow.provenance import (
    CoverageProof,
    ObservationOutcome,
    ObservationSet,
    PolicyState,
    SourceLane,
    SourcePolicyEntry,
    SourcePolicyGeneration,
    SourceSnapshot,
    Workflow,
    canonical_json_bytes,
)
from pkm_workflow.source_governance import CoverageEvaluator, CoverageResult
from pkm_workflow.source_import import build_source_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "v8_curation_manifest.json"


class PublicResolver:
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class FeedTransport:
    def __init__(self, responses: dict[str, tuple[int, bytes, str | None]]) -> None:
        self._responses = responses

    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse:
        status, body, content_type = self._responses[request.url]
        return WireResponse(
            status=status,
            headers=(("Content-Type", content_type),) if content_type is not None else (),
            body=body,
            peer_ip=request.connect_ip,
        )


def _manifest() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _feed(items: list[tuple[str, str, str, str | None]]) -> bytes:
    entries = []
    for title, item_url, content, published_at in items:
        published = f"<pubDate>{published_at}</pubDate>" if published_at is not None else ""
        entries.append(
            "<item>"
            f"<guid>{item_url}</guid><link>{item_url}</link><title>{title}</title>"
            f"<description>{content}</description>{published}</item>"
        )
    return (
        "<rss version='2.0'><channel><title>fixture</title>" + "".join(entries) + "</channel></rss>"
    ).encode()


def _atom(
    *,
    title: str,
    item_url: str,
    published_at: str,
    updated_at: str,
) -> bytes:
    return (
        "<feed xmlns='http://www.w3.org/2005/Atom'><title>fixture</title>"
        f"<id>{item_url}/feed</id><updated>{updated_at}</updated><entry>"
        f"<id>{item_url}</id><link href='{item_url}'/><title>{title}</title>"
        f"<summary>Agent evidence for {title}</summary>"
        f"<published>{published_at}</published><updated>{updated_at}</updated>"
        "</entry></feed>"
    ).encode()


def build_valid_closure(
    *,
    title_overrides: dict[tuple[SourceLane, int], str] | None = None,
    published_overrides: dict[tuple[SourceLane, int], str | None] | None = None,
    feed_overrides: dict[tuple[SourceLane, int], tuple[bytes, str]] | None = None,
    failed_endpoints: set[tuple[SourceLane, int]] | None = None,
    extra_item_count: int = 0,
) -> tuple[
    SourcePolicyGeneration,
    SourceSnapshot,
    FetchBatch,
    EvidencePackage,
    CoverageProof,
    CoverageResult,
    bytes,
    CurationPolicyGeneration,
]:
    endpoints: list[dict[str, str]] = []
    responses: dict[str, tuple[int, bytes, str | None]] = {}
    for lane in SourceLane:
        for index in range(2):
            endpoint = f"https://feed.example.test/{lane.value}/{index}"
            item_url = f"https://news.example.test/{lane.value}/{index}"
            endpoints.append({"name": f"{lane.value}-{index}", "url": endpoint})
            title = (title_overrides or {}).get(
                (lane, index),
                f"Agent {lane.value} release number {index}",
            )
            items = [
                (
                    title,
                    item_url,
                    f"{_manifest()['feed']['content']} {lane.value} {index}",
                    (published_overrides or {}).get(
                        (lane, index),
                        str(_manifest()["feed"]["published_at"]),
                    ),
                )
            ]
            if lane is SourceLane.OFFICIAL_LABS and index == 0:
                items.extend(
                    (
                        f"Overflow candidate distinct topic {extra_index}",
                        f"https://news.example.test/overflow/{extra_index}",
                        f"Agent evidence unique payload {extra_index}",
                        str(_manifest()["feed"]["published_at"]),
                    )
                    for extra_index in range(extra_item_count)
                )
            responses[endpoint] = (200, _feed(items), "application/rss+xml")
            if (feed_overrides or {}).get((lane, index)) is not None:
                body, mime = (feed_overrides or {})[(lane, index)]
                responses[endpoint] = (200, body, mime)
            if (lane, index) in (failed_endpoints or set()):
                responses[endpoint] = (503, b"", None)
    document = {"rss_feeds": endpoints}
    baseline = build_source_baseline(
        (
            (
                "synthetic-curation",
                document,
                hashlib.sha256(canonical_json_bytes(document)).hexdigest(),
            ),
        )
    )
    entries = []
    for record in baseline.records:
        lane = next(lane for lane in SourceLane if f"/{lane.value}/" in record.endpoint)
        group = record.endpoint.rsplit("/", 1)[-1]
        entries.append(
            SourcePolicyEntry(
                source_id=record.source_id,
                workflows=(Workflow.AI,),
                lane=lane,
                origin_group=f"{lane.value}-group-{group}",
                weight_bps=1_000,
                endpoint_generation="endpoint-gen-1",
                evidence_role="primary",
                state=PolicyState.ACTIVE,
            )
        )
    policy = SourcePolicyGeneration.create(
        baseline,
        entries=tuple(entries),
        approval_generation="approval-1",
    )
    snapshot = SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 18),
    )
    batch = FetchService(PublicResolver(), FeedTransport(responses), FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 13, tzinfo=timezone.utc),
    )
    package = EvidenceParser().parse(policy, snapshot, batch)
    proof = CoverageProof.create(policy, snapshot, package.observation_set)
    coverage = CoverageEvaluator().evaluate(proof)
    manifest = _manifest()
    user_context = json.dumps(
        manifest["user_context"],
        ensure_ascii=False,
    ).encode()
    curation_policy = CurationPolicyGeneration.create_synthetic(
        expected_user_context_hash=str(manifest["expected_user_context_hash"]),
        evidence_role_order=("primary",),
        generator_model="gpt-5.6-terra",
        reviewer_model="gpt-5.6-sol",
        prompt_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        repair_budget=1,
    )
    return (
        policy,
        snapshot,
        batch,
        package,
        proof,
        coverage,
        user_context,
        curation_policy,
    )


def curate_closure(closure):
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = closure
    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )
    return result


def test_complete_production_closure_creates_stable_model_candidates() -> None:
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure()
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    assert result.disposition is CurationDisposition.READY
    assert tuple(candidate.cluster_hash for candidate in result.model_candidates) == tuple(
        cluster.cluster_hash for cluster in result.audit_clusters
    )
    assert {item.evidence_id for cluster in result.audit_clusters for item in cluster.evidence} == {
        item.evidence_id for item in package.items
    }
    assert all(
        cluster.relevance_reason == "PROFILE_TOPIC_MATCH" for cluster in result.audit_clusters
    )
    assert all(
        cluster.profile_fields == ("priorities[0].topics[0]", "priorities[0].topics[1]")
        for cluster in result.audit_clusters
    )
    assert result.curation_result_hash.startswith("sha256:")


def test_claimed_coverage_is_recomputed_not_trusted() -> None:
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure()
    )
    forged = replace(coverage, weighted_coverage_bps=coverage.weighted_coverage_bps - 1)

    with pytest.raises(ValueError, match="coverage result"):
        CurationEngine().curate(
            source_policy=policy,
            snapshot=snapshot,
            fetch_batch=batch,
            evidence_package=package,
            coverage_proof=proof,
            claimed_coverage=forged,
            user_context_json=context,
            curation_policy=curation_policy,
        )


def test_internally_valid_coverage_proof_from_another_observation_set_is_rejected() -> None:
    policy, snapshot, batch, package, _proof, coverage, context, curation_policy = (
        build_valid_closure()
    )
    changed_observation = replace(
        package.observation_set.observations[0],
        outcome=ObservationOutcome.COMPLETE_ZERO,
        item_count=0,
    )
    other_observations = ObservationSet.create(
        snapshot,
        (changed_observation, *package.observation_set.observations[1:]),
    )
    other_proof = CoverageProof.create(policy, snapshot, other_observations)
    other_coverage = CoverageEvaluator().evaluate(other_proof)

    with pytest.raises(ValueError, match="does not match the evidence closure"):
        CurationEngine().curate(
            source_policy=policy,
            snapshot=snapshot,
            fetch_batch=batch,
            evidence_package=package,
            coverage_proof=other_proof,
            claimed_coverage=other_coverage,
            user_context_json=context,
            curation_policy=curation_policy,
        )


def test_cross_origin_duplicate_titles_form_one_lossless_cluster() -> None:
    shared_title = "Agent framework releases deterministic eval harness"
    overrides = {
        (SourceLane.OFFICIAL_LABS, 0): shared_title,
        (SourceLane.RESEARCH, 0): shared_title,
    }
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure(title_overrides=overrides)
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    matching = [
        cluster
        for cluster in result.audit_clusters
        if {item.title for item in cluster.evidence} == {shared_title}
    ]
    assert len(matching) == 1
    assert len(matching[0].evidence) == 2
    assert len({item.origin_group for item in matching[0].evidence}) == 2


def test_similarity_window_uses_selected_event_time_not_out_of_window_update() -> None:
    left_title = "Agent framework release improves coding evaluation reliability"
    right_title = left_title + " today"
    item_url = "https://news.example.test/official-labs/atom"
    overrides = {
        (SourceLane.OFFICIAL_LABS, 0): (
            _atom(
                title=left_title,
                item_url=item_url,
                published_at="2026-08-17T12:00:00Z",
                updated_at="2026-08-01T00:00:00Z",
            ),
            "application/atom+xml",
        )
    }
    titles = {(SourceLane.OFFICIAL_LABS, 1): right_title}
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure(title_overrides=titles, feed_overrides=overrides)
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    cluster = next(
        cluster
        for cluster in result.audit_clusters
        if {item.title for item in cluster.evidence} == {left_title, right_title}
    )
    assert len(cluster.evidence) == 2


def test_every_item_is_accounted_for_as_cluster_or_time_exclusion() -> None:
    missing = (SourceLane.OFFICIAL_LABS, 0)
    outside = (SourceLane.RESEARCH, 0)
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure(
            published_overrides={
                missing: None,
                outside: "Sat, 01 Aug 2026 12:00:00 GMT",
            }
        )
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    clustered = {item.evidence_id for cluster in result.audit_clusters for item in cluster.evidence}
    excluded = {exclusion.evidence_id for exclusion in result.exclusions}
    assert clustered.isdisjoint(excluded)
    assert clustered | excluded == {item.evidence_id for item in package.items}
    assert {exclusion.reason.value for exclusion in result.exclusions} == {
        "MISSING_EVENT_TIME",
        "EVENT_TIME_OUTSIDE_WINDOW",
    }


def test_twenty_five_clusters_block_without_truncating_audit_data() -> None:
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure(extra_item_count=17)
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    assert result.disposition is CurationDisposition.POLICY_OVERFLOW
    assert result.model_candidates == ()
    assert len(result.audit_clusters) == 25
    assert sum(len(cluster.evidence) for cluster in result.audit_clusters) == 25


def test_similarity_work_budget_blocks_adversarial_feed_without_losing_evidence() -> None:
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure(extra_item_count=392)
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    assert result.disposition is CurationDisposition.POLICY_OVERFLOW
    assert result.model_candidates == ()
    assert result.similarity_budget_exhausted is True
    assert result.similarity_comparisons == result.similarity_comparison_budget
    assert sum(len(cluster.evidence) for cluster in result.audit_clusters) == len(package.items)


def test_coverage_insufficient_never_exposes_healthy_partial_candidates() -> None:
    failures = {
        (SourceLane.OFFICIAL_LABS, 0),
        (SourceLane.OFFICIAL_LABS, 1),
    }
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure(failed_endpoints=failures)
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )

    assert result.disposition is CurationDisposition.COVERAGE_INSUFFICIENT
    assert result.model_candidates == ()
    assert result.audit_clusters


def test_model_evidence_omits_locator_and_user_context_must_match_policy() -> None:
    policy, snapshot, batch, package, proof, coverage, context, curation_policy = (
        build_valid_closure()
    )
    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context,
        curation_policy=curation_policy,
    )
    model_payload = result.model_candidates[0].evidence[0].payload()
    assert all(
        token not in key.casefold() for key in model_payload for token in ("url", "uri", "locator")
    )
    assert not hasattr(result.model_candidates[0], "locators")
    assert all(
        locator.status == "UNVERIFIED_LOCATOR"
        for cluster in result.audit_clusters
        for locator in cluster.locators
    )

    changed = json.loads(context)
    changed["projects"] = ["Different project"]
    with pytest.raises(ValueError, match="UserContext hash"):
        CurationEngine().curate(
            source_policy=policy,
            snapshot=snapshot,
            fetch_batch=batch,
            evidence_package=package,
            coverage_proof=proof,
            claimed_coverage=coverage,
            user_context_json=json.dumps(changed).encode(),
            curation_policy=curation_policy,
        )


def test_user_context_schema_rejects_malformed_or_unsafe_contracts() -> None:
    manifest = _manifest()
    valid = manifest["user_context"]
    expected_hash = str(manifest["expected_user_context_hash"])
    with pytest.raises(TypeError, match="must be bytes"):
        UserContextContract.parse("{}", expected_hash=expected_hash)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UTF-8 JSON"):
        UserContextContract.parse(b"{", expected_hash=expected_hash)
    with pytest.raises(ValueError, match="canonical SHA-256"):
        UserContextContract.parse(b"{}", expected_hash="bad")

    mutations: tuple[tuple[object, str], ...] = (
        ({**valid, "extra": True}, "approved schema"),
        ({**valid, "vault_inference_allowed": True}, "prohibit Vault"),
        ({**valid, "style": {"language": "zh-CN"}}, "style fields"),
        (
            {
                **valid,
                "style": {**valid["style"], "evidence_first": "yes"},
            },
            "style flags",
        ),
        ({**valid, "projects": []}, "non-empty list"),
        ({**valid, "projects": ["same", "same"]}, "duplicates"),
        ({**valid, "priorities": []}, "priorities must"),
        (
            {
                **valid,
                "priorities": [{"rank": 1, "topics": ["Agent"], "extra": True}],
            },
            "priority fields",
        ),
        (
            {**valid, "priorities": [{"rank": 0, "topics": ["Agent"]}]},
            "positive integer",
        ),
        (
            {
                **valid,
                "priorities": [
                    {"rank": 2, "topics": ["model"]},
                    {"rank": 1, "topics": ["Agent"]},
                ],
            },
            "unique and ascending",
        ),
    )
    for document, message in mutations:
        with pytest.raises(ValueError, match=message):
            UserContextContract.parse(
                json.dumps(document).encode(),
                expected_hash=expected_hash,
            )


def test_policy_rejects_invalid_generation_and_unknown_active_role() -> None:
    closure = build_valid_closure()
    policy, snapshot, batch, package, proof, coverage, context, valid_policy = closure
    invalid_arguments = (
        ({"prompt_hash": "bad"}, "canonical SHA-256"),
        ({"evidence_role_order": ()}, "ordering must be complete"),
        ({"evidence_role_order": ("primary", "primary")}, "must not contain duplicates"),
        ({"repair_budget": 2}, "zero or one"),
        ({"generator_model": ""}, "non-empty text"),
    )
    base = {
        "expected_user_context_hash": valid_policy.expected_user_context_hash,
        "evidence_role_order": valid_policy.evidence_role_order,
        "generator_model": valid_policy.generator_model,
        "reviewer_model": valid_policy.reviewer_model,
        "prompt_hash": valid_policy.prompt_hash,
        "schema_hash": valid_policy.schema_hash,
        "repair_budget": valid_policy.repair_budget,
    }
    for changes, message in invalid_arguments:
        with pytest.raises(ValueError, match=message):
            CurationPolicyGeneration.create_synthetic(**(base | changes))
    with pytest.raises(ValueError, match="generation is invalid"):
        replace(valid_policy, candidate_cap=23).validate()

    wrong_roles = CurationPolicyGeneration.create_synthetic(
        **(base | {"evidence_role_order": ("secondary",)})
    )
    with pytest.raises(ValueError, match="evidence role"):
        CurationEngine().curate(
            source_policy=policy,
            snapshot=snapshot,
            fetch_batch=batch,
            evidence_package=package,
            coverage_proof=proof,
            claimed_coverage=coverage,
            user_context_json=context,
            curation_policy=wrong_roles,
        )


def test_cjk_similarity_clusters_losslessly_and_no_profile_match_gets_no_boost() -> None:
    left = "智能体评测框架正式发布"
    right = left + "版"
    closure = build_valid_closure(
        title_overrides={
            (SourceLane.OFFICIAL_LABS, 0): left,
            (SourceLane.RESEARCH, 0): right,
        }
    )
    policy, snapshot, batch, package, proof, coverage, context, old_policy = closure
    context_object = deepcopy(json.loads(context))
    context_object["priorities"] = [{"rank": 1, "topics": ["quantum"]}]
    context_bytes = json.dumps(context_object, ensure_ascii=False).encode()
    context_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(context_object)).hexdigest()
    curation_policy = CurationPolicyGeneration.create_synthetic(
        expected_user_context_hash=context_hash,
        evidence_role_order=old_policy.evidence_role_order,
        generator_model=old_policy.generator_model,
        reviewer_model=old_policy.reviewer_model,
        prompt_hash=old_policy.prompt_hash,
        schema_hash=old_policy.schema_hash,
        repair_budget=old_policy.repair_budget,
    )

    result = CurationEngine().curate(
        source_policy=policy,
        snapshot=snapshot,
        fetch_batch=batch,
        evidence_package=package,
        coverage_proof=proof,
        claimed_coverage=coverage,
        user_context_json=context_bytes,
        curation_policy=curation_policy,
    )

    matching = next(
        cluster
        for cluster in result.audit_clusters
        if {item.title for item in cluster.evidence} == {left, right}
    )
    assert matching.relevance_reason == "GENERAL_POSSIBLE_IMPACT"
    assert matching.best_profile_rank is None
    assert matching.profile_fields == ()
