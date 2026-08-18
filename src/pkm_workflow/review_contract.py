"""Fail-closed review requirements for synthetic Generator drafts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from .curation import CurationDisposition, CurationResult, ModelEvidenceItem, normalize_text
from .provenance import canonical_json_bytes

IDENTITY_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}")
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def _hash(payload: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _require_identity(value: str, field: str) -> None:
    if IDENTITY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a stable lowercase identity")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def _require_hash(value: str, field: str) -> None:
    if HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical SHA-256 digest")


@dataclass(frozen=True)
class AssertiveClaim:
    claim_id: str
    statement: str
    evidence_ids: tuple[str, ...]
    claimed_risk: str

    def validate(self) -> None:
        _require_identity(self.claim_id, "claim_id")
        _require_text(self.statement, "claim statement")
        _require_text(self.claimed_risk, "claimed_risk")
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("assertive claim needs unique evidence references")

    def payload(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "evidence_ids": list(self.evidence_ids),
            "claimed_risk": self.claimed_risk,
        }


@dataclass(frozen=True)
class SourceExcerpt:
    excerpt_id: str
    evidence_id: str
    text: str

    def validate(self) -> None:
        _require_identity(self.excerpt_id, "excerpt_id")
        _require_text(self.evidence_id, "excerpt evidence_id")
        _require_text(self.text, "excerpt text")

    def payload(self) -> dict[str, object]:
        return {
            "excerpt_id": self.excerpt_id,
            "evidence_id": self.evidence_id,
            "text": self.text,
        }


@dataclass(frozen=True)
class StoryDraft:
    story_id: str
    title_claim_id: str
    top_rank: int | None
    claims: tuple[AssertiveClaim, ...]
    conclusion_claim_ids: tuple[str, ...]
    recommendation_claim_ids: tuple[str, ...]
    source_excerpts: tuple[SourceExcerpt, ...]

    def payload(self) -> dict[str, object]:
        return {
            "story_id": self.story_id,
            "title_claim_id": self.title_claim_id,
            "top_rank": self.top_rank,
            "claims": [claim.payload() for claim in self.claims],
            "conclusion_claim_ids": list(self.conclusion_claim_ids),
            "recommendation_claim_ids": list(self.recommendation_claim_ids),
            "source_excerpts": [excerpt.payload() for excerpt in self.source_excerpts],
        }


@dataclass(frozen=True)
class GeneratorDraft:
    stories: tuple[StoryDraft, ...]
    digest_conclusion_claim_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "stories": [story.payload() for story in self.stories],
            "digest_conclusion_claim_ids": list(self.digest_conclusion_claim_ids),
        }


@dataclass(frozen=True)
class ReviewRequirement:
    claim_id: str
    statement: str
    evidence_ids: tuple[str, ...]
    required: bool
    source_registry_hash: str
    source_policy_hash: str
    source_snapshot_hash: str
    fetch_batch_hash: str
    observation_set_hash: str
    coverage_proof_hash: str
    coverage_result_hash: str
    evidence_registry_hash: str
    user_context_hash: str
    curation_policy_hash: str
    curation_result_hash: str
    generator_draft_hash: str
    review_requirement_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "evidence_ids": list(self.evidence_ids),
            "required": self.required,
            "source_registry_hash": self.source_registry_hash,
            "source_policy_hash": self.source_policy_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "fetch_batch_hash": self.fetch_batch_hash,
            "observation_set_hash": self.observation_set_hash,
            "coverage_proof_hash": self.coverage_proof_hash,
            "coverage_result_hash": self.coverage_result_hash,
            "evidence_registry_hash": self.evidence_registry_hash,
            "user_context_hash": self.user_context_hash,
            "curation_policy_hash": self.curation_policy_hash,
            "curation_result_hash": self.curation_result_hash,
            "generator_draft_hash": self.generator_draft_hash,
        }

    def validate(self) -> None:
        _require_identity(self.claim_id, "claim_id")
        _require_text(self.statement, "claim statement")
        if self.required is not True:
            raise ValueError("review requirement must remain required")
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("review requirement needs unique evidence references")
        for field in (
            "source_registry_hash",
            "source_policy_hash",
            "source_snapshot_hash",
            "fetch_batch_hash",
            "observation_set_hash",
            "coverage_proof_hash",
            "coverage_result_hash",
            "evidence_registry_hash",
            "user_context_hash",
            "curation_policy_hash",
            "curation_result_hash",
            "generator_draft_hash",
            "review_requirement_hash",
        ):
            _require_hash(getattr(self, field), field)
        if _hash(self.payload()) != self.review_requirement_hash:
            raise ValueError("review requirement hash is invalid")


class ReviewContract:
    def derive(
        self,
        *,
        curation: CurationResult,
        draft: GeneratorDraft,
    ) -> tuple[ReviewRequirement, ...]:
        curation.validate()
        if curation.disposition is not CurationDisposition.READY:
            raise ValueError("blocked curation cannot produce review requirements")
        if not isinstance(draft, GeneratorDraft) or not draft.stories:
            raise ValueError("Generator draft must contain at least one story")
        if len(draft.stories) > curation.max_stories:
            raise ValueError("SCHEMA_INVALID: too many stories")
        evidence: dict[str, ModelEvidenceItem] = {}
        evidence_cluster: dict[str, str] = {}
        for cluster in curation.model_candidates:
            for item in cluster.evidence:
                evidence[item.evidence_id] = item
                evidence_cluster[item.evidence_id] = cluster.cluster_hash
        story_ids: set[str] = set()
        top_ranks: set[int] = set()
        claim_ids: set[str] = set()
        excerpt_ids: set[str] = set()
        claims: list[AssertiveClaim] = []
        for story in draft.stories:
            _require_identity(story.story_id, "story_id")
            if story.story_id in story_ids:
                raise ValueError("story IDs must be unique")
            story_ids.add(story.story_id)
            if len(story.claims) > curation.max_claims_per_story:
                raise ValueError("SCHEMA_INVALID: too many claims in a story")
            if story.top_rank is not None and (
                type(story.top_rank) is not int or not 1 <= story.top_rank <= 3
            ):
                raise ValueError("top rank must be within 1..3")
            if story.top_rank is not None:
                if story.top_rank in top_ranks:
                    raise ValueError("top ranks must be unique")
                top_ranks.add(story.top_rank)
            local_claim_ids = {claim.claim_id for claim in story.claims}
            if len(local_claim_ids) != len(story.claims):
                raise ValueError("claim IDs must be unique")
            referenced_claims = {
                story.title_claim_id,
                *story.conclusion_claim_ids,
                *story.recommendation_claim_ids,
            }
            if not referenced_claims <= local_claim_ids:
                raise ValueError("story output fields may reference only assertive claims")
            story_clusters: set[str] = set()
            for claim in story.claims:
                claim.validate()
                if claim.claim_id in claim_ids or claim.claim_id in excerpt_ids:
                    raise ValueError("claim IDs must be globally unique")
                claim_ids.add(claim.claim_id)
                story_clusters.add(
                    self._validate_evidence_refs(claim.evidence_ids, evidence, evidence_cluster)
                )
                claims.append(claim)
            for excerpt in story.source_excerpts:
                excerpt.validate()
                if excerpt.excerpt_id in excerpt_ids or excerpt.excerpt_id in claim_ids:
                    raise ValueError("excerpt IDs must be globally unique from claims")
                excerpt_ids.add(excerpt.excerpt_id)
                evidence_item = evidence.get(excerpt.evidence_id)
                if evidence_item is None:
                    raise ValueError("source excerpt references unknown evidence")
                story_clusters.add(evidence_cluster[excerpt.evidence_id])
                normalized_excerpt = normalize_text(excerpt.text)
                if not normalized_excerpt or not any(
                    normalized_excerpt in normalize_text(source)
                    for source in (evidence_item.title, evidence_item.content_text)
                ):
                    raise ValueError("source excerpt is not an exact normalized evidence span")
            if len(story_clusters) != 1:
                raise ValueError("story evidence must belong to one event cluster")
        if len(claims) > curation.max_assertive_claims:
            raise ValueError("SCHEMA_INVALID: too many assertive claims")
        if not set(draft.digest_conclusion_claim_ids) <= claim_ids:
            raise ValueError("digest conclusions may reference only assertive claims")
        draft_hash = _hash(draft.payload())
        requirements = []
        for claim in sorted(claims, key=lambda item: item.claim_id):
            requirement = ReviewRequirement(
                claim_id=claim.claim_id,
                statement=claim.statement,
                evidence_ids=claim.evidence_ids,
                required=True,
                source_registry_hash=curation.source_registry_hash,
                source_policy_hash=curation.source_policy_hash,
                source_snapshot_hash=curation.source_snapshot_hash,
                fetch_batch_hash=curation.fetch_batch_hash,
                observation_set_hash=curation.observation_set_hash,
                coverage_proof_hash=curation.coverage_proof_hash,
                coverage_result_hash=curation.coverage_result_hash,
                evidence_registry_hash=curation.evidence_registry_hash,
                user_context_hash=curation.user_context_hash,
                curation_policy_hash=curation.curation_policy_hash,
                curation_result_hash=curation.curation_result_hash,
                generator_draft_hash=draft_hash,
                review_requirement_hash="",
            )
            requirement = replace(
                requirement,
                review_requirement_hash=_hash(requirement.payload()),
            )
            requirement.validate()
            requirements.append(requirement)
        return tuple(requirements)

    @staticmethod
    def _validate_evidence_refs(
        evidence_ids: tuple[str, ...],
        evidence: dict[str, ModelEvidenceItem],
        evidence_cluster: dict[str, str],
    ) -> str:
        if any(evidence_id not in evidence for evidence_id in evidence_ids):
            raise ValueError("assertive claim references unknown evidence")
        clusters = {evidence_cluster[evidence_id] for evidence_id in evidence_ids}
        if len(clusters) != 1:
            raise ValueError("assertive claim evidence must belong to one event cluster")
        return next(iter(clusters))
