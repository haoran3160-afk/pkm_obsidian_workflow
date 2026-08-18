"""Deterministic source-policy and observation proof objects.

The SHA-256 bindings in this module detect inconsistent inputs relative to an
approved policy hash. They are not signatures and do not authenticate a local
process that can replace both code and approved configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .source_import import (
    ImportedSourceRecord,
    SourceBaseline,
    validate_imported_source_record,
    validate_source_baseline,
)

HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCE_ID_PATTERN = re.compile(r"^source-[0-9a-f]{32}$")
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


class RegistryClassificationRequired(ValueError):
    """Raised when no approved complete policy is available."""


class Workflow(str, Enum):
    AI = "ai"
    WEEKLY = "weekly"
    IELTS = "ielts"


class SourceLane(str, Enum):
    OFFICIAL_LABS = "official-labs"
    RESEARCH = "research"
    OPEN_SOURCE_RELEASES = "open-source-releases"
    INDUSTRY_POLICY = "industry-policy"


class PolicyState(str, Enum):
    ACTIVE = "ACTIVE"
    LEGACY_DISABLED_PRESERVED = "LEGACY_DISABLED_PRESERVED"


class ObservationScope(str, Enum):
    PRODUCTION = "PRODUCTION"
    PROBE = "PROBE"


class ObservationOutcome(str, Enum):
    COMPLETE_ZERO = "COMPLETE_ZERO"
    COMPLETE_WITH_ITEMS = "COMPLETE_WITH_ITEMS"
    FETCH_ERROR = "FETCH_ERROR"
    PARSE_ERROR = "PARSE_ERROR"

    @property
    def healthy(self) -> bool:
        return self in {
            ObservationOutcome.COMPLETE_ZERO,
            ObservationOutcome.COMPLETE_WITH_ITEMS,
        }


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a previously normalized value for identity hashing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _canonical_endpoint(value: str) -> str:
    endpoint = value.strip()
    _require_text(endpoint, "source endpoint")
    parsed = urlsplit(endpoint)
    if parsed.scheme.lower() in {"http", "https"}:
        if not parsed.hostname:
            raise ValueError("HTTP source endpoint requires a hostname")
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower()
        port = parsed.port
        if port is not None and not (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ):
            hostname = f"{hostname}:{port}"
        normalized = SplitResult(
            scheme,
            hostname,
            parsed.path or "/",
            parsed.query,
            "",
        )
        return urlunsplit(normalized)
    return "opaque:" + " ".join(endpoint.casefold().split())


def _endpoint_id(source_id: str, canonical_endpoint: str) -> str:
    digest = hashlib.sha256(f"{source_id}\x00{canonical_endpoint}".encode()).hexdigest()
    return f"endpoint-{digest[:32]}"


@dataclass(frozen=True)
class SourcePolicyEntry:
    source_id: str
    workflows: tuple[Workflow, ...]
    lane: SourceLane | None
    origin_group: str
    weight_bps: int
    endpoint_generation: str
    evidence_role: str
    state: PolicyState

    def __post_init__(self) -> None:
        if SOURCE_ID_PATTERN.fullmatch(self.source_id) is None:
            raise ValueError("source_id must be a sealed source identity")
        if type(self.weight_bps) is not int:
            raise TypeError("weight_bps must be an integer")
        if not 1 <= self.weight_bps <= 10_000:
            raise ValueError("weight_bps must be within 1..10000")
        _require_text(self.origin_group, "origin_group")
        _require_text(self.endpoint_generation, "endpoint_generation")
        _require_text(self.evidence_role, "evidence_role")
        if any(not isinstance(workflow, Workflow) for workflow in self.workflows):
            raise TypeError("workflows must contain Workflow values")
        if len(self.workflows) != len(set(self.workflows)):
            raise ValueError("workflows must not contain duplicates")
        if self.lane is not None and not isinstance(self.lane, SourceLane):
            raise TypeError("lane must be a SourceLane")
        if self.state is PolicyState.ACTIVE:
            if not self.workflows:
                raise ValueError("active source must belong to at least one workflow")
            if (
                Workflow.AI in self.workflows or Workflow.WEEKLY in self.workflows
            ) and self.lane is None:
                raise ValueError("AI and Weekly active sources require a four-lane lane")
        elif self.state is PolicyState.LEGACY_DISABLED_PRESERVED:
            if self.workflows or self.lane is not None:
                raise ValueError("legacy-disabled source cannot be active in a workflow")
        else:
            raise TypeError("state must be a PolicyState")

    def normalized(self) -> SourcePolicyEntry:
        return replace(
            self,
            workflows=tuple(sorted(self.workflows, key=lambda value: value.value)),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "workflows": [workflow.value for workflow in self.workflows],
            "lane": self.lane.value if self.lane is not None else None,
            "origin_group": self.origin_group,
            "weight_bps": self.weight_bps,
            "endpoint_generation": self.endpoint_generation,
            "evidence_role": self.evidence_role,
            "state": self.state.value,
        }


@dataclass(frozen=True)
class RegistrySource:
    source_id: str
    source_generation: str
    origin: str
    collection: str
    original_index: int
    raw_record_hash: str
    adapter: str
    name: str
    endpoint: str
    canonical_endpoint: str
    endpoint_id: str
    enabled: bool

    def payload(self) -> dict[str, object]:
        return asdict(self)


def _registry_source(
    record: ImportedSourceRecord,
    source_generation: str,
) -> RegistrySource:
    canonical_endpoint = _canonical_endpoint(record.endpoint)
    return RegistrySource(
        source_id=record.source_id,
        source_generation=source_generation,
        origin=record.origin,
        collection=record.collection,
        original_index=record.original_index,
        raw_record_hash=record.raw_record_hash,
        adapter=record.adapter,
        name=record.name,
        endpoint=record.endpoint,
        canonical_endpoint=canonical_endpoint,
        endpoint_id=_endpoint_id(record.source_id, canonical_endpoint),
        enabled=record.enabled,
    )


@dataclass(frozen=True)
class ApprovedSourceGeneration:
    generation_id: str
    approval_generation: str
    records: tuple[ImportedSourceRecord, ...]
    generation_hash: str

    @classmethod
    def create(
        cls,
        *,
        generation_id: str,
        approval_generation: str,
        records: tuple[ImportedSourceRecord, ...],
    ) -> ApprovedSourceGeneration:
        _require_text(generation_id, "generation_id")
        _require_text(approval_generation, "approval_generation")
        if not records:
            raise ValueError("approved source generation cannot be empty")
        identities = [record.source_id for record in records]
        if len(identities) != len(set(identities)):
            raise ValueError("approved source generation IDs must be unique")
        for record in records:
            validate_imported_source_record(record)
        ordered = tuple(sorted(records, key=lambda record: record.source_id))
        payload = {
            "generation_id": generation_id,
            "approval_generation": approval_generation,
            "records": [asdict(record) for record in ordered],
        }
        return cls(
            generation_id=generation_id,
            approval_generation=approval_generation,
            records=ordered,
            generation_hash=_sha256(payload),
        )

    def validate(self) -> None:
        recreated = ApprovedSourceGeneration.create(
            generation_id=self.generation_id,
            approval_generation=self.approval_generation,
            records=self.records,
        )
        if recreated != self:
            raise ValueError("approved source generation hash is invalid")


def _validate_policy_coverage(
    registry_sources: tuple[RegistrySource, ...] | list[RegistrySource],
    entries: tuple[SourcePolicyEntry, ...],
) -> None:
    registry_ids = [record.source_id for record in registry_sources]
    if len(registry_ids) != len(set(registry_ids)):
        raise ValueError("source registry IDs must remain unique across generations")
    provided_ids = [entry.source_id for entry in entries]
    if len(provided_ids) != len(set(provided_ids)) or set(provided_ids) != set(registry_ids):
        raise ValueError("policy source IDs must exactly cover the source registry")
    by_source = {entry.source_id: entry for entry in entries}
    endpoint_groups: dict[str, str] = {}
    endpoint_active_lanes: dict[str, SourceLane | None] = {}
    for record in registry_sources:
        entry = by_source[record.source_id]
        if record.enabled and entry.state is not PolicyState.ACTIVE:
            raise ValueError("enabled baseline source must remain active in policy")
        if not record.enabled and entry.state is not PolicyState.LEGACY_DISABLED_PRESERVED:
            raise ValueError("disabled baseline source must be preserved as legacy-disabled")
        existing_group = endpoint_groups.setdefault(
            record.canonical_endpoint,
            entry.origin_group,
        )
        if existing_group != entry.origin_group:
            raise ValueError("records sharing a canonical endpoint must share one origin group")
        if entry.state is PolicyState.ACTIVE:
            existing_lane = endpoint_active_lanes.setdefault(
                record.canonical_endpoint,
                entry.lane,
            )
            if existing_lane != entry.lane:
                raise ValueError("active records sharing a canonical endpoint must share one lane")


@dataclass(frozen=True)
class SourcePolicyGeneration:
    baseline: SourceBaseline
    baseline_id: str
    approval_generation: str
    approved_append_generations: tuple[ApprovedSourceGeneration, ...]
    registry_sources: tuple[RegistrySource, ...]
    entries: tuple[SourcePolicyEntry, ...]
    source_registry_hash: str
    source_policy_hash: str

    @classmethod
    def create(
        cls,
        baseline: SourceBaseline,
        *,
        entries: tuple[SourcePolicyEntry, ...] | None,
        approval_generation: str,
        approved_appends: tuple[ApprovedSourceGeneration, ...] = (),
    ) -> SourcePolicyGeneration:
        validate_source_baseline(baseline)
        _require_text(approval_generation, "approval_generation")
        if entries is None:
            raise RegistryClassificationRequired("REGISTRY_CLASSIFICATION_REQUIRED")
        append_ids = [generation.generation_id for generation in approved_appends]
        if len(append_ids) != len(set(append_ids)):
            raise ValueError("approved append generation IDs must be unique")
        for generation in approved_appends:
            generation.validate()
        ordered_appends = tuple(
            sorted(approved_appends, key=lambda generation: generation.generation_id)
        )
        registry_sources = [
            _registry_source(record, baseline.baseline_id) for record in baseline.records
        ]
        for generation in ordered_appends:
            registry_sources.extend(
                _registry_source(record, generation.generation_id) for record in generation.records
            )
        registry_sources.sort(key=lambda record: record.source_id)
        ordered_entries = tuple(
            sorted(
                (entry.normalized() for entry in entries),
                key=lambda entry: entry.source_id,
            )
        )
        _validate_policy_coverage(registry_sources, ordered_entries)

        registry_payload = {
            "baseline_id": baseline.baseline_id,
            "approved_append_generations": [
                {
                    "generation_id": generation.generation_id,
                    "approval_generation": generation.approval_generation,
                    "generation_hash": generation.generation_hash,
                }
                for generation in ordered_appends
            ],
            "sources": [record.payload() for record in registry_sources],
        }
        source_registry_hash = _sha256(registry_payload)
        policy_payload = {
            "baseline_id": baseline.baseline_id,
            "approval_generation": approval_generation,
            "source_registry_hash": source_registry_hash,
            "entries": [entry.payload() for entry in ordered_entries],
        }
        return cls(
            baseline=baseline,
            baseline_id=baseline.baseline_id,
            approval_generation=approval_generation,
            approved_append_generations=ordered_appends,
            registry_sources=tuple(registry_sources),
            entries=ordered_entries,
            source_registry_hash=source_registry_hash,
            source_policy_hash=_sha256(policy_payload),
        )

    def validate(self) -> None:
        validate_source_baseline(self.baseline)
        if self.baseline_id != self.baseline.baseline_id:
            raise ValueError("policy baseline binding is invalid")
        append_ids = [generation.generation_id for generation in self.approved_append_generations]
        if len(append_ids) != len(set(append_ids)) or tuple(sorted(append_ids)) != tuple(
            append_ids
        ):
            raise ValueError("approved append generations are not canonical")
        for generation in self.approved_append_generations:
            generation.validate()
        expected_registry = [
            _registry_source(record, self.baseline_id) for record in self.baseline.records
        ]
        for generation in self.approved_append_generations:
            expected_registry.extend(
                _registry_source(record, generation.generation_id) for record in generation.records
            )
        expected_registry.sort(key=lambda record: record.source_id)
        if tuple(expected_registry) != self.registry_sources:
            raise ValueError(
                "source registry does not exactly match the bound baseline and appends"
            )
        canonical_entries = tuple(
            sorted(
                (entry.normalized() for entry in self.entries),
                key=lambda entry: entry.source_id,
            )
        )
        if canonical_entries != self.entries:
            raise ValueError("source policy entries are not canonical")
        _validate_policy_coverage(self.registry_sources, self.entries)
        if (
            HASH_PATTERN.fullmatch(self.source_registry_hash) is None
            or HASH_PATTERN.fullmatch(self.source_policy_hash) is None
        ):
            raise ValueError("source policy hashes are malformed")
        registry_payload = {
            "baseline_id": self.baseline_id,
            "approved_append_generations": [
                {
                    "generation_id": generation.generation_id,
                    "approval_generation": generation.approval_generation,
                    "generation_hash": generation.generation_hash,
                }
                for generation in self.approved_append_generations
            ],
            "sources": [record.payload() for record in self.registry_sources],
        }
        if _sha256(registry_payload) != self.source_registry_hash:
            raise ValueError("source registry hash is invalid")
        policy_payload = {
            "baseline_id": self.baseline_id,
            "approval_generation": self.approval_generation,
            "source_registry_hash": self.source_registry_hash,
            "entries": [entry.payload() for entry in self.entries],
        }
        if _sha256(policy_payload) != self.source_policy_hash:
            raise ValueError("source policy hash is invalid")

    def entry_for(self, source_id: str) -> SourcePolicyEntry:
        for entry in self.entries:
            if entry.source_id == source_id:
                return entry
        raise KeyError(source_id)

    def active_entries_for(self, workflow: Workflow) -> tuple[SourcePolicyEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.state is PolicyState.ACTIVE and workflow in entry.workflows
        )


@dataclass(frozen=True)
class ExpectedEndpoint:
    source_id: str
    source_generation: str
    endpoint_id: str
    endpoint_generation: str
    lane: SourceLane | None
    origin_group: str
    weight_bps: int

    def payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "endpoint_id": self.endpoint_id,
            "endpoint_generation": self.endpoint_generation,
            "lane": self.lane.value if self.lane is not None else None,
            "origin_group": self.origin_group,
            "weight_bps": self.weight_bps,
        }


@dataclass(frozen=True)
class SourceSnapshot:
    source_registry_hash: str
    source_policy_hash: str
    baseline_id: str
    approval_generation: str
    workflow: Workflow
    content_date: date
    window_start: datetime
    cutoff_at: datetime
    expected_endpoints: tuple[ExpectedEndpoint, ...]
    preserved_disabled_source_ids: tuple[str, ...]
    source_snapshot_hash: str

    @classmethod
    def create(
        cls,
        policy: SourcePolicyGeneration,
        *,
        workflow: Workflow,
        content_date: date,
    ) -> SourceSnapshot:
        policy.validate()
        if not isinstance(workflow, Workflow):
            raise TypeError("workflow must be a Workflow")
        if type(content_date) is not date:
            raise TypeError("content_date must be a date")
        policy_by_source = {entry.source_id: entry for entry in policy.entries}
        expected = []
        preserved = []
        for source in policy.registry_sources:
            entry = policy_by_source[source.source_id]
            if entry.state is PolicyState.LEGACY_DISABLED_PRESERVED:
                preserved.append(source.source_id)
                continue
            if workflow not in entry.workflows:
                continue
            expected.append(
                ExpectedEndpoint(
                    source_id=source.source_id,
                    source_generation=source.source_generation,
                    endpoint_id=source.endpoint_id,
                    endpoint_generation=entry.endpoint_generation,
                    lane=entry.lane,
                    origin_group=entry.origin_group,
                    weight_bps=entry.weight_bps,
                )
            )
        expected.sort(key=lambda item: (item.source_id, item.endpoint_id))
        preserved.sort()
        window_start = datetime.combine(
            content_date - timedelta(days=1),
            time(7, 0),
            tzinfo=SHANGHAI,
        )
        cutoff_at = datetime.combine(content_date, time(7, 0), tzinfo=SHANGHAI)
        payload = {
            "source_registry_hash": policy.source_registry_hash,
            "source_policy_hash": policy.source_policy_hash,
            "baseline_id": policy.baseline_id,
            "approval_generation": policy.approval_generation,
            "workflow": workflow.value,
            "content_date": content_date.isoformat(),
            "window_start": _iso(window_start),
            "cutoff_at": _iso(cutoff_at),
            "expected_endpoints": [item.payload() for item in expected],
            "preserved_disabled_source_ids": preserved,
        }
        return cls(
            source_registry_hash=policy.source_registry_hash,
            source_policy_hash=policy.source_policy_hash,
            baseline_id=policy.baseline_id,
            approval_generation=policy.approval_generation,
            workflow=workflow,
            content_date=content_date,
            window_start=window_start,
            cutoff_at=cutoff_at,
            expected_endpoints=tuple(expected),
            preserved_disabled_source_ids=tuple(preserved),
            source_snapshot_hash=_sha256(payload),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_registry_hash": self.source_registry_hash,
            "source_policy_hash": self.source_policy_hash,
            "baseline_id": self.baseline_id,
            "approval_generation": self.approval_generation,
            "workflow": self.workflow.value,
            "content_date": self.content_date.isoformat(),
            "window_start": _iso(self.window_start),
            "cutoff_at": _iso(self.cutoff_at),
            "expected_endpoints": [item.payload() for item in self.expected_endpoints],
            "preserved_disabled_source_ids": list(self.preserved_disabled_source_ids),
        }

    def validate(self) -> None:
        if _sha256(self.payload()) != self.source_snapshot_hash:
            raise ValueError("source snapshot hash is invalid")
        expected_start = datetime.combine(
            self.content_date - timedelta(days=1),
            time(7, 0),
            tzinfo=SHANGHAI,
        )
        expected_cutoff = datetime.combine(
            self.content_date,
            time(7, 0),
            tzinfo=SHANGHAI,
        )
        if self.window_start != expected_start or self.cutoff_at != expected_cutoff:
            raise ValueError("source snapshot window is not the exact Shanghai window")


@dataclass(frozen=True)
class TerminalObservation:
    source_id: str
    source_generation: str
    endpoint_id: str
    endpoint_generation: str
    window_start: datetime
    cutoff_at: datetime
    scope: ObservationScope
    outcome: ObservationOutcome
    item_count: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.source_id, "source_id"),
            (self.source_generation, "source_generation"),
            (self.endpoint_id, "endpoint_id"),
            (self.endpoint_generation, "endpoint_generation"),
        ):
            _require_text(value, name)
        if self.window_start.tzinfo is None or self.cutoff_at.tzinfo is None:
            raise ValueError("observation window must be timezone-aware")
        if self.window_start >= self.cutoff_at:
            raise ValueError("observation window must end after it starts")
        if not isinstance(self.scope, ObservationScope):
            raise TypeError("scope must be an ObservationScope")
        if not isinstance(self.outcome, ObservationOutcome):
            raise TypeError("outcome must be an ObservationOutcome")
        if type(self.item_count) is not int or self.item_count < 0:
            raise ValueError("item_count must be a non-negative integer")
        if self.outcome is ObservationOutcome.COMPLETE_WITH_ITEMS:
            if self.item_count == 0:
                raise ValueError("COMPLETE_WITH_ITEMS requires a positive item_count")
        elif self.item_count != 0:
            raise ValueError("only COMPLETE_WITH_ITEMS may have a positive item_count")

    def payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "endpoint_id": self.endpoint_id,
            "endpoint_generation": self.endpoint_generation,
            "window_start": _iso(self.window_start),
            "cutoff_at": _iso(self.cutoff_at),
            "scope": self.scope.value,
            "outcome": self.outcome.value,
            "item_count": self.item_count,
        }


@dataclass(frozen=True)
class ObservationSet:
    source_snapshot_hash: str
    observations: tuple[TerminalObservation, ...]
    observation_set_hash: str

    @classmethod
    def create(
        cls,
        snapshot: SourceSnapshot,
        observations: tuple[TerminalObservation, ...],
    ) -> ObservationSet:
        snapshot.validate()
        expected_by_key = {
            (expected.source_id, expected.endpoint_id): expected
            for expected in snapshot.expected_endpoints
        }
        keys = [(observation.source_id, observation.endpoint_id) for observation in observations]
        if len(keys) != len(set(keys)):
            raise ValueError("each endpoint must have exactly one terminal observation")
        if set(keys) != set(expected_by_key):
            raise ValueError("production observations must exactly match snapshot endpoints")
        ordered = tuple(
            sorted(
                observations,
                key=lambda item: (item.source_id, item.endpoint_id),
            )
        )
        for observation in ordered:
            expected = expected_by_key[(observation.source_id, observation.endpoint_id)]
            if observation.scope is not ObservationScope.PRODUCTION:
                raise ValueError("probe observation cannot enter a production observation set")
            if observation.source_generation != expected.source_generation:
                raise ValueError("observation source generation does not match snapshot")
            if observation.endpoint_generation != expected.endpoint_generation:
                raise ValueError("observation endpoint generation does not match snapshot")
            if (
                observation.window_start != snapshot.window_start
                or observation.cutoff_at != snapshot.cutoff_at
            ):
                raise ValueError("observation window does not match snapshot")
        payload = {
            "source_snapshot_hash": snapshot.source_snapshot_hash,
            "observations": [observation.payload() for observation in ordered],
        }
        return cls(
            source_snapshot_hash=snapshot.source_snapshot_hash,
            observations=ordered,
            observation_set_hash=_sha256(payload),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_snapshot_hash": self.source_snapshot_hash,
            "observations": [observation.payload() for observation in self.observations],
        }

    def validate(self) -> None:
        if _sha256(self.payload()) != self.observation_set_hash:
            raise ValueError("observation set hash is invalid")


@dataclass(frozen=True)
class CoverageProof:
    policy: SourcePolicyGeneration
    snapshot: SourceSnapshot
    observation_set: ObservationSet
    source_registry_hash: str
    source_policy_hash: str
    source_snapshot_hash: str
    observation_set_hash: str
    coverage_proof_hash: str

    @classmethod
    def create(
        cls,
        policy: SourcePolicyGeneration,
        snapshot: SourceSnapshot,
        observation_set: ObservationSet,
    ) -> CoverageProof:
        policy.validate()
        expected_snapshot = SourceSnapshot.create(
            policy,
            workflow=snapshot.workflow,
            content_date=snapshot.content_date,
        )
        if expected_snapshot != snapshot:
            raise ValueError("source snapshot is not the deterministic policy projection")
        expected_observations = ObservationSet.create(
            snapshot,
            observation_set.observations,
        )
        if expected_observations != observation_set:
            raise ValueError("observation set is not the exact snapshot terminal set")
        payload = {
            "source_registry_hash": snapshot.source_registry_hash,
            "source_policy_hash": snapshot.source_policy_hash,
            "source_snapshot_hash": snapshot.source_snapshot_hash,
            "observation_set_hash": observation_set.observation_set_hash,
        }
        return cls(
            policy=policy,
            snapshot=snapshot,
            observation_set=observation_set,
            source_registry_hash=snapshot.source_registry_hash,
            source_policy_hash=snapshot.source_policy_hash,
            source_snapshot_hash=snapshot.source_snapshot_hash,
            observation_set_hash=observation_set.observation_set_hash,
            coverage_proof_hash=_sha256(payload),
        )

    def validate(self) -> None:
        self.policy.validate()
        expected_snapshot = SourceSnapshot.create(
            self.policy,
            workflow=self.snapshot.workflow,
            content_date=self.snapshot.content_date,
        )
        if expected_snapshot != self.snapshot:
            raise ValueError("source snapshot is not the deterministic policy projection")
        expected_observations = ObservationSet.create(
            self.snapshot,
            self.observation_set.observations,
        )
        if expected_observations != self.observation_set:
            raise ValueError("observation set is not the exact snapshot terminal set")
        if (
            self.source_registry_hash != self.snapshot.source_registry_hash
            or self.source_policy_hash != self.snapshot.source_policy_hash
            or self.source_snapshot_hash != self.snapshot.source_snapshot_hash
            or self.observation_set_hash != self.observation_set.observation_set_hash
            or self.observation_set.source_snapshot_hash != self.source_snapshot_hash
        ):
            raise ValueError("coverage proof hash chain is inconsistent")
        payload: dict[str, Any] = {
            "source_registry_hash": self.source_registry_hash,
            "source_policy_hash": self.source_policy_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "observation_set_hash": self.observation_set_hash,
        }
        if _sha256(payload) != self.coverage_proof_hash:
            raise ValueError("coverage proof hash is invalid")
