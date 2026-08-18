"""Deterministic curation contracts over the sealed source/evidence closure."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from .evidence import EvidenceItem, EvidencePackage
from .fetch_service import FetchBatch
from .provenance import (
    CoverageProof,
    PolicyState,
    SourcePolicyGeneration,
    SourceSnapshot,
    canonical_json_bytes,
)
from .source_governance import CoverageDecision, CoverageEvaluator, CoverageResult

HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
USER_CONTEXT_FIELDS = {
    "schema_version",
    "contract_id",
    "projects",
    "technical_environment",
    "priorities",
    "style",
    "prohibited_inferences",
    "personalization_rule",
    "vault_inference_allowed",
}
STYLE_FIELDS = {
    "language",
    "orientation",
    "evidence_first",
    "precision_over_recall",
    "abstention_allowed",
}


def _hash(payload: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _require_hash(value: str, field: str) -> None:
    if HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical SHA-256 digest")


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _title_tokens(value: str) -> frozenset[str]:
    normalized = normalize_text(value)
    contains_cjk = any("\u3400" <= character <= "\u9fff" for character in normalized)
    if not contains_cjk:
        return frozenset(normalized.split())
    compact = "".join(normalized.split())
    if len(compact) < 3:
        return frozenset((compact,)) if compact else frozenset()
    return frozenset(compact[index : index + 3] for index in range(len(compact) - 2))


class CurationPolicyState(str, Enum):
    SYNTHETIC_QUALIFICATION = "SYNTHETIC_QUALIFICATION"


class CurationDisposition(str, Enum):
    READY = "READY"
    COVERAGE_INSUFFICIENT = "COVERAGE_INSUFFICIENT"
    POLICY_OVERFLOW = "POLICY_OVERFLOW"


class ExclusionReason(str, Enum):
    MISSING_EVENT_TIME = "MISSING_EVENT_TIME"
    EVENT_TIME_OUTSIDE_WINDOW = "EVENT_TIME_OUTSIDE_WINDOW"


@dataclass(frozen=True)
class UserPriority:
    rank: int
    topics: tuple[str, ...]


@dataclass(frozen=True)
class UserContextContract:
    schema_version: str
    contract_id: str
    projects: tuple[str, ...]
    technical_environment: tuple[str, ...]
    priorities: tuple[UserPriority, ...]
    prohibited_inferences: tuple[str, ...]
    personalization_rule: str
    user_context_hash: str

    @classmethod
    def parse(cls, document: bytes, *, expected_hash: str) -> UserContextContract:
        if type(document) is not bytes:
            raise TypeError("user_context_json must be bytes")
        _require_hash(expected_hash, "expected_user_context_hash")
        try:
            raw = json.loads(document.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("UserContext must be UTF-8 JSON") from error
        if not isinstance(raw, dict) or set(raw) != USER_CONTEXT_FIELDS:
            raise ValueError("UserContext fields do not match the approved schema")
        if raw["vault_inference_allowed"] is not False:
            raise ValueError("UserContext must prohibit Vault inference")
        style = raw["style"]
        if not isinstance(style, dict) or set(style) != STYLE_FIELDS:
            raise ValueError("UserContext style fields are invalid")
        if any(
            type(style[name]) is not bool for name in STYLE_FIELDS - {"language", "orientation"}
        ):
            raise ValueError("UserContext style flags must be booleans")
        _require_text(style["language"], "style.language")
        _require_text(style["orientation"], "style.orientation")

        def text_tuple(value: object, field: str) -> tuple[str, ...]:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{field} must be a non-empty list")
            result = tuple(_require_text(item, field) for item in value)
            if len(result) != len(set(result)):
                raise ValueError(f"{field} must not contain duplicates")
            return result

        raw_priorities = raw["priorities"]
        if not isinstance(raw_priorities, list) or not raw_priorities:
            raise ValueError("priorities must be a non-empty list")
        priorities: list[UserPriority] = []
        for index, priority in enumerate(raw_priorities):
            if not isinstance(priority, dict) or set(priority) != {"rank", "topics"}:
                raise ValueError("priority fields are invalid")
            rank = priority["rank"]
            if type(rank) is not int or rank <= 0:
                raise ValueError("priority rank must be a positive integer")
            priorities.append(
                UserPriority(
                    rank=rank, topics=text_tuple(priority["topics"], f"priorities[{index}]")
                )
            )
        if tuple(priority.rank for priority in priorities) != tuple(
            sorted(priority.rank for priority in priorities)
        ) or len({priority.rank for priority in priorities}) != len(priorities):
            raise ValueError("priority ranks must be unique and ascending")
        schema_version = _require_text(raw["schema_version"], "schema_version")
        contract_id = _require_text(raw["contract_id"], "contract_id")
        projects = text_tuple(raw["projects"], "projects")
        technical_environment = text_tuple(raw["technical_environment"], "technical_environment")
        prohibited_inferences = text_tuple(raw["prohibited_inferences"], "prohibited_inferences")
        personalization_rule = _require_text(raw["personalization_rule"], "personalization_rule")
        canonical_hash = _hash(raw)
        if canonical_hash != expected_hash:
            raise ValueError("UserContext hash does not match the approved policy")
        return cls(
            schema_version=schema_version,
            contract_id=contract_id,
            projects=projects,
            technical_environment=technical_environment,
            priorities=tuple(priorities),
            prohibited_inferences=prohibited_inferences,
            personalization_rule=personalization_rule,
            user_context_hash=canonical_hash,
        )


@dataclass(frozen=True)
class CurationPolicyGeneration:
    state: CurationPolicyState
    expected_user_context_hash: str
    evidence_role_order: tuple[str, ...]
    generator_model: str
    reviewer_model: str
    prompt_hash: str
    schema_hash: str
    repair_budget: int
    candidate_cap: int
    clustering_window_hours: int
    title_similarity_bps: int
    similarity_comparison_budget: int
    max_stories: int
    max_claims_per_story: int
    max_assertive_claims: int
    policy_generation_hash: str

    @classmethod
    def create_synthetic(
        cls,
        *,
        expected_user_context_hash: str,
        evidence_role_order: tuple[str, ...],
        generator_model: str,
        reviewer_model: str,
        prompt_hash: str,
        schema_hash: str,
        repair_budget: int,
    ) -> CurationPolicyGeneration:
        _require_hash(expected_user_context_hash, "expected_user_context_hash")
        _require_hash(prompt_hash, "prompt_hash")
        _require_hash(schema_hash, "schema_hash")
        if not evidence_role_order or any(not role.strip() for role in evidence_role_order):
            raise ValueError("evidence role ordering must be complete")
        if len(set(evidence_role_order)) != len(evidence_role_order):
            raise ValueError("evidence role ordering must not contain duplicates")
        if type(repair_budget) is not int or not 0 <= repair_budget <= 1:
            raise ValueError("repair budget must be zero or one")
        payload = {
            "state": CurationPolicyState.SYNTHETIC_QUALIFICATION.value,
            "expected_user_context_hash": expected_user_context_hash,
            "evidence_role_order": list(evidence_role_order),
            "window_rule": "ASIA_SHANGHAI_D_MINUS_1_0700_TO_D_0700",
            "event_time_precedence": ["updated_at", "published_at"],
            "normalization": "UNICODE_NFKC_CASEFOLD_ALNUM_SPACE_V1",
            "clustering": "LOSSLESS_UNION_FIND_TITLE_JACCARD_V1",
            "clustering_window_hours": 72,
            "title_similarity_bps": 8_500,
            "similarity_comparison_budget": 50_000,
            "profile_matching": "NORMALIZED_CASEFOLD_SUBSTRING_V1",
            "sort_keys": ["profile_rank", "role_rank", "latest_event_time", "cluster_hash"],
            "candidate_cap": 24,
            "overflow": CurationDisposition.POLICY_OVERFLOW.value,
            "generator_model": _require_text(generator_model, "generator_model"),
            "reviewer_model": _require_text(reviewer_model, "reviewer_model"),
            "prompt_hash": prompt_hash,
            "schema_hash": schema_hash,
            "repair_budget": repair_budget,
            "max_stories": 8,
            "max_claims_per_story": 4,
            "max_assertive_claims": 24,
            "review_rule": "ALL_ASSERTIVE_CLAIMS_REQUIRED",
        }
        return cls(
            state=CurationPolicyState.SYNTHETIC_QUALIFICATION,
            expected_user_context_hash=expected_user_context_hash,
            evidence_role_order=evidence_role_order,
            generator_model=generator_model,
            reviewer_model=reviewer_model,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            repair_budget=repair_budget,
            candidate_cap=24,
            clustering_window_hours=72,
            title_similarity_bps=8_500,
            similarity_comparison_budget=50_000,
            max_stories=8,
            max_claims_per_story=4,
            max_assertive_claims=24,
            policy_generation_hash=_hash(payload),
        )

    def validate(self) -> None:
        recreated = self.create_synthetic(
            expected_user_context_hash=self.expected_user_context_hash,
            evidence_role_order=self.evidence_role_order,
            generator_model=self.generator_model,
            reviewer_model=self.reviewer_model,
            prompt_hash=self.prompt_hash,
            schema_hash=self.schema_hash,
            repair_budget=self.repair_budget,
        )
        if recreated != self:
            raise ValueError("curation policy generation is invalid")


@dataclass(frozen=True)
class ModelEvidenceItem:
    evidence_id: str
    source_id: str
    source_generation: str
    endpoint_id: str
    endpoint_generation: str
    origin_group: str
    evidence_role: str
    title: str
    content_text: str
    event_time: datetime
    evidence_item_hash: str
    source_snapshot_hash: str
    evidence_registry_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "endpoint_id": self.endpoint_id,
            "endpoint_generation": self.endpoint_generation,
            "origin_group": self.origin_group,
            "evidence_role": self.evidence_role,
            "title": self.title,
            "content_text": self.content_text,
            "event_time": self.event_time.isoformat(timespec="microseconds"),
            "evidence_item_hash": self.evidence_item_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "evidence_registry_hash": self.evidence_registry_hash,
        }


@dataclass(frozen=True)
class EvidenceLocator:
    evidence_id: str
    locator: str
    status: str = "UNVERIFIED_LOCATOR"


@dataclass(frozen=True)
class CurationExclusion:
    evidence_id: str
    reason: ExclusionReason


@dataclass(frozen=True)
class EventCluster:
    cluster_hash: str
    evidence: tuple[ModelEvidenceItem, ...]
    locators: tuple[EvidenceLocator, ...]
    latest_event_time: datetime
    best_profile_rank: int | None
    relevance_reason: str
    profile_fields: tuple[str, ...]
    best_role_rank: int

    def identity_payload(self) -> dict[str, object]:
        return {
            "members": [item.payload() for item in self.evidence],
            "locators": [
                {
                    "evidence_id": locator.evidence_id,
                    "locator": locator.locator,
                    "status": locator.status,
                }
                for locator in self.locators
            ],
            "latest_event_time": self.latest_event_time.isoformat(timespec="microseconds"),
            "best_profile_rank": self.best_profile_rank,
            "relevance_reason": self.relevance_reason,
            "profile_fields": list(self.profile_fields),
            "best_role_rank": self.best_role_rank,
        }

    def validate(self) -> None:
        if not self.evidence:
            raise ValueError("event cluster must contain evidence")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("event cluster contains duplicate evidence identities")
        if tuple(locator.evidence_id for locator in self.locators) != evidence_ids:
            raise ValueError("event cluster locators must exactly match evidence ordering")
        if any(
            locator.status != "UNVERIFIED_LOCATOR" or not locator.locator.strip()
            for locator in self.locators
        ):
            raise ValueError("event cluster locator is invalid")
        if _hash(self.identity_payload()) != self.cluster_hash:
            raise ValueError("event cluster hash is invalid")
        if self.latest_event_time != max(item.event_time for item in self.evidence):
            raise ValueError("event cluster latest event time is invalid")
        if type(self.best_role_rank) is not int or self.best_role_rank < 0:
            raise ValueError("event cluster role rank is invalid")
        if self.best_profile_rank is None:
            if self.profile_fields or self.relevance_reason != "GENERAL_POSSIBLE_IMPACT":
                raise ValueError("event cluster general relevance metadata is invalid")
        elif (
            type(self.best_profile_rank) is not int
            or self.best_profile_rank <= 0
            or not self.profile_fields
            or self.relevance_reason != "PROFILE_TOPIC_MATCH"
        ):
            raise ValueError("event cluster profile relevance metadata is invalid")

    def payload(self) -> dict[str, object]:
        return {
            "evidence": [item.payload() for item in self.evidence],
            "locators": [
                {
                    "evidence_id": locator.evidence_id,
                    "locator": locator.locator,
                    "status": locator.status,
                }
                for locator in self.locators
            ],
            "latest_event_time": self.latest_event_time.isoformat(timespec="microseconds"),
            "best_profile_rank": self.best_profile_rank,
            "relevance_reason": self.relevance_reason,
            "profile_fields": list(self.profile_fields),
            "best_role_rank": self.best_role_rank,
        }

    def model_candidate(self) -> ModelCandidate:
        return ModelCandidate(
            cluster_hash=self.cluster_hash,
            evidence=self.evidence,
            latest_event_time=self.latest_event_time,
            best_profile_rank=self.best_profile_rank,
            relevance_reason=self.relevance_reason,
            profile_fields=self.profile_fields,
            best_role_rank=self.best_role_rank,
        )


@dataclass(frozen=True)
class ModelCandidate:
    cluster_hash: str
    evidence: tuple[ModelEvidenceItem, ...]
    latest_event_time: datetime
    best_profile_rank: int | None
    relevance_reason: str
    profile_fields: tuple[str, ...]
    best_role_rank: int

    def payload(self) -> dict[str, object]:
        return {
            "cluster_hash": self.cluster_hash,
            "evidence": [item.payload() for item in self.evidence],
            "latest_event_time": self.latest_event_time.isoformat(timespec="microseconds"),
            "best_profile_rank": self.best_profile_rank,
            "relevance_reason": self.relevance_reason,
            "profile_fields": list(self.profile_fields),
            "best_role_rank": self.best_role_rank,
        }


@dataclass(frozen=True)
class CurationResult:
    disposition: CurationDisposition
    audit_clusters: tuple[EventCluster, ...]
    model_candidates: tuple[ModelCandidate, ...]
    exclusions: tuple[CurationExclusion, ...]
    input_evidence_ids: tuple[str, ...]
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
    max_stories: int
    max_claims_per_story: int
    max_assertive_claims: int
    similarity_comparison_budget: int
    similarity_comparisons: int
    similarity_budget_exhausted: bool
    curation_result_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "disposition": self.disposition.value,
            "audit_clusters": [
                cluster.payload() | {"cluster_hash": cluster.cluster_hash}
                for cluster in self.audit_clusters
            ],
            "model_candidate_hashes": [cluster.cluster_hash for cluster in self.model_candidates],
            "exclusions": [
                {"evidence_id": exclusion.evidence_id, "reason": exclusion.reason.value}
                for exclusion in self.exclusions
            ],
            "input_evidence_ids": list(self.input_evidence_ids),
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
            "max_stories": self.max_stories,
            "max_claims_per_story": self.max_claims_per_story,
            "max_assertive_claims": self.max_assertive_claims,
            "similarity_comparison_budget": self.similarity_comparison_budget,
            "similarity_comparisons": self.similarity_comparisons,
            "similarity_budget_exhausted": self.similarity_budget_exhausted,
        }

    def validate(self) -> None:
        if _hash(self.payload()) != self.curation_result_hash:
            raise ValueError("curation result hash is invalid")
        if (
            self.similarity_comparison_budget != 50_000
            or type(self.similarity_comparisons) is not int
            or not 0 <= self.similarity_comparisons <= self.similarity_comparison_budget
            or type(self.similarity_budget_exhausted) is not bool
            or (
                self.similarity_budget_exhausted
                and self.similarity_comparisons != self.similarity_comparison_budget
            )
        ):
            raise ValueError("curation similarity work accounting is invalid")
        audit_hashes = {cluster.cluster_hash for cluster in self.audit_clusters}
        if len(audit_hashes) != len(self.audit_clusters):
            raise ValueError("curation result contains duplicate cluster identities")
        evidence_ids: list[str] = []
        for cluster in self.audit_clusters:
            cluster.validate()
            evidence_ids.extend(item.evidence_id for item in cluster.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence must belong to exactly one event cluster")
        exclusion_ids = [exclusion.evidence_id for exclusion in self.exclusions]
        if (
            any(not evidence_id.strip() for evidence_id in exclusion_ids)
            or len(set(exclusion_ids)) != len(exclusion_ids)
            or set(evidence_ids) & set(exclusion_ids)
        ):
            raise ValueError("evidence must belong to exactly one cluster or exclusion")
        if (
            tuple(sorted(set(self.input_evidence_ids))) != self.input_evidence_ids
            or tuple(sorted((*evidence_ids, *exclusion_ids))) != self.input_evidence_ids
        ):
            raise ValueError("curation result does not preserve the complete evidence partition")
        if any(cluster.cluster_hash not in audit_hashes for cluster in self.model_candidates):
            raise ValueError("model candidates must be members of the audit cluster set")
        if self.disposition is CurationDisposition.READY:
            expected_candidates = tuple(
                cluster.model_candidate() for cluster in self.audit_clusters
            )
            if self.model_candidates != expected_candidates:
                raise ValueError("model candidates must exactly match audit clusters")
        elif self.model_candidates:
            raise ValueError("blocked curation must not expose model candidates")


class CurationEngine:
    def curate(
        self,
        *,
        source_policy: SourcePolicyGeneration,
        snapshot: SourceSnapshot,
        fetch_batch: FetchBatch,
        evidence_package: EvidencePackage,
        coverage_proof: CoverageProof,
        claimed_coverage: CoverageResult,
        user_context_json: bytes,
        curation_policy: CurationPolicyGeneration,
    ) -> CurationResult:
        fetch_batch.validate(source_policy, snapshot)
        evidence_package.validate(source_policy, snapshot, fetch_batch)
        coverage_proof.validate()
        if (
            coverage_proof.policy != source_policy
            or coverage_proof.snapshot != snapshot
            or coverage_proof.observation_set != evidence_package.observation_set
        ):
            raise ValueError("coverage proof does not match the evidence closure")
        recomputed_coverage = CoverageEvaluator().evaluate(coverage_proof)
        if recomputed_coverage != claimed_coverage:
            raise ValueError("claimed coverage result does not match recomputed coverage result")
        curation_policy.validate()
        user_context = UserContextContract.parse(
            user_context_json,
            expected_hash=curation_policy.expected_user_context_hash,
        )
        role_ranks = {role: index for index, role in enumerate(curation_policy.evidence_role_order)}
        policy_entries = {entry.source_id: entry for entry in source_policy.entries}
        for entry in source_policy.entries:
            if entry.state is PolicyState.ACTIVE and entry.evidence_role not in role_ranks:
                raise ValueError("active source evidence role is absent from curation policy")
        disposition = (
            CurationDisposition.READY
            if claimed_coverage.disposition is CoverageDecision.PUBLISH
            else CurationDisposition.COVERAGE_INSUFFICIENT
        )
        eligible: list[
            tuple[
                EvidenceItem,
                ModelEvidenceItem,
                int | None,
                tuple[str, ...],
                int,
            ]
        ] = []
        exclusions: list[CurationExclusion] = []
        for item in evidence_package.items:
            event_time = self._event_time(item, snapshot)
            if event_time is None:
                reason = (
                    ExclusionReason.MISSING_EVENT_TIME
                    if item.updated_at is None and item.published_at is None
                    else ExclusionReason.EVENT_TIME_OUTSIDE_WINDOW
                )
                exclusions.append(CurationExclusion(item.evidence_id, reason))
                continue
            entry = policy_entries[item.source_id]
            model_item = ModelEvidenceItem(
                evidence_id=item.evidence_id,
                source_id=item.source_id,
                source_generation=item.source_generation,
                endpoint_id=item.endpoint_id,
                endpoint_generation=item.endpoint_generation,
                origin_group=entry.origin_group,
                evidence_role=entry.evidence_role,
                title=item.title,
                content_text=item.content_text,
                event_time=event_time,
                evidence_item_hash=item.evidence_item_hash,
                source_snapshot_hash=snapshot.source_snapshot_hash,
                evidence_registry_hash=evidence_package.evidence_registry_hash,
            )
            profile_rank, profile_fields = self._profile_match(model_item, user_context)
            eligible.append(
                (
                    item,
                    model_item,
                    profile_rank,
                    profile_fields,
                    role_ranks[entry.evidence_role],
                )
            )
        clusters, similarity_comparisons, similarity_budget_exhausted = self._cluster(
            eligible, curation_policy
        )
        clusters.sort(key=self._cluster_sort_key)
        audit_clusters = tuple(clusters)
        if disposition is CurationDisposition.COVERAGE_INSUFFICIENT:
            model_candidates: tuple[ModelCandidate, ...] = ()
        elif len(audit_clusters) > curation_policy.candidate_cap or similarity_budget_exhausted:
            disposition = CurationDisposition.POLICY_OVERFLOW
            model_candidates = ()
        else:
            model_candidates = tuple(cluster.model_candidate() for cluster in audit_clusters)
        result = CurationResult(
            disposition=disposition,
            audit_clusters=audit_clusters,
            model_candidates=model_candidates,
            exclusions=tuple(exclusions),
            input_evidence_ids=tuple(sorted(item.evidence_id for item in evidence_package.items)),
            source_registry_hash=source_policy.source_registry_hash,
            source_policy_hash=source_policy.source_policy_hash,
            source_snapshot_hash=snapshot.source_snapshot_hash,
            fetch_batch_hash=fetch_batch.fetch_batch_hash,
            observation_set_hash=evidence_package.observation_set.observation_set_hash,
            coverage_proof_hash=coverage_proof.coverage_proof_hash,
            coverage_result_hash=claimed_coverage.coverage_result_hash,
            evidence_registry_hash=evidence_package.evidence_registry_hash,
            user_context_hash=user_context.user_context_hash,
            curation_policy_hash=curation_policy.policy_generation_hash,
            max_stories=curation_policy.max_stories,
            max_claims_per_story=curation_policy.max_claims_per_story,
            max_assertive_claims=curation_policy.max_assertive_claims,
            similarity_comparison_budget=curation_policy.similarity_comparison_budget,
            similarity_comparisons=similarity_comparisons,
            similarity_budget_exhausted=similarity_budget_exhausted,
            curation_result_hash="",
        )
        result = replace(result, curation_result_hash=_hash(result.payload()))
        result.validate()
        return result

    @staticmethod
    def _event_time(item: EvidenceItem, snapshot: SourceSnapshot) -> datetime | None:
        if (
            item.updated_at is not None
            and snapshot.window_start <= item.updated_at < snapshot.cutoff_at
        ):
            return item.updated_at
        if (
            item.published_at is not None
            and snapshot.window_start <= item.published_at < snapshot.cutoff_at
        ):
            return item.published_at
        return None

    @staticmethod
    def _profile_match(
        item: ModelEvidenceItem,
        context: UserContextContract,
    ) -> tuple[int | None, tuple[str, ...]]:
        content = normalize_text(f"{item.title} {item.content_text}")
        matches: list[tuple[int, str]] = []
        for priority_index, priority in enumerate(context.priorities):
            for topic_index, topic in enumerate(priority.topics):
                if normalize_text(topic) in content:
                    matches.append(
                        (priority.rank, f"priorities[{priority_index}].topics[{topic_index}]")
                    )
        if not matches:
            return None, ()
        best_rank = min(rank for rank, _field in matches)
        return best_rank, tuple(field for rank, field in matches if rank == best_rank)

    @classmethod
    def _cluster(
        cls,
        eligible: list[
            tuple[
                EvidenceItem,
                ModelEvidenceItem,
                int | None,
                tuple[str, ...],
                int,
            ]
        ],
        policy: CurationPolicyGeneration,
    ) -> tuple[list[EventCluster], int, bool]:
        ordered = sorted(eligible, key=lambda value: value[0].evidence_id)
        parents = list(range(len(ordered)))
        normalized_titles = [normalize_text(value[0].title) for value in ordered]
        title_tokens = [_title_tokens(value[0].title) for value in ordered]

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            parents[max(left_root, right_root)] = min(left_root, right_root)

        exact_groups: tuple[dict[str, int], ...] = ({}, {}, {})
        for index, value in enumerate(ordered):
            item = value[0]
            exact_keys = (
                item.item_url,
                item.content_hash if item.content_text else "",
                normalized_titles[index],
            )
            for exact_index, key in zip(exact_groups, exact_keys, strict=True):
                if not key:
                    continue
                existing = exact_index.setdefault(key, index)
                union(existing, index)

        similarity_comparisons = 0
        similarity_budget_exhausted = False
        for left in range(len(ordered)):
            if similarity_budget_exhausted:
                break
            for right in range(left + 1, len(ordered)):
                if find(left) == find(right):
                    continue
                left_model = ordered[left][1]
                right_model = ordered[right][1]
                if (
                    abs((left_model.event_time - right_model.event_time).total_seconds())
                    > policy.clustering_window_hours * 3600
                ):
                    continue
                if similarity_comparisons >= policy.similarity_comparison_budget:
                    similarity_budget_exhausted = True
                    break
                similarity_comparisons += 1
                if cls._titles_similar(
                    title_tokens[left], title_tokens[right], policy.title_similarity_bps
                ):
                    union(left, right)
        groups: dict[int, list[int]] = {}
        for index in range(len(ordered)):
            groups.setdefault(find(index), []).append(index)
        clusters: list[EventCluster] = []
        for root in sorted(groups):
            members = [ordered[index] for index in groups[root]]
            evidence = tuple(member[1] for member in members)
            locators = tuple(
                EvidenceLocator(member[0].evidence_id, member[0].item_url) for member in members
            )
            profile_ranks = [member[2] for member in members if member[2] is not None]
            best_profile_rank = min(profile_ranks) if profile_ranks else None
            profile_fields: list[str] = []
            if best_profile_rank is not None:
                for member in members:
                    if member[2] == best_profile_rank:
                        for field in member[3]:
                            if field not in profile_fields:
                                profile_fields.append(field)
            cluster = EventCluster(
                cluster_hash="",
                evidence=evidence,
                locators=locators,
                latest_event_time=max(item.event_time for item in evidence),
                best_profile_rank=best_profile_rank,
                relevance_reason=(
                    "PROFILE_TOPIC_MATCH"
                    if best_profile_rank is not None
                    else "GENERAL_POSSIBLE_IMPACT"
                ),
                profile_fields=tuple(profile_fields),
                best_role_rank=min(member[4] for member in members),
            )
            cluster = replace(cluster, cluster_hash=_hash(cluster.identity_payload()))
            cluster.validate()
            clusters.append(cluster)
        return clusters, similarity_comparisons, similarity_budget_exhausted

    @staticmethod
    def _titles_similar(
        left_tokens: frozenset[str],
        right_tokens: frozenset[str],
        title_similarity_bps: int,
    ) -> bool:
        union = left_tokens | right_tokens
        return bool(union) and (
            10_000 * len(left_tokens & right_tokens) >= title_similarity_bps * len(union)
        )

    @staticmethod
    def _cluster_sort_key(cluster: EventCluster) -> tuple[object, ...]:
        return (
            cluster.best_profile_rank if cluster.best_profile_rank is not None else 2**31,
            cluster.best_role_rank,
            -int(cluster.latest_event_time.timestamp() * 1_000_000),
            cluster.cluster_hash,
        )
