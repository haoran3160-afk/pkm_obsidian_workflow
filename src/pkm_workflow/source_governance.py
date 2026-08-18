"""Coverage evaluation over a complete, hash-bound source proof."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .provenance import (
    CoverageProof,
    ObservationOutcome,
    SourceLane,
    canonical_json_bytes,
)
from .source_import import stable_source_id as _stable_source_id


class CoverageLevel(str, Enum):
    A = "A"
    B = "B"
    INSUFFICIENT = "INSUFFICIENT"


class CoverageDecision(str, Enum):
    PUBLISH = "PUBLISH"
    RETRYABLE = "RETRYABLE"


@dataclass(frozen=True)
class CoverageResult:
    level: CoverageLevel
    disposition: CoverageDecision
    weighted_coverage_bps: int
    healthy_groups: tuple[tuple[SourceLane, int], ...]
    gaps: tuple[str, ...]
    reason_code: str | None
    may_publish_verified_stories: bool
    may_publish_credible_no_news: bool
    source_registry_hash: str
    source_policy_hash: str
    source_snapshot_hash: str
    observation_set_hash: str
    coverage_proof_hash: str
    coverage_result_hash: str

    @classmethod
    def create(
        cls,
        *,
        level: CoverageLevel,
        disposition: CoverageDecision,
        weighted_coverage_bps: int,
        healthy_groups: tuple[tuple[SourceLane, int], ...],
        gaps: tuple[str, ...],
        reason_code: str | None,
        may_publish_verified_stories: bool,
        may_publish_credible_no_news: bool,
        proof: CoverageProof,
    ) -> CoverageResult:
        payload = {
            "level": level.value,
            "disposition": disposition.value,
            "weighted_coverage_bps": weighted_coverage_bps,
            "healthy_groups": [
                {"lane": lane.value, "count": count} for lane, count in healthy_groups
            ],
            "gaps": list(gaps),
            "reason_code": reason_code,
            "may_publish_verified_stories": may_publish_verified_stories,
            "may_publish_credible_no_news": may_publish_credible_no_news,
            "source_registry_hash": proof.source_registry_hash,
            "source_policy_hash": proof.source_policy_hash,
            "source_snapshot_hash": proof.source_snapshot_hash,
            "observation_set_hash": proof.observation_set_hash,
            "coverage_proof_hash": proof.coverage_proof_hash,
        }
        result_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls(
            level=level,
            disposition=disposition,
            weighted_coverage_bps=weighted_coverage_bps,
            healthy_groups=healthy_groups,
            gaps=gaps,
            reason_code=reason_code,
            may_publish_verified_stories=may_publish_verified_stories,
            may_publish_credible_no_news=may_publish_credible_no_news,
            source_registry_hash=proof.source_registry_hash,
            source_policy_hash=proof.source_policy_hash,
            source_snapshot_hash=proof.source_snapshot_hash,
            observation_set_hash=proof.observation_set_hash,
            coverage_proof_hash=proof.coverage_proof_hash,
            coverage_result_hash=result_hash,
        )


class CoverageEvaluator:
    """Evaluate only the fixed denominator contained in a validated proof."""

    def evaluate(self, proof: CoverageProof) -> CoverageResult:
        if not isinstance(proof, CoverageProof):
            raise TypeError("CoverageEvaluator requires a CoverageProof")
        proof.validate()

        observations = {
            (observation.source_id, observation.endpoint_id): observation
            for observation in proof.observation_set.observations
        }
        all_group_weights: dict[tuple[SourceLane, str], int] = {}
        healthy_group_weights: dict[tuple[SourceLane, str], int] = {}
        for expected in proof.snapshot.expected_endpoints:
            if expected.lane is None:
                continue
            key = (expected.lane, expected.origin_group)
            all_group_weights[key] = max(
                all_group_weights.get(key, 0),
                expected.weight_bps,
            )
            observation = observations[(expected.source_id, expected.endpoint_id)]
            if observation.outcome in {
                ObservationOutcome.COMPLETE_ZERO,
                ObservationOutcome.COMPLETE_WITH_ITEMS,
            }:
                healthy_group_weights[key] = max(
                    healthy_group_weights.get(key, 0),
                    expected.weight_bps,
                )

        denominator = sum(all_group_weights.values())
        numerator = sum(healthy_group_weights.values())
        weighted_coverage_bps = numerator * 10_000 // denominator if denominator else 0
        healthy_groups = tuple(
            (
                lane,
                sum(1 for group_lane, _group in healthy_group_weights if group_lane is lane),
            )
            for lane in SourceLane
        )
        gaps = tuple(
            f"{lane.value}: {count} healthy independent origin groups"
            for lane, count in healthy_groups
            if count < 2
        )
        coverage_a = (
            all(count >= 2 for _lane, count in healthy_groups) and weighted_coverage_bps >= 8_500
        )
        coverage_b = (
            all(count >= 1 for _lane, count in healthy_groups) and weighted_coverage_bps >= 7_000
        )
        if coverage_a:
            return CoverageResult.create(
                level=CoverageLevel.A,
                disposition=CoverageDecision.PUBLISH,
                weighted_coverage_bps=weighted_coverage_bps,
                healthy_groups=healthy_groups,
                gaps=(),
                reason_code=None,
                may_publish_verified_stories=True,
                may_publish_credible_no_news=True,
                proof=proof,
            )
        if coverage_b:
            return CoverageResult.create(
                level=CoverageLevel.B,
                disposition=CoverageDecision.PUBLISH,
                weighted_coverage_bps=weighted_coverage_bps,
                healthy_groups=healthy_groups,
                gaps=gaps,
                reason_code=None,
                may_publish_verified_stories=True,
                may_publish_credible_no_news=False,
                proof=proof,
            )
        return CoverageResult.create(
            level=CoverageLevel.INSUFFICIENT,
            disposition=CoverageDecision.RETRYABLE,
            weighted_coverage_bps=weighted_coverage_bps,
            healthy_groups=healthy_groups,
            gaps=gaps,
            reason_code="COVERAGE_INSUFFICIENT",
            may_publish_verified_stories=False,
            may_publish_credible_no_news=False,
            proof=proof,
        )


def stable_source_id(origin: str, original_index: int, raw_record_hash: str) -> str:
    """Retain the historical public identity helper without a second implementation."""

    return _stable_source_id(origin, original_index, raw_record_hash)


def ensure_additive_multiset(
    baseline: Counter[str],
    proposed: Counter[str],
) -> None:
    """Reject missing duplicate occurrences; additions are allowed."""

    removed = {
        identity: count - proposed.get(identity, 0)
        for identity, count in baseline.items()
        if proposed.get(identity, 0) < count
    }
    if removed:
        details = ", ".join(f"{identity} x{count}" for identity, count in sorted(removed.items()))
        raise ValueError(f"additive source update would remove: {details}")
