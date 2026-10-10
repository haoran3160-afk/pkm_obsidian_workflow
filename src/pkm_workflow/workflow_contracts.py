"""Canonical immutable contracts for the PKM workflow.

The workflow has several adapters (CLI, HTTP, scheduler, Codex), but they all
exchange the models in this module.  Keeping the contracts immutable prevents
an adapter from silently changing authorization, source identity, or run
state after validation.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
REQUIRED_STAGES = ("ingest", "curate", "validate", "commit", "verify")


class ContractModel(BaseModel):
    """Base class for strict, immutable values shared across adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StageStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class RunStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMMITTED = "COMMITTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class PublicationState(str, Enum):
    PENDING = "PENDING__"
    PARTIAL = "PARTIAL__"
    COMMITTED = "COMMITTED"


class SourceHealth(str, Enum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    PROBING = "PROBING"


class FetchReasonCode(str, Enum):
    OK = "OK"
    BOZO = "BOZO"
    HTTP_404 = "HTTP_404"
    HTML_NOT_FEED = "HTML_NOT_FEED"
    SSL_ERROR = "SSL_ERROR"
    TIMEOUT = "TIMEOUT"
    ZERO_YIELD = "ZERO_YIELD"
    NETWORK_ERROR = "NETWORK_ERROR"


class ExecutionMode(str, Enum):
    SHADOW = "shadow"
    OBSERVED = "observed"
    UNATTENDED = "unattended"


class EventType(str, Enum):
    RUN_CREATED = "RUN_CREATED"
    STAGE_CHANGED = "STAGE_CHANGED"
    MODE_TRANSITION = "MODE_TRANSITION"
    GATE_CONSUMED = "GATE_CONSUMED"
    ARTIFACT_RESERVED = "ARTIFACT_RESERVED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    LLM_CALL_RESERVED = "LLM_CALL_RESERVED"
    ABORTED = "ABORTED"


class CommitStatus(str, Enum):
    COMMITTED = "COMMITTED"
    PARTIAL = "PARTIAL"
    ABORTED = "ABORTED"


class CurationDecisionKind(str, Enum):
    SELECTED = "selected"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class IntentKind(str, Enum):
    OFFICIAL_AI = "official_ai"
    ENGINEERING = "engineering"
    RESEARCH = "research"
    MARKET_GROWTH = "market_growth"
    VIDEO = "video"
    IELTS = "ielts"


class DuplicateKind(str, Enum):
    EXACT = "exact"
    CONTENT = "content"


class FeedbackAction(str, Enum):
    PROMOTE = "promote"
    SKIP = "skip"
    REVISIT = "revisit"


class GateKind(str, Enum):
    A = "A"
    B = "B"


class SourceSpec(ContractModel):
    source_id: str = Field(min_length=1)
    legacy_record_id: str = Field(min_length=1)
    observed_record_id: str = Field(min_length=1)
    fetch_target_id: str = Field(min_length=1)
    origin_config_path: str = Field(min_length=1)
    origin_record_index: int = Field(ge=0)
    name: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    enabled: bool
    intent: list[str] = Field(default_factory=list)
    trust_tier: str = Field(min_length=1)
    status: SourceHealth
    endpoint: str = Field(min_length=1)
    endpoint_version: int = Field(ge=1)
    owner: str = Field(min_length=1)
    created_at: datetime

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_http(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("endpoint must use http or https")
        return normalized

    @field_validator("intent")
    @classmethod
    def intent_values_are_non_empty(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("intent values cannot be blank")
        return values


class ObservedSourceRecord(ContractModel):
    observed_record_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    legacy_record_id: str = Field(min_length=1)
    origin_path: str = Field(min_length=1)
    origin_record_index: int = Field(ge=0)
    origin_collection: str = Field(min_length=1)
    raw_record_hash: str = Field(pattern=SHA256_PATTERN)
    name: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    enabled: bool
    first_seen_at: datetime


class FetchTarget(ContractModel):
    fetch_target_id: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    observed_record_ids: list[str] = Field(min_length=1)


class UnresolvedReference(ContractModel):
    reference_id: str = Field(min_length=1)
    origin_object_id: str = Field(min_length=1)
    origin_hash: str = Field(pattern=SHA256_PATTERN)
    value: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SourceBaselineManifest(ContractModel):
    baseline_id: str = Field(min_length=1)
    generated_at: datetime
    observed_sources: list[ObservedSourceRecord] = Field(default_factory=list)
    fetch_targets: list[FetchTarget] = Field(default_factory=list)
    unresolved_references: list[UnresolvedReference] = Field(default_factory=list)
    observed_record_count_by_origin: dict[str, int] = Field(default_factory=dict)
    observed_record_count: int = Field(ge=0)
    fetchable_source_count: int = Field(ge=0)
    disabled_source_count: int = Field(ge=0)
    unresolved_reference_count: int = Field(ge=0)
    universe_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def declared_counts_match_records(self) -> SourceBaselineManifest:
        expected = {
            "observed_record_count": len(self.observed_sources),
            "fetchable_source_count": len(self.fetch_targets),
            "disabled_source_count": sum(not record.enabled for record in self.observed_sources),
            "unresolved_reference_count": len(self.unresolved_references),
        }
        mismatches = [name for name, value in expected.items() if getattr(self, name) != value]
        if mismatches:
            raise ValueError("baseline count mismatch: " + ", ".join(mismatches))
        if sum(self.observed_record_count_by_origin.values()) != len(self.observed_sources):
            raise ValueError("observed_record_count_by_origin does not match records")
        return self


class PathIdentityRecord(ContractModel):
    relative_path: str = Field(min_length=1)
    is_directory: bool
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    device_id: int = Field(ge=0)
    file_id: int = Field(ge=0)


class VaultBaselineManifest(ContractModel):
    vault_root: str = Field(min_length=1)
    captured_at: datetime
    entries: list[PathIdentityRecord]
    multiset_hash: str = Field(pattern=SHA256_PATTERN)


class RawItem(ContractModel):
    item_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    guid: str = ""
    title: str = Field(min_length=1)
    published_at: datetime | None = None
    fetched_at: datetime
    content_hash: str = Field(pattern=SHA256_PATTERN)
    raw_ref: str = Field(min_length=1)
    evidence_spans: list[str] = Field(default_factory=list)

    @field_validator("canonical_url")
    @classmethod
    def canonical_url_must_be_http(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("canonical_url must use http or https")
        return normalized


class FetchDiagnostic(ContractModel):
    reason_code: FetchReasonCode
    retryable: bool
    yield_count: int = Field(ge=0)
    detail: str = ""


class PortfolioCandidate(ContractModel):
    item_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    content_type: str = Field(min_length=1)
    intents: list[IntentKind] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)

    @field_validator("canonical_url")
    @classmethod
    def portfolio_url_must_be_http(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("canonical_url must use http or https")
        return normalized

    @field_validator("intents", "evidence_refs")
    @classmethod
    def portfolio_lists_are_unique(cls, values: list[Any]) -> list[Any]:
        if len(values) != len(set(values)):
            raise ValueError("portfolio list values must be unique")
        return values


class IntentPortfolio(ContractModel):
    pools: dict[str, list[PortfolioCandidate]]
    all_item_ids: list[str]

    @model_validator(mode="after")
    def pools_preserve_every_candidate(self) -> IntentPortfolio:
        allowed = {intent.value for intent in IntentKind}
        if set(self.pools) != allowed:
            raise ValueError("portfolio must declare every intent pool")
        if len(self.all_item_ids) != len(set(self.all_item_ids)):
            raise ValueError("all_item_ids cannot contain duplicates")
        observed: set[str] = set()
        for pool_name, candidates in self.pools.items():
            for candidate in candidates:
                if IntentKind(pool_name) not in candidate.intents:
                    raise ValueError("candidate is present in an undeclared intent")
                observed.add(candidate.item_id)
        if observed != set(self.all_item_ids):
            raise ValueError("portfolio pools must preserve every candidate")
        return self


class DuplicateRecord(ContractModel):
    cluster_id: str = Field(min_length=1)
    duplicate_kind: DuplicateKind
    member_item_ids: list[str] = Field(min_length=2)
    representative_item_id: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def representative_is_a_member(self) -> DuplicateRecord:
        if self.representative_item_id not in self.member_item_ids:
            raise ValueError("duplicate representative must be a member")
        return self


class SemanticClusterProposal(ContractModel):
    cluster_id: str = Field(min_length=1)
    member_item_ids: list[str] = Field(min_length=1)
    representative_item_id: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)

    @model_validator(mode="after")
    def representative_is_a_semantic_member(self) -> SemanticClusterProposal:
        if len(self.member_item_ids) != len(set(self.member_item_ids)):
            raise ValueError("semantic cluster members must be unique")
        if self.representative_item_id not in self.member_item_ids:
            raise ValueError("semantic representative must be a member")
        return self


class CurationEvidence(ContractModel):
    evidence_id: str = Field(min_length=1)
    raw_item_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    raw_ref: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("canonical_url")
    @classmethod
    def evidence_url_must_be_http(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("evidence URL must use http or https")
        return normalized


class CurationClaim(ContractModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def claim_evidence_is_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("claim evidence refs must be unique")
        return values


class CurationSection(ContractModel):
    slot_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    claims: list[CurationClaim] = Field(min_length=1)


class CurationPlan(ContractModel):
    run_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    input_hash: str = Field(pattern=SHA256_PATTERN)
    evidence: list[CurationEvidence] = Field(min_length=1)
    sections: list[CurationSection] = Field(min_length=1)

    @model_validator(mode="after")
    def every_claim_is_grounded(self) -> CurationPlan:
        evidence_by_id = {evidence.evidence_id: evidence for evidence in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        claim_ids: set[str] = set()
        number_pattern = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?")
        for section in self.sections:
            for claim in section.claims:
                if claim.claim_id in claim_ids:
                    raise ValueError("claim IDs must be unique")
                claim_ids.add(claim.claim_id)
                unknown = set(claim.evidence_refs) - set(evidence_by_id)
                if unknown:
                    raise ValueError("claim contains an unknown evidence ref")
                referenced_text = "\n".join(
                    evidence_by_id[ref].excerpt for ref in claim.evidence_refs
                )
                unsupported = [
                    number
                    for number in number_pattern.findall(claim.text)
                    if number not in referenced_text
                ]
                if unsupported:
                    raise ValueError("claim contains an exact number absent from evidence")
        return self


class CurationDecision(ContractModel):
    run_id: str = Field(min_length=1)
    item_ids: list[str] = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    decision: CurationDecisionKind
    reason_codes: list[str] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class NoteDraft(ContractModel):
    run_id: str = Field(min_length=1)
    logical_collection: str = Field(min_length=1)
    target_path: str = Field(min_length=1)
    version: int = Field(ge=1)
    body_hash: str = Field(pattern=SHA256_PATTERN)
    source_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    review_state: str = Field(min_length=1)


class CommitFileResult(ContractModel):
    path: str = Field(min_length=1)
    hash: str = Field(pattern=SHA256_PATTERN)
    write_status: PublicationState


class CommitResult(ContractModel):
    run_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    status: CommitStatus
    files: list[CommitFileResult] = Field(default_factory=list)
    commit_marker: str | None = None
    checkpoint_status: str = Field(min_length=1)
    errors: list[str] = Field(default_factory=list)


class AuthorizationContext(ContractModel):
    effective_mode: ExecutionMode
    mode_epoch: str = Field(min_length=1)
    release_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    spec_hash: str = Field(pattern=SHA256_PATTERN)
    config_hash: str = Field(pattern=SHA256_PATTERN)
    source_baseline_hash: str = Field(pattern=SHA256_PATTERN)
    gate_receipt_id: str | None = None
    gate_consumption_id: str | None = None
    authorization_lineage_id: str = Field(min_length=1)
    parent_run_id: str | None = None

    @model_validator(mode="after")
    def privileged_modes_require_gate_lineage(self) -> AuthorizationContext:
        if self.effective_mode is not ExecutionMode.SHADOW:
            if not self.gate_receipt_id or not self.gate_consumption_id:
                raise ValueError("observed and unattended modes require consumed Gate evidence")
        return self


class RunRef(ContractModel):
    run_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)
    command: str = Field(min_length=1)
    authorization: AuthorizationContext
    incident_root_run_id: str | None = None
    recovers_run_id: str | None = None


class RunEvent(ContractModel):
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    event_type: EventType
    created_at: datetime
    stage: str | None = None
    stage_status: StageStatus | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunView(ContractModel):
    ref: RunRef
    status: RunStatus
    stages: dict[str, StageStatus] = Field(default_factory=dict)
    events: tuple[RunEvent, ...] = ()


class FeedbackReceipt(ContractModel):
    feedback_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    action: FeedbackAction
    idempotency_key: str = Field(min_length=1)
    reason: str = ""
    recorded_at: datetime
    event_path: str = Field(min_length=1)
    event_hash: str = Field(pattern=SHA256_PATTERN)


class SafetyInputs(ContractModel):
    observed_sources_before: int = Field(default=0, ge=0)
    observed_sources_after: int = Field(default=0, ge=0)
    fetchable_sources_before: int = Field(default=0, ge=0)
    fetchable_sources_after: int = Field(default=0, ge=0)
    disabled_sources_before: int = Field(default=0, ge=0)
    disabled_sources_after: int = Field(default=0, ge=0)
    unresolved_sources_before: int = Field(default=0, ge=0)
    unresolved_sources_after: int = Field(default=0, ge=0)
    existing_delete_count: int = Field(default=0, ge=0)
    existing_move_count: int = Field(default=0, ge=0)
    overwrite_count: int = Field(default=0, ge=0)
    out_of_scope_write_count: int = Field(default=0, ge=0)
    new_artifact_disappear_count: int = Field(default=0, ge=0)
    new_artifact_move_count: int = Field(default=0, ge=0)
    new_artifact_truncate_count: int = Field(default=0, ge=0)
    monitor_overflow_count: int = Field(default=0, ge=0)
    monitor_gap_count: int = Field(default=0, ge=0)


class VerificationResult(ContractModel):
    run_id: str = Field(min_length=1)
    safe: bool
    safety_summary: dict[str, int] = Field(default_factory=dict)
    overall_status: RunStatus
    persisted: bool
    event_id: str | None = None


class GateResult(ContractModel):
    requested_mode: ExecutionMode
    allowed: bool
    reasons: tuple[str, ...] = ()


class FinalManifest(ContractModel):
    run_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)
    started_at: datetime
    stages: dict[str, StageStatus]
    counts: dict[str, int] = Field(default_factory=dict)
    durations: dict[str, float] = Field(default_factory=dict)
    source_health_summary: dict[str, int] = Field(default_factory=dict)
    input_generation: str | None = None
    output_generation: str | None = None
    overall_status: RunStatus
    authorization: AuthorizationContext

    @model_validator(mode="after")
    def committed_requires_successful_required_stages(self) -> FinalManifest:
        if self.overall_status is RunStatus.COMMITTED:
            invalid = [
                stage
                for stage in REQUIRED_STAGES
                if self.stages.get(stage) is not StageStatus.SUCCEEDED
            ]
            if invalid:
                raise ValueError(
                    "COMMITTED final manifest requires successful required stages: "
                    + ", ".join(invalid)
                )
        return self


class CommandEnvelope(ContractModel):
    command: str = Field(min_length=1)
    run_id: str | None = None
    input_run_id: str | None = None
    incident_root_run_id: str | None = None
    recovers_run_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    status: RunStatus
    exit_code: int
    manifest_path: str | None = None
    manifest_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    safety_summary: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class GateChallenge(ContractModel):
    challenge_id: str = Field(min_length=1)
    gate: GateKind
    nonce: str = Field(min_length=16)
    phrase_hash: str = Field(pattern=SHA256_PATTERN)
    baseline_hash: str = Field(pattern=SHA256_PATTERN)
    evidence_hash: str = Field(pattern=SHA256_PATTERN)
    release_hash: str = Field(pattern=SHA256_PATTERN)
    spec_hash: str = Field(pattern=SHA256_PATTERN)
    config_hash: str = Field(pattern=SHA256_PATTERN)
    source_baseline_hash: str = Field(pattern=SHA256_PATTERN)
    expires_at: datetime


class GateReceipt(ContractModel):
    receipt_id: str = Field(min_length=1)
    challenge_id: str = Field(min_length=1)
    gate: GateKind
    windows_sid_hash: str = Field(pattern=SHA256_PATTERN)
    windows_account_hash: str = Field(pattern=SHA256_PATTERN)
    challenge_hash: str = Field(pattern=SHA256_PATTERN)
    usage_limit: Literal[1] = 1
    issued_at: datetime


class ReleaseInputFile(ContractModel):
    role: str = Field(min_length=1)
    absolute_path: str = Field(min_length=1)
    snapshot_path: str | None = None
    size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    device_id: int = Field(ge=0)
    file_id: int = Field(ge=0)


class ReleaseInputDescriptor(ContractModel):
    schema_version: Literal[1] = 1
    attempt_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    ui_enabled: bool
    inputs: list[ReleaseInputFile] = Field(min_length=1)
    relative_command_manifest_path: str = Field(min_length=1)
    relative_command_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class ReleaseSnapshotFile(ContractModel):
    relative_path: str = Field(min_length=1)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)


class ReleaseManifest(ContractModel):
    schema_version: Literal[1] = 1
    release_hash: str = Field(pattern=SHA256_PATTERN)
    attempt_id: str = Field(min_length=1)
    snapshot_root: str = Field(min_length=1)
    entrypoint_path: str = Field(min_length=1)
    harness_path: str = Field(min_length=1)
    command_manifest_path: str = Field(min_length=1)
    source_hash: str = Field(pattern=SHA256_PATTERN)
    spec_hash: str = Field(pattern=SHA256_PATTERN)
    config_hash: str = Field(pattern=SHA256_PATTERN)
    source_baseline_hash: str = Field(pattern=SHA256_PATTERN)
    interpreter_hash: str = Field(pattern=SHA256_PATTERN)
    node_hash: str = Field(pattern=SHA256_PATTERN)
    lockfile_hashes: dict[str, str]
    files: list[ReleaseSnapshotFile] = Field(min_length=1)
    ui_enabled: bool
    sealed: bool
    created_at: datetime


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.PARTIAL,
        RunStatus.COMMITTED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    }
)


def assert_run_transition(current: RunStatus, target: RunStatus) -> None:
    """Reject mutation of a terminal run; recovery must create a new run."""

    if current in TERMINAL_RUN_STATUSES:
        raise ValueError(f"run status {current.value} is terminal")
    if current is target:
        return
    if current is not RunStatus.PENDING:
        raise ValueError(f"unsupported run transition: {current.value} -> {target.value}")


for _publication_state in PublicationState:
    if len(_publication_state.value.encode("ascii")) != 9:
        raise RuntimeError("publication state slots must be exactly 9 ASCII bytes")
