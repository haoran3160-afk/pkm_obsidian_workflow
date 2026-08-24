"""Fail-closed T1.3b Generator -> Reviewer qualification orchestration.

This module is deliberately not a process launcher.  A future native adapter
must implement :class:`NativeQualifiedInvoker` and provide content-addressed
manifest and attestation bytes.  Without that adapter the only executable
state is ``BLOCKED_PREQUALIFICATION``.  Synthetic adapters can exercise the
same parsing chain, but can never produce a live-qualified result.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import NoReturn, Protocol

from .codex_runner import (
    CodexRole,
    CodexToolEventError,
    CodexUsage,
    StrictCodexAuditResult,
    audit_codex_jsonl_strict,
)
from .curation import CurationResult
from .model_contract import (
    GENERATOR_MODEL,
    REVIEWER_MODEL,
    DecisionSet,
    ModelRole,
    SealedModelInput,
    parse_generator_output,
    parse_reviewer_output,
    render_model_stdin,
    seal_generator_input,
    seal_reviewer_input,
)
from .provenance import canonical_json_bytes
from .review_contract import ReviewContract, ReviewRequirement

NATIVE_REQUEST_SCHEMA = "t1.3b-native-request-v1"
NATIVE_MANIFEST_SCHEMA = "t1.3b-native-invocation-manifest-v1"
NATIVE_ATTESTATION_SCHEMA = "t1.3b-native-attestation-envelope-v1"
MAX_NATIVE_STDIN_BYTES = 500_000
MAX_NATIVE_EVIDENCE_BYTES = 256_000
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
THREAD_PATTERN = UUID_PATTERN
ZERO_HASH = "sha256:" + "0" * 64


class NativeFailureCode(str, Enum):
    BLOCKED_PREQUALIFICATION = "BLOCKED_PREQUALIFICATION"
    TIMEOUT = "TIMEOUT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    QUOTA_DELAYED = "QUOTA_DELAYED"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"
    PROCESS_FAILED = "PROCESS_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class QualificationOutcome(str, Enum):
    BLOCKED_PREQUALIFICATION = "BLOCKED_PREQUALIFICATION"
    GENERATOR_ABSTAINED_QUALIFICATION_INCOMPLETE = (
        "GENERATOR_ABSTAINED_QUALIFICATION_INCOMPLETE"
    )
    SYNTHETIC_CHAIN_PASSED = "SYNTHETIC_CHAIN_PASSED"
    CHAIN_FAILED = "CHAIN_FAILED"


class JobFailureCode(str, Enum):
    NATIVE_SEAM_BLOCKED = "NATIVE_SEAM_BLOCKED"
    NATIVE_TIMEOUT = "NATIVE_TIMEOUT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    QUOTA_DELAYED = "QUOTA_DELAYED"
    NATIVE_SECURITY_BLOCKED = "NATIVE_SECURITY_BLOCKED"
    NATIVE_PROCESS_FAILED = "NATIVE_PROCESS_FAILED"
    NATIVE_EVIDENCE_INVALID = "NATIVE_EVIDENCE_INVALID"
    NATIVE_ATTESTATION_REJECTED = "NATIVE_ATTESTATION_REJECTED"
    NATIVE_CLOSED_ADAPTER_NOT_INTEGRATED = "NATIVE_CLOSED_ADAPTER_NOT_INTEGRATED"
    SYNTHETIC_NATIVE_CLAIM = "SYNTHETIC_NATIVE_CLAIM"
    SEALED_INPUT_REJECTED = "SEALED_INPUT_REJECTED"
    MODEL_EVENT_AUDIT_REJECTED = "MODEL_EVENT_AUDIT_REJECTED"
    GENERATOR_OUTPUT_REJECTED = "GENERATOR_OUTPUT_REJECTED"
    GENERATOR_ABSTAINED = "GENERATOR_ABSTAINED"
    REVIEW_CONTRACT_REJECTED = "REVIEW_CONTRACT_REJECTED"
    REVIEWER_THREAD_NOT_FRESH = "REVIEWER_THREAD_NOT_FRESH"
    REVIEW_DECISION_MISMATCH = "REVIEW_DECISION_MISMATCH"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class NativeInvocationFailure(RuntimeError):
    """Typed failure emitted by the native seam without untrusted detail."""

    def __init__(self, code: NativeFailureCode) -> None:
        if not isinstance(code, NativeFailureCode):
            raise TypeError("native invocation failure code must be typed")
        self.code = code
        super().__init__(code.value)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _require_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical SHA-256 digest")
    return value


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _canonical_object(document: bytes, label: str) -> dict[str, object]:
    if type(document) is not bytes or len(document) > MAX_NATIVE_EVIDENCE_BYTES:
        raise ValueError(f"{label} byte representation is invalid")
    try:
        value = json.loads(
            document.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != document:
        raise ValueError(f"{label} must be one canonical JSON object")
    return value


@dataclass(frozen=True)
class NativeInvocationRequest:
    """The complete byte-level request handed to the native qualification TCB."""

    role: CodexRole
    model: str
    prompt_hash: str
    schema_hash: str
    input_hash: str
    schema_json: bytes = field(repr=False)
    stdin_bytes: bytes = field(repr=False)
    run_id: str
    run_nonce_hash: str
    invocation_seq: int
    parent_receipt_hash: str
    previous_thread_id: str | None
    fresh_session_required: bool = True
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        expected_model = {
            CodexRole.GENERATOR: GENERATOR_MODEL,
            CodexRole.REVIEWER: REVIEWER_MODEL,
        }.get(self.role)
        if expected_model is None or self.model != expected_model:
            raise ValueError("native request role/model binding is invalid")
        for field_name in ("prompt_hash", "schema_hash", "input_hash"):
            _require_hash(getattr(self, field_name), field_name)
        if UUID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("native request run_id is invalid")
        _require_hash(self.run_nonce_hash, "run_nonce_hash")
        _require_hash(self.parent_receipt_hash, "parent_receipt_hash")
        if self.run_nonce_hash == ZERO_HASH:
            raise ValueError("native request nonce hash must be non-zero")
        expected_seq = 1 if self.role is CodexRole.GENERATOR else 2
        if type(self.invocation_seq) is not int or self.invocation_seq != expected_seq:
            raise ValueError("native request invocation sequence is invalid")
        if self.role is CodexRole.GENERATOR and self.parent_receipt_hash != ZERO_HASH:
            raise ValueError("Generator request cannot have a parent receipt")
        if self.role is CodexRole.REVIEWER and self.parent_receipt_hash == ZERO_HASH:
            raise ValueError("Reviewer request must bind the Generator receipt")
        if type(self.schema_json) is not bytes or _digest(self.schema_json) != self.schema_hash:
            raise ValueError("native request schema bytes do not match schema_hash")
        if (
            type(self.stdin_bytes) is not bytes
            or not self.stdin_bytes
            or len(self.stdin_bytes) > MAX_NATIVE_STDIN_BYTES
        ):
            raise ValueError("native request stdin bytes are invalid")
        if self.fresh_session_required is not True:
            raise ValueError("every Codex invocation must require a fresh session")
        if self.role is CodexRole.GENERATOR and self.previous_thread_id is not None:
            raise ValueError("Generator request cannot inherit a previous thread")
        if self.role is CodexRole.REVIEWER and (
            not isinstance(self.previous_thread_id, str)
            or THREAD_PATTERN.fullmatch(self.previous_thread_id) is None
        ):
            raise ValueError("Reviewer request must forbid the exact Generator thread")
        object.__setattr__(self, "request_hash", _digest(canonical_json_bytes(self.payload())))

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": NATIVE_REQUEST_SCHEMA,
            "role": self.role.value,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "schema_hash": self.schema_hash,
            "schema_bytes": len(self.schema_json),
            "input_hash": self.input_hash,
            "stdin_hash": _digest(self.stdin_bytes),
            "stdin_bytes": len(self.stdin_bytes),
            "run_id": self.run_id,
            "run_nonce_hash": self.run_nonce_hash,
            "invocation_seq": self.invocation_seq,
            "parent_receipt_hash": self.parent_receipt_hash,
            "previous_thread_id": self.previous_thread_id,
            "fresh_session_required": self.fresh_session_required,
        }


@dataclass(frozen=True)
class NativeInvocationEvidence:
    """Raw stdout plus content-addressed native manifest and attestation."""

    stdout_bytes: bytes = field(repr=False)
    stderr_bytes: bytes = field(repr=False)
    manifest_json: bytes = field(repr=False)
    manifest_hash: str
    attestation_json: bytes = field(repr=False)
    attestation_hash: str
    synthetic: bool

    @classmethod
    def create_synthetic(
        cls,
        *,
        request: NativeInvocationRequest,
        stdout_bytes: bytes,
        stderr_bytes: bytes = b"",
        qualifier_release_hash: str,
        isolation_evidence_hash: str,
        file_trace_hash: str,
        network_trace_hash: str,
        process_outcome_hash: str,
        proxy_terminal_hash: str,
        joint_terminal_hash: str,
        stdout_artifact_identity_hash: str,
        windows_build_hash: str,
        official_binary_identity_hash: str,
        package_sid_hash: str,
        wfp_policy_hash: str,
    ) -> NativeInvocationEvidence:
        """Create explicitly non-live evidence for deterministic offline tests."""

        for field_name, value in (
            ("qualifier_release_hash", qualifier_release_hash),
            ("isolation_evidence_hash", isolation_evidence_hash),
            ("file_trace_hash", file_trace_hash),
            ("network_trace_hash", network_trace_hash),
            ("process_outcome_hash", process_outcome_hash),
            ("proxy_terminal_hash", proxy_terminal_hash),
            ("joint_terminal_hash", joint_terminal_hash),
            ("stdout_artifact_identity_hash", stdout_artifact_identity_hash),
            ("windows_build_hash", windows_build_hash),
            ("official_binary_identity_hash", official_binary_identity_hash),
            ("package_sid_hash", package_sid_hash),
            ("wfp_policy_hash", wfp_policy_hash),
        ):
            _require_hash(value, field_name)
        if type(stdout_bytes) is not bytes or type(stderr_bytes) is not bytes:
            raise TypeError("synthetic process output must remain raw bytes")
        qualification_terminal = "CONTRACT_HARNESS_COMPLETED_UNQUALIFIED"
        manifest = canonical_json_bytes(
            {
                "schema_version": NATIVE_MANIFEST_SCHEMA,
                "run_id": request.run_id,
                "run_nonce_hash": request.run_nonce_hash,
                "invocation_seq": request.invocation_seq,
                "parent_receipt_hash": request.parent_receipt_hash,
                "request_hash": request.request_hash,
                "role": request.role.value,
                "model": request.model,
                "prompt_hash": request.prompt_hash,
                "schema_hash": request.schema_hash,
                "input_hash": request.input_hash,
                "stdin_hash": _digest(request.stdin_bytes),
                "stdout_hash": _digest(stdout_bytes),
                "stdout_bytes": len(stdout_bytes),
                "stderr_hash": _digest(stderr_bytes),
                "stderr_bytes": len(stderr_bytes),
                "exit_code": 0,
                "stdout_overflow": False,
                "stderr_overflow": False,
                "job_drained": True,
                "descendants_remaining": 0,
                "qualification_terminal": qualification_terminal,
                "synthetic": True,
            }
        )
        manifest_hash = _digest(manifest)
        closure_payload = {
            "manifest_hash": manifest_hash,
            "qualifier_release_hash": qualifier_release_hash,
            "isolation_evidence_hash": isolation_evidence_hash,
            "file_trace_hash": file_trace_hash,
            "network_trace_hash": network_trace_hash,
            "process_outcome_hash": process_outcome_hash,
            "proxy_terminal_hash": proxy_terminal_hash,
            "joint_terminal_hash": joint_terminal_hash,
            "stdout_artifact_identity_hash": stdout_artifact_identity_hash,
            "windows_build_hash": windows_build_hash,
            "official_binary_identity_hash": official_binary_identity_hash,
            "package_sid_hash": package_sid_hash,
            "wfp_policy_hash": wfp_policy_hash,
            "qualification_terminal": qualification_terminal,
        }
        attestation = canonical_json_bytes(
            {
                "schema_version": NATIVE_ATTESTATION_SCHEMA,
                "request_hash": request.request_hash,
                **closure_payload,
                "result_closure_hash": _digest(canonical_json_bytes(closure_payload)),
            }
        )
        result = cls(
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            manifest_json=manifest,
            manifest_hash=manifest_hash,
            attestation_json=attestation,
            attestation_hash=_digest(attestation),
            synthetic=True,
        )
        result.validate(request)
        return result

    @classmethod
    def from_native_bytes(
        cls,
        *,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
        manifest_json: bytes,
        attestation_json: bytes,
    ) -> NativeInvocationEvidence:
        """Wrap untrusted native-shaped bytes; this does not confer live authority."""

        manifest = _canonical_object(manifest_json, "native manifest")
        synthetic = manifest.get("synthetic")
        if type(synthetic) is not bool:
            raise ValueError("native manifest synthetic marker is invalid")
        return cls(
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            manifest_json=manifest_json,
            manifest_hash=_digest(manifest_json),
            attestation_json=attestation_json,
            attestation_hash=_digest(attestation_json),
            synthetic=synthetic,
        )

    def validate(self, request: NativeInvocationRequest) -> None:
        if type(self.stdout_bytes) is not bytes or type(self.stderr_bytes) is not bytes:
            raise TypeError("native process output must remain raw bytes")
        if type(self.synthetic) is not bool:
            raise ValueError("native evidence synthetic marker must be boolean")
        _require_hash(self.manifest_hash, "manifest_hash")
        _require_hash(self.attestation_hash, "attestation_hash")
        if _digest(self.manifest_json) != self.manifest_hash:
            raise ValueError("native manifest content hash is invalid")
        if _digest(self.attestation_json) != self.attestation_hash:
            raise ValueError("native attestation content hash is invalid")
        _canonical_object(self.manifest_json, "native manifest")
        qualification_terminal = (
            "CONTRACT_HARNESS_COMPLETED_UNQUALIFIED"
            if self.synthetic
            else "NATIVE_QUALIFICATION_SUCCEEDED"
        )
        expected_manifest = {
            "schema_version": NATIVE_MANIFEST_SCHEMA,
            "run_id": request.run_id,
            "run_nonce_hash": request.run_nonce_hash,
            "invocation_seq": request.invocation_seq,
            "parent_receipt_hash": request.parent_receipt_hash,
            "request_hash": request.request_hash,
            "role": request.role.value,
            "model": request.model,
            "prompt_hash": request.prompt_hash,
            "schema_hash": request.schema_hash,
            "input_hash": request.input_hash,
            "stdin_hash": _digest(request.stdin_bytes),
            "stdout_hash": _digest(self.stdout_bytes),
            "stdout_bytes": len(self.stdout_bytes),
            "stderr_hash": _digest(self.stderr_bytes),
            "stderr_bytes": len(self.stderr_bytes),
            "exit_code": 0,
            "stdout_overflow": False,
            "stderr_overflow": False,
            "job_drained": True,
            "descendants_remaining": 0,
            "qualification_terminal": qualification_terminal,
            "synthetic": self.synthetic,
        }
        if self.manifest_json != canonical_json_bytes(expected_manifest):
            raise ValueError("native manifest is not exactly bound to the invocation")
        attestation = _canonical_object(self.attestation_json, "native attestation")
        closure_fields = (
            "manifest_hash",
            "qualifier_release_hash",
            "isolation_evidence_hash",
            "file_trace_hash",
            "network_trace_hash",
            "process_outcome_hash",
            "proxy_terminal_hash",
            "joint_terminal_hash",
            "stdout_artifact_identity_hash",
            "windows_build_hash",
            "official_binary_identity_hash",
            "package_sid_hash",
            "wfp_policy_hash",
        )
        expected_fields = {
            "schema_version",
            "request_hash",
            "qualification_terminal",
            "result_closure_hash",
            *closure_fields,
        }
        if set(attestation) != expected_fields:
            raise ValueError("native attestation fields are not exact")
        for field_name in closure_fields:
            _require_hash(attestation[field_name], field_name)
        closure_payload = {field_name: attestation[field_name] for field_name in closure_fields}
        closure_payload["qualification_terminal"] = qualification_terminal
        expected_attestation = {
            "schema_version": NATIVE_ATTESTATION_SCHEMA,
            "request_hash": request.request_hash,
            **closure_payload,
            "result_closure_hash": _digest(canonical_json_bytes(closure_payload)),
        }
        if self.attestation_json != canonical_json_bytes(expected_attestation):
            raise ValueError("native attestation is not the exact result closure")


@dataclass(frozen=True)
class AttestationValidation:
    """Decision produced by the separately trusted native evidence validator."""

    accepted: bool
    request_hash: str
    manifest_hash: str
    attestation_hash: str
    validator_policy_hash: str
    blocker_codes: tuple[str, ...]

    def validate(
        self,
        *,
        request: NativeInvocationRequest,
        evidence: NativeInvocationEvidence,
    ) -> None:
        if type(self.accepted) is not bool:
            raise ValueError("attestation validation flag must be boolean")
        for field_name in (
            "request_hash",
            "manifest_hash",
            "attestation_hash",
            "validator_policy_hash",
        ):
            _require_hash(getattr(self, field_name), field_name)
        if (
            self.request_hash != request.request_hash
            or self.manifest_hash != evidence.manifest_hash
            or self.attestation_hash != evidence.attestation_hash
        ):
            raise ValueError("attestation validator decision is not content-addressed")
        if (
            type(self.blocker_codes) is not tuple
            or len(set(self.blocker_codes)) != len(self.blocker_codes)
            or any(not isinstance(code, str) or not code for code in self.blocker_codes)
        ):
            raise ValueError("attestation blocker codes are invalid")
        if self.accepted and self.blocker_codes:
            raise ValueError("accepted attestation cannot retain blockers")
        if not self.accepted and not self.blocker_codes:
            raise ValueError("rejected attestation must state a blocker")


class NativeQualifiedInvoker(Protocol):
    """Contract-harness seam; injectable implementations are never live authority."""

    @property
    def is_synthetic(self) -> bool: ...

    def invoke(self, request: NativeInvocationRequest) -> NativeInvocationEvidence: ...


class NativeAttestationValidator(Protocol):
    """Validate harness evidence; it cannot authorize live qualification."""

    def validate(
        self,
        *,
        request: NativeInvocationRequest,
        evidence: NativeInvocationEvidence,
    ) -> AttestationValidation: ...


class _BlockedNativeInvoker:
    @property
    def is_synthetic(self) -> bool:
        return False

    def invoke(self, request: NativeInvocationRequest) -> NativeInvocationEvidence:
        del request
        raise NativeInvocationFailure(NativeFailureCode.BLOCKED_PREQUALIFICATION)


class _BlockedAttestationValidator:
    def validate(
        self,
        *,
        request: NativeInvocationRequest,
        evidence: NativeInvocationEvidence,
    ) -> AttestationValidation:
        return AttestationValidation(
            accepted=False,
            request_hash=request.request_hash,
            manifest_hash=evidence.manifest_hash,
            attestation_hash=evidence.attestation_hash,
            validator_policy_hash="sha256:" + "0" * 64,
            blocker_codes=("NATIVE_ATTESTATION_VALIDATOR_NOT_INTEGRATED",),
        )


class InvocationReceiptTerminal(str, Enum):
    NATIVE_EVIDENCE_REJECTED = "NATIVE_EVIDENCE_REJECTED"
    NATIVE_ATTESTATION_REJECTED = "NATIVE_ATTESTATION_REJECTED"
    NATIVE_CLOSED_ADAPTER_REJECTED = "NATIVE_CLOSED_ADAPTER_REJECTED"
    MODEL_AUDITED = "MODEL_AUDITED"
    MODEL_EVENT_AUDIT_REJECTED = "MODEL_EVENT_AUDIT_REJECTED"


@dataclass(frozen=True)
class InvocationReceipt:
    """Non-content audit receipt retained even when a later stage fails."""

    role: CodexRole
    run_id: str
    invocation_seq: int
    parent_receipt_hash: str
    request_hash: str
    manifest_hash: str
    attestation_hash: str
    stdout_hash: str
    stdout_bytes: int
    stderr_hash: str
    stderr_bytes: int
    synthetic: bool
    terminal: InvocationReceiptTerminal
    thread_id: str | None
    usage: CodexUsage | None
    event_count: int | None
    reasoning_item_count: int | None
    tool_event_count: int | None
    orchestration_elapsed_ns: int
    receipt_hash: str

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def create(
        cls,
        *,
        request: NativeInvocationRequest,
        evidence: NativeInvocationEvidence,
        terminal: InvocationReceiptTerminal,
        audit: StrictCodexAuditResult | None,
        orchestration_elapsed_ns: int,
    ) -> InvocationReceipt:
        audited = terminal is InvocationReceiptTerminal.MODEL_AUDITED
        if audited != (audit is not None):
            raise ValueError("receipt terminal and audit presence differ")
        if audit is not None and audit.role is not request.role:
            raise ValueError("receipt audit role differs from request")
        values: dict[str, object] = {
            "role": request.role,
            "run_id": request.run_id,
            "invocation_seq": request.invocation_seq,
            "parent_receipt_hash": request.parent_receipt_hash,
            "request_hash": request.request_hash,
            "manifest_hash": _digest(evidence.manifest_json),
            "attestation_hash": _digest(evidence.attestation_json),
            "stdout_hash": _digest(evidence.stdout_bytes),
            "stdout_bytes": len(evidence.stdout_bytes),
            "stderr_hash": _digest(evidence.stderr_bytes),
            "stderr_bytes": len(evidence.stderr_bytes),
            "synthetic": evidence.synthetic,
            "terminal": terminal,
            "thread_id": audit.thread_id if audit is not None else None,
            "usage": audit.usage if audit is not None else None,
            "event_count": audit.event_count if audit is not None else None,
            "reasoning_item_count": audit.reasoning_item_count if audit is not None else None,
            "tool_event_count": audit.tool_event_count if audit is not None else None,
            "orchestration_elapsed_ns": orchestration_elapsed_ns,
        }
        payload = cls._payload_from_values(values)
        result = cls(
            role=request.role,
            run_id=request.run_id,
            invocation_seq=request.invocation_seq,
            parent_receipt_hash=request.parent_receipt_hash,
            request_hash=request.request_hash,
            manifest_hash=_digest(evidence.manifest_json),
            attestation_hash=_digest(evidence.attestation_json),
            stdout_hash=_digest(evidence.stdout_bytes),
            stdout_bytes=len(evidence.stdout_bytes),
            stderr_hash=_digest(evidence.stderr_bytes),
            stderr_bytes=len(evidence.stderr_bytes),
            synthetic=evidence.synthetic,
            terminal=terminal,
            thread_id=audit.thread_id if audit is not None else None,
            usage=audit.usage if audit is not None else None,
            event_count=audit.event_count if audit is not None else None,
            reasoning_item_count=audit.reasoning_item_count if audit is not None else None,
            tool_event_count=audit.tool_event_count if audit is not None else None,
            orchestration_elapsed_ns=orchestration_elapsed_ns,
            receipt_hash=_digest(canonical_json_bytes(payload)),
        )
        return result

    @staticmethod
    def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
        usage_value = values["usage"]
        usage = None
        if isinstance(usage_value, CodexUsage):
            usage = {
                "input_tokens": usage_value.input_tokens,
                "cached_input_tokens": usage_value.cached_input_tokens,
                "cache_write_input_tokens": usage_value.cache_write_input_tokens,
                "output_tokens": usage_value.output_tokens,
                "reasoning_output_tokens": usage_value.reasoning_output_tokens,
            }
        role = values["role"]
        terminal = values["terminal"]
        return {
            "role": role.value if isinstance(role, CodexRole) else role,
            "run_id": values["run_id"],
            "invocation_seq": values["invocation_seq"],
            "parent_receipt_hash": values["parent_receipt_hash"],
            "request_hash": values["request_hash"],
            "manifest_hash": values["manifest_hash"],
            "attestation_hash": values["attestation_hash"],
            "stdout_hash": values["stdout_hash"],
            "stdout_bytes": values["stdout_bytes"],
            "stderr_hash": values["stderr_hash"],
            "stderr_bytes": values["stderr_bytes"],
            "synthetic": values["synthetic"],
            "terminal": terminal.value if isinstance(terminal, InvocationReceiptTerminal) else terminal,
            "thread_id": values["thread_id"],
            "usage": usage,
            "event_count": values["event_count"],
            "reasoning_item_count": values["reasoning_item_count"],
            "tool_event_count": values["tool_event_count"],
            "orchestration_elapsed_ns": values["orchestration_elapsed_ns"],
        }

    def payload(self) -> dict[str, object]:
        return self._payload_from_values(
            {
                "role": self.role,
                "run_id": self.run_id,
                "invocation_seq": self.invocation_seq,
                "parent_receipt_hash": self.parent_receipt_hash,
                "request_hash": self.request_hash,
                "manifest_hash": self.manifest_hash,
                "attestation_hash": self.attestation_hash,
                "stdout_hash": self.stdout_hash,
                "stdout_bytes": self.stdout_bytes,
                "stderr_hash": self.stderr_hash,
                "stderr_bytes": self.stderr_bytes,
                "synthetic": self.synthetic,
                "terminal": self.terminal,
                "thread_id": self.thread_id,
                "usage": self.usage,
                "event_count": self.event_count,
                "reasoning_item_count": self.reasoning_item_count,
                "tool_event_count": self.tool_event_count,
                "orchestration_elapsed_ns": self.orchestration_elapsed_ns,
            }
        )

    def validate(self) -> None:
        if not isinstance(self.role, CodexRole) or not isinstance(
            self.terminal, InvocationReceiptTerminal
        ):
            raise ValueError("receipt role and terminal must use frozen enums")
        for field_name in (
            "request_hash",
            "manifest_hash",
            "attestation_hash",
            "stdout_hash",
            "stderr_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, field_name), field_name)
        if UUID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("receipt run_id is invalid")
        if type(self.invocation_seq) is not int or self.invocation_seq not in (1, 2):
            raise ValueError("receipt invocation sequence is invalid")
        _require_hash(self.parent_receipt_hash, "parent_receipt_hash")
        if self.role is CodexRole.GENERATOR and (
            self.invocation_seq != 1 or self.parent_receipt_hash != ZERO_HASH
        ):
            raise ValueError("Generator receipt sequence or parent is invalid")
        if self.role is CodexRole.REVIEWER and (
            self.invocation_seq != 2 or self.parent_receipt_hash == ZERO_HASH
        ):
            raise ValueError("Reviewer receipt sequence or parent is invalid")
        if type(self.synthetic) is not bool:
            raise ValueError("receipt synthetic marker must be boolean")
        for value in (
            self.stdout_bytes,
            self.stderr_bytes,
            self.orchestration_elapsed_ns,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("receipt byte and duration counters must be non-negative integers")
        audited = self.terminal is InvocationReceiptTerminal.MODEL_AUDITED
        audit_values = (
            self.thread_id,
            self.usage,
            self.event_count,
            self.reasoning_item_count,
            self.tool_event_count,
        )
        if audited and (
            not isinstance(self.thread_id, str)
            or THREAD_PATTERN.fullmatch(self.thread_id) is None
            or not isinstance(self.usage, CodexUsage)
            or any(type(value) is not int or value < 0 for value in audit_values[2:])
        ):
            raise ValueError("audited receipt lacks an exact lifecycle summary")
        if self.usage is not None and any(
            type(value) is not int or not 0 <= value <= 1_000_000_000
            for value in (
                self.usage.input_tokens,
                self.usage.cached_input_tokens,
                self.usage.cache_write_input_tokens,
                self.usage.output_tokens,
                self.usage.reasoning_output_tokens,
            )
        ):
            raise ValueError("receipt usage counters are invalid")
        if not audited and any(value is not None for value in audit_values):
            raise ValueError("rejected audit receipt cannot claim lifecycle values")
        if _digest(canonical_json_bytes(self.payload())) != self.receipt_hash:
            raise ValueError("invocation receipt hash is invalid")


def _chain_closure_hash(
    *,
    run_id: str,
    run_nonce_hash: str,
    receipts: tuple[InvocationReceipt, ...],
    decision_set: DecisionSet,
) -> str:
    return _digest(
        canonical_json_bytes(
            {
                "schema_version": "t1.3b-python-chain-closure-v1",
                "run_id": run_id,
                "run_nonce_hash": run_nonce_hash,
                "receipt_hashes": [receipt.receipt_hash for receipt in receipts],
                "decision_set_hash": decision_set.decision_set_hash,
                "outcome": QualificationOutcome.SYNTHETIC_CHAIN_PASSED.value,
            }
        )
    )


@dataclass(frozen=True)
class QualificationResult:
    outcome: QualificationOutcome
    failure_code: JobFailureCode | None
    decision_set: DecisionSet | None
    generator_thread_id: str | None
    reviewer_thread_id: str | None
    native_manifest_hashes: tuple[str, ...]
    native_attestation_hashes: tuple[str, ...]
    invocation_receipts: tuple[InvocationReceipt, ...]
    attempted_invocations: int
    run_id: str
    run_nonce_hash: str
    chain_closure_hash: str | None
    live_qualified: bool
    production_eligible: bool = False
    failure_detail: None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, QualificationOutcome) or (
            self.failure_code is not None and not isinstance(self.failure_code, JobFailureCode)
        ):
            raise ValueError("qualification outcome and failure code must use frozen enums")
        if self.production_eligible is not False or self.failure_detail is not None:
            raise ValueError("T1.3b cannot activate production or expose failure detail")
        if UUID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("qualification result run_id is invalid")
        _require_hash(self.run_nonce_hash, "run_nonce_hash")
        if (
            type(self.attempted_invocations) is not int
            or not 0 <= self.attempted_invocations <= 2
            or self.attempted_invocations < len(self.invocation_receipts)
        ):
            raise ValueError("qualification invocation accounting is invalid")
        for receipt in self.invocation_receipts:
            receipt.validate()
            if receipt.run_id != self.run_id:
                raise ValueError("receipt run_id differs from qualification run")
        if self.invocation_receipts:
            first = self.invocation_receipts[0]
            if first.role is not CodexRole.GENERATOR:
                raise ValueError("qualification receipt prefix must start with Generator")
        if len(self.invocation_receipts) == 2:
            first, second = self.invocation_receipts
            if (
                second.role is not CodexRole.REVIEWER
                or second.parent_receipt_hash != first.receipt_hash
            ):
                raise ValueError("qualification receipt prefix is not one ordered chain")
        if self.native_manifest_hashes != tuple(
            receipt.manifest_hash for receipt in self.invocation_receipts
        ) or self.native_attestation_hashes != tuple(
            receipt.attestation_hash for receipt in self.invocation_receipts
        ):
            raise ValueError("qualification evidence hashes differ from retained receipts")
        passed = self.outcome is QualificationOutcome.SYNTHETIC_CHAIN_PASSED
        if passed != (self.failure_code is None):
            raise ValueError("only a complete synthetic pass may omit a failure code")
        if passed != (self.decision_set is not None):
            raise ValueError("only a complete two-model chain may expose a DecisionSet")
        if self.live_qualified is not False:
            raise ValueError("injectable Python seams cannot claim live qualification")
        if passed and (
            self.failure_code is not None
            or self.generator_thread_id is None
            or self.reviewer_thread_id is None
            or self.generator_thread_id == self.reviewer_thread_id
            or len(self.native_manifest_hashes) != 2
            or len(self.native_attestation_hashes) != 2
            or self.attempted_invocations != 2
            or len(self.invocation_receipts) != 2
            or self.chain_closure_hash is None
        ):
            raise ValueError("complete qualification result is missing exact chain evidence")
        if passed:
            assert self.decision_set is not None
            self.decision_set.validate()
            generator_receipt, reviewer_receipt = self.invocation_receipts
            if (
                generator_receipt.role is not CodexRole.GENERATOR
                or generator_receipt.invocation_seq != 1
                or generator_receipt.parent_receipt_hash != ZERO_HASH
                or reviewer_receipt.role is not CodexRole.REVIEWER
                or reviewer_receipt.invocation_seq != 2
                or reviewer_receipt.parent_receipt_hash != generator_receipt.receipt_hash
                or self.generator_thread_id != generator_receipt.thread_id
                or self.reviewer_thread_id != reviewer_receipt.thread_id
            ):
                raise ValueError("qualification receipts do not form one ordered chain")
            expected_closure = _chain_closure_hash(
                run_id=self.run_id,
                run_nonce_hash=self.run_nonce_hash,
                receipts=self.invocation_receipts,
                decision_set=self.decision_set,
            )
            if self.chain_closure_hash != expected_closure:
                raise ValueError("qualification chain closure hash is invalid")
        elif self.outcome is QualificationOutcome.GENERATOR_ABSTAINED_QUALIFICATION_INCOMPLETE:
            if (
                self.failure_code is not JobFailureCode.GENERATOR_ABSTAINED
                or self.attempted_invocations != 1
                or len(self.invocation_receipts) != 1
                or self.invocation_receipts[0].role is not CodexRole.GENERATOR
                or self.invocation_receipts[0].terminal
                is not InvocationReceiptTerminal.MODEL_AUDITED
                or self.generator_thread_id != self.invocation_receipts[0].thread_id
                or self.reviewer_thread_id is not None
            ):
                raise ValueError("Generator abstention result shape is invalid")
        elif self.generator_thread_id is not None or self.reviewer_thread_id is not None:
            raise ValueError("failed qualification cannot expose top-level thread identities")
        if not passed and self.chain_closure_hash is not None:
            raise ValueError("incomplete qualification cannot expose a chain closure")


class _ChainAbort(Exception):
    def __init__(
        self,
        outcome: QualificationOutcome,
        code: JobFailureCode,
        receipt: InvocationReceipt | None = None,
    ) -> None:
        self.outcome = outcome
        self.code = code
        self.receipt = receipt
        super().__init__(code.value)


class CodexQualificationJob:
    """Run the sole sealed T1.3b qualification chain; never render or publish."""

    def __init__(
        self,
        *,
        invoker: NativeQualifiedInvoker | None = None,
        validator: NativeAttestationValidator | None = None,
    ) -> None:
        self._invoker = invoker if invoker is not None else _BlockedNativeInvoker()
        self._validator = (
            validator if validator is not None else _BlockedAttestationValidator()
        )

    def run(
        self,
        *,
        curation: CurationResult,
        user_context_json: bytes,
    ) -> QualificationResult:
        run_id = str(uuid.uuid4())
        run_nonce_hash = _digest(secrets.token_bytes(32))
        receipts: list[InvocationReceipt] = []
        attempted_invocations = 0
        try:
            generator_input = seal_generator_input(
                curation=curation,
                user_context_json=user_context_json,
            )
            generator_request = self._request(
                generator_input,
                previous_thread_id=None,
                run_id=run_id,
                run_nonce_hash=run_nonce_hash,
                invocation_seq=1,
                parent_receipt_hash=ZERO_HASH,
            )
        except (TypeError, ValueError):
            return self._failure(
                JobFailureCode.SEALED_INPUT_REJECTED,
                run_id=run_id,
                run_nonce_hash=run_nonce_hash,
            )

        try:
            attempted_invocations += 1
            started_ns = time.perf_counter_ns()
            generator_evidence = self._invoke(generator_request, started_ns=started_ns)
            generator_audit, generator_receipt = self._audit(
                generator_evidence,
                CodexRole.GENERATOR,
                request=generator_request,
                started_ns=started_ns,
            )
            receipts.append(generator_receipt)
            try:
                generator_draft = parse_generator_output(
                    generator_input,
                    generator_audit.final_agent_message.encode("utf-8"),
                )
            except (TypeError, ValueError) as error:
                raise _ChainAbort(
                    QualificationOutcome.CHAIN_FAILED,
                    JobFailureCode.GENERATOR_OUTPUT_REJECTED,
                ) from error

            if not generator_draft.stories:
                return QualificationResult(
                    outcome=(
                        QualificationOutcome.GENERATOR_ABSTAINED_QUALIFICATION_INCOMPLETE
                    ),
                    failure_code=JobFailureCode.GENERATOR_ABSTAINED,
                    decision_set=None,
                    generator_thread_id=generator_audit.thread_id,
                    reviewer_thread_id=None,
                    native_manifest_hashes=(generator_evidence.manifest_hash,),
                    native_attestation_hashes=(generator_evidence.attestation_hash,),
                    invocation_receipts=tuple(receipts),
                    attempted_invocations=attempted_invocations,
                    run_id=run_id,
                    run_nonce_hash=run_nonce_hash,
                    chain_closure_hash=None,
                    live_qualified=False,
                )

            try:
                requirements = ReviewContract().derive(
                    curation=curation,
                    draft=generator_draft,
                )
                reviewer_input = seal_reviewer_input(
                    curation=curation,
                    draft=generator_draft,
                    requirements=requirements,
                )
                reviewer_request = self._request(
                    reviewer_input,
                    previous_thread_id=generator_audit.thread_id,
                    run_id=run_id,
                    run_nonce_hash=run_nonce_hash,
                    invocation_seq=2,
                    parent_receipt_hash=generator_receipt.receipt_hash,
                )
            except (TypeError, ValueError) as error:
                raise _ChainAbort(
                    QualificationOutcome.CHAIN_FAILED,
                    JobFailureCode.REVIEW_CONTRACT_REJECTED,
                ) from error

            attempted_invocations += 1
            started_ns = time.perf_counter_ns()
            reviewer_evidence = self._invoke(reviewer_request, started_ns=started_ns)
            reviewer_audit, reviewer_receipt = self._audit(
                reviewer_evidence,
                CodexRole.REVIEWER,
                request=reviewer_request,
                started_ns=started_ns,
            )
            receipts.append(reviewer_receipt)
            try:
                reviewer_audit.assert_fresh_from(generator_audit)
            except CodexToolEventError as error:
                raise _ChainAbort(
                    QualificationOutcome.CHAIN_FAILED,
                    JobFailureCode.REVIEWER_THREAD_NOT_FRESH,
                ) from error
            try:
                decision_set = parse_reviewer_output(
                    reviewer_input,
                    reviewer_audit.final_agent_message.encode("utf-8"),
                )
                self._assert_exact_decisions(
                    decision_set,
                    requirements=requirements,
                    reviewer_input=reviewer_input,
                    curation=curation,
                )
            except (TypeError, ValueError) as error:
                raise _ChainAbort(
                    QualificationOutcome.CHAIN_FAILED,
                    JobFailureCode.REVIEW_DECISION_MISMATCH,
                ) from error

            chain_closure_hash = _chain_closure_hash(
                run_id=run_id,
                run_nonce_hash=run_nonce_hash,
                receipts=tuple(receipts),
                decision_set=decision_set,
            )
            return QualificationResult(
                outcome=QualificationOutcome.SYNTHETIC_CHAIN_PASSED,
                failure_code=None,
                decision_set=decision_set,
                generator_thread_id=generator_audit.thread_id,
                reviewer_thread_id=reviewer_audit.thread_id,
                native_manifest_hashes=(
                    generator_evidence.manifest_hash,
                    reviewer_evidence.manifest_hash,
                ),
                native_attestation_hashes=(
                    generator_evidence.attestation_hash,
                    reviewer_evidence.attestation_hash,
                ),
                invocation_receipts=tuple(receipts),
                attempted_invocations=attempted_invocations,
                run_id=run_id,
                run_nonce_hash=run_nonce_hash,
                chain_closure_hash=chain_closure_hash,
                live_qualified=False,
            )
        except _ChainAbort as error:
            if error.receipt is not None:
                receipts.append(error.receipt)
            return self._failure(
                error.code,
                outcome=error.outcome,
                run_id=run_id,
                run_nonce_hash=run_nonce_hash,
                receipts=tuple(receipts),
                attempted_invocations=attempted_invocations,
            )
        except Exception:
            return self._failure(
                JobFailureCode.INTERNAL_FAILURE,
                run_id=run_id,
                run_nonce_hash=run_nonce_hash,
                receipts=tuple(receipts),
                attempted_invocations=attempted_invocations,
            )

    @staticmethod
    def _request(
        sealed: SealedModelInput,
        *,
        previous_thread_id: str | None,
        run_id: str,
        run_nonce_hash: str,
        invocation_seq: int,
        parent_receipt_hash: str,
    ) -> NativeInvocationRequest:
        sealed.validate()
        role = {
            ModelRole.GENERATOR: CodexRole.GENERATOR,
            ModelRole.REVIEWER: CodexRole.REVIEWER,
        }[sealed.role]
        return NativeInvocationRequest(
            role=role,
            model=sealed.model,
            prompt_hash=sealed.prompt_hash,
            schema_hash=sealed.schema_hash,
            input_hash=sealed.payload_hash,
            schema_json=sealed.schema_json,
            stdin_bytes=render_model_stdin(sealed),
            run_id=run_id,
            run_nonce_hash=run_nonce_hash,
            invocation_seq=invocation_seq,
            parent_receipt_hash=parent_receipt_hash,
            previous_thread_id=previous_thread_id,
        )

    def _invoke(
        self,
        request: NativeInvocationRequest,
        *,
        started_ns: int,
    ) -> NativeInvocationEvidence:
        try:
            evidence = self._invoker.invoke(request)
        except NativeInvocationFailure as error:
            outcome, code = self._native_failure(error.code)
            raise _ChainAbort(outcome, code) from error
        except Exception as error:
            raise _ChainAbort(
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.INTERNAL_FAILURE,
            ) from error
        if not isinstance(evidence, NativeInvocationEvidence):
            raise _ChainAbort(
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.NATIVE_EVIDENCE_INVALID,
            )
        try:
            evidence.validate(request)
            invoker_synthetic = self._invoker.is_synthetic
            if type(invoker_synthetic) is not bool or invoker_synthetic != evidence.synthetic:
                raise ValueError("native seam and evidence origin markers differ")
        except Exception as error:
            raise _ChainAbort(
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.NATIVE_EVIDENCE_INVALID,
                self._rejected_receipt(
                    request=request,
                    evidence=evidence,
                    terminal=InvocationReceiptTerminal.NATIVE_EVIDENCE_REJECTED,
                    started_ns=started_ns,
                ),
            ) from error
        if not evidence.synthetic:
            raise _ChainAbort(
                QualificationOutcome.BLOCKED_PREQUALIFICATION,
                JobFailureCode.NATIVE_CLOSED_ADAPTER_NOT_INTEGRATED,
                self._rejected_receipt(
                    request=request,
                    evidence=evidence,
                    terminal=InvocationReceiptTerminal.NATIVE_CLOSED_ADAPTER_REJECTED,
                    started_ns=started_ns,
                ),
            )
        try:
            validation = self._validator.validate(request=request, evidence=evidence)
            if not isinstance(validation, AttestationValidation):
                raise ValueError("attestation validator returned an unknown decision")
            validation.validate(request=request, evidence=evidence)
        except Exception as error:
            raise _ChainAbort(
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.NATIVE_EVIDENCE_INVALID,
                self._rejected_receipt(
                    request=request,
                    evidence=evidence,
                    terminal=InvocationReceiptTerminal.NATIVE_ATTESTATION_REJECTED,
                    started_ns=started_ns,
                ),
            ) from error
        if not validation.accepted:
            raise _ChainAbort(
                QualificationOutcome.BLOCKED_PREQUALIFICATION,
                JobFailureCode.NATIVE_ATTESTATION_REJECTED,
                self._rejected_receipt(
                    request=request,
                    evidence=evidence,
                    terminal=InvocationReceiptTerminal.NATIVE_ATTESTATION_REJECTED,
                    started_ns=started_ns,
                ),
            )
        return evidence

    @staticmethod
    def _rejected_receipt(
        *,
        request: NativeInvocationRequest,
        evidence: NativeInvocationEvidence,
        terminal: InvocationReceiptTerminal,
        started_ns: int,
    ) -> InvocationReceipt | None:
        try:
            return InvocationReceipt.create(
                request=request,
                evidence=evidence,
                terminal=terminal,
                audit=None,
                orchestration_elapsed_ns=max(0, time.perf_counter_ns() - started_ns),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _audit(
        evidence: NativeInvocationEvidence,
        role: CodexRole,
        *,
        request: NativeInvocationRequest,
        started_ns: int,
    ) -> tuple[StrictCodexAuditResult, InvocationReceipt]:
        try:
            audit = audit_codex_jsonl_strict(evidence.stdout_bytes, role=role)
        except (TypeError, CodexToolEventError) as error:
            receipt = InvocationReceipt.create(
                request=request,
                evidence=evidence,
                terminal=InvocationReceiptTerminal.MODEL_EVENT_AUDIT_REJECTED,
                audit=None,
                orchestration_elapsed_ns=max(0, time.perf_counter_ns() - started_ns),
            )
            raise _ChainAbort(
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.MODEL_EVENT_AUDIT_REJECTED,
                receipt,
            ) from error
        receipt = InvocationReceipt.create(
            request=request,
            evidence=evidence,
            terminal=InvocationReceiptTerminal.MODEL_AUDITED,
            audit=audit,
            orchestration_elapsed_ns=max(0, time.perf_counter_ns() - started_ns),
        )
        return audit, receipt

    @staticmethod
    def _assert_exact_decisions(
        decision_set: DecisionSet,
        *,
        requirements: tuple[ReviewRequirement, ...],
        reviewer_input: SealedModelInput,
        curation: CurationResult,
    ) -> None:
        decision_set.validate()
        if (
            len(decision_set.decisions) != len(requirements)
            or decision_set.reviewer_input_hash != reviewer_input.payload_hash
            or decision_set.curation_result_hash != curation.curation_result_hash
            or not requirements
            or decision_set.generator_draft_hash != requirements[0].generator_draft_hash
        ):
            raise ValueError("DecisionSet closure does not exactly match the review contract")
        for decision, requirement in zip(decision_set.decisions, requirements, strict=True):
            if (
                decision.decision_id != requirement.claim_id
                or decision.requirement_id != requirement.claim_id
                or decision.claim_id != requirement.claim_id
                or decision.evidence_ids != requirement.evidence_ids
                or decision.review_requirement_hash != requirement.review_requirement_hash
            ):
                raise ValueError("DecisionSet is not exactly equal to ReviewContract.derive")

    @staticmethod
    def _native_failure(
        code: NativeFailureCode,
    ) -> tuple[QualificationOutcome, JobFailureCode]:
        mapping = {
            NativeFailureCode.BLOCKED_PREQUALIFICATION: (
                QualificationOutcome.BLOCKED_PREQUALIFICATION,
                JobFailureCode.NATIVE_SEAM_BLOCKED,
            ),
            NativeFailureCode.TIMEOUT: (
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.NATIVE_TIMEOUT,
            ),
            NativeFailureCode.AUTH_REQUIRED: (
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.AUTH_REQUIRED,
            ),
            NativeFailureCode.QUOTA_DELAYED: (
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.QUOTA_DELAYED,
            ),
            NativeFailureCode.SECURITY_BLOCKED: (
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.NATIVE_SECURITY_BLOCKED,
            ),
            NativeFailureCode.PROCESS_FAILED: (
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.NATIVE_PROCESS_FAILED,
            ),
            NativeFailureCode.INTERNAL_ERROR: (
                QualificationOutcome.CHAIN_FAILED,
                JobFailureCode.INTERNAL_FAILURE,
            ),
        }
        return mapping[code]

    @staticmethod
    def _failure(
        code: JobFailureCode,
        *,
        run_id: str,
        run_nonce_hash: str,
        outcome: QualificationOutcome = QualificationOutcome.CHAIN_FAILED,
        receipts: tuple[InvocationReceipt, ...] = (),
        attempted_invocations: int = 0,
    ) -> QualificationResult:
        return QualificationResult(
            outcome=outcome,
            failure_code=code,
            decision_set=None,
            generator_thread_id=None,
            reviewer_thread_id=None,
            native_manifest_hashes=tuple(receipt.manifest_hash for receipt in receipts),
            native_attestation_hashes=tuple(receipt.attestation_hash for receipt in receipts),
            invocation_receipts=receipts,
            attempted_invocations=attempted_invocations,
            run_id=run_id,
            run_nonce_hash=run_nonce_hash,
            chain_closure_hash=None,
            live_qualified=False,
        )
