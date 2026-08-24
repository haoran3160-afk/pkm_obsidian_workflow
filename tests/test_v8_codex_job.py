from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace

import pytest
from test_v8_curation import build_valid_closure, curate_closure

from pkm_workflow.codex_job import (
    MAX_NATIVE_EVIDENCE_BYTES,
    MAX_NATIVE_STDIN_BYTES,
    AttestationValidation,
    CodexQualificationJob,
    InvocationReceiptTerminal,
    JobFailureCode,
    NativeFailureCode,
    NativeInvocationEvidence,
    NativeInvocationFailure,
    NativeInvocationRequest,
    QualificationOutcome,
)
from pkm_workflow.codex_runner import CodexRole, CodexUsage
from pkm_workflow.provenance import canonical_json_bytes

GENERATOR_THREAD = "019f2b90-0c34-77a1-ad02-b38355965b93"
REVIEWER_THREAD = "019f2b90-0c34-77a1-ad02-b38355965b94"
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


def _model_payload(request: NativeInvocationRequest) -> dict[str, object]:
    marker = b"BEGIN_SEALED_INPUT_JSON\n"
    end = b"\nEND_SEALED_INPUT_JSON\n"
    start = request.stdin_bytes.index(marker) + len(marker)
    return json.loads(request.stdin_bytes[start : -len(end)])


def _generator_message(
    request: NativeInvocationRequest,
    *,
    abstain: bool = False,
) -> bytes:
    payload = _model_payload(request)
    curation = payload["curation"]
    context = payload["user_context"]
    assert isinstance(curation, dict)
    assert isinstance(context, dict)
    candidate = curation["model_candidates"][0]
    evidence_id = candidate["evidence"][0]["evidence_id"]
    stories: list[dict[str, object]] = []
    digest_claims: list[str] = []
    if not abstain:
        stories.append(
            {
                "story_id": "story-1",
                "cluster_hash": candidate["cluster_hash"],
                "title_claim_id": "claim-1",
                "top_rank": 1,
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "statement": "The sealed evidence reports an Agent release.",
                        "evidence_ids": [evidence_id],
                        "claimed_risk": "LOW",
                    }
                ],
                "conclusion_claim_ids": ["claim-1"],
                "recommendation_claim_ids": [],
                "source_excerpts": [],
            }
        )
        digest_claims.append("claim-1")
    return canonical_json_bytes(
        {
            "binding": {
                "model": request.model,
                "prompt_hash": request.prompt_hash,
                "schema_hash": request.schema_hash,
                "input_hash": request.input_hash,
                "curation_result_hash": curation["curation_result_hash"],
                "user_context_hash": context["user_context_hash"],
            },
            "stories": stories,
            "digest_conclusion_claim_ids": digest_claims,
        }
    )


def _reviewer_message(
    request: NativeInvocationRequest,
    *,
    mutate: Callable[[dict[str, object]], None] | None = None,
) -> bytes:
    payload = _model_payload(request)
    rubric = payload["rubric"]
    assert isinstance(rubric, dict)
    decisions = [dict(value, verdict="ACCEPT") for value in rubric["decision_bindings"]]
    result: dict[str, object] = {
        "binding": {
            "model": request.model,
            "prompt_hash": request.prompt_hash,
            "schema_hash": request.schema_hash,
            "input_hash": request.input_hash,
            "curation_result_hash": rubric["curation_result_hash"],
            "generator_draft_hash": rubric["generator_draft_hash"],
        },
        "decisions": decisions,
    }
    if mutate is not None:
        mutate(result)
    return canonical_json_bytes(result)


def _jsonl(thread_id: str, message: bytes) -> bytes:
    events = (
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "item-1", "type": "agent_message", "text": message.decode()},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 10,
                "reasoning_output_tokens": 0,
            },
        },
    )
    return b"\n".join(canonical_json_bytes(event) for event in events) + b"\n"


def _evidence(
    request: NativeInvocationRequest,
    stdout: bytes,
) -> NativeInvocationEvidence:
    return NativeInvocationEvidence.create_synthetic(
        request=request,
        stdout_bytes=stdout,
        qualifier_release_hash=SHA_A,
        isolation_evidence_hash=SHA_B,
        file_trace_hash=SHA_C,
        network_trace_hash=SHA_C,
        process_outcome_hash=SHA_C,
        proxy_terminal_hash=SHA_C,
        joint_terminal_hash=SHA_C,
        stdout_artifact_identity_hash=SHA_C,
        windows_build_hash=SHA_C,
        official_binary_identity_hash=SHA_C,
        package_sid_hash=SHA_C,
        wfp_policy_hash=SHA_C,
    )


class ScriptedSyntheticInvoker:
    is_synthetic = True

    def __init__(
        self,
        generator: Callable[[NativeInvocationRequest], NativeInvocationEvidence],
        reviewer: Callable[[NativeInvocationRequest], NativeInvocationEvidence] | None = None,
    ) -> None:
        self._generator = generator
        self._reviewer = reviewer
        self.requests: list[NativeInvocationRequest] = []

    def invoke(self, request: NativeInvocationRequest) -> NativeInvocationEvidence:
        self.requests.append(request)
        if request.role is CodexRole.GENERATOR:
            return self._generator(request)
        if self._reviewer is None:
            raise AssertionError("Reviewer must not be called")
        return self._reviewer(request)


class SyntheticValidator:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls = 0

    def validate(
        self,
        *,
        request: NativeInvocationRequest,
        evidence: NativeInvocationEvidence,
    ) -> AttestationValidation:
        self.calls += 1
        return AttestationValidation(
            accepted=self.accepted,
            request_hash=request.request_hash,
            manifest_hash=evidence.manifest_hash,
            attestation_hash=evidence.attestation_hash,
            validator_policy_hash=SHA_A,
            blocker_codes=() if self.accepted else ("SYNTHETIC_VALIDATOR_REJECTED",),
        )


def _valid_inputs():
    closure = build_valid_closure()
    return curate_closure(closure), closure[6]


def _happy_invoker() -> ScriptedSyntheticInvoker:
    return ScriptedSyntheticInvoker(
        lambda request: _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _generator_message(request)),
        ),
        lambda request: _evidence(
            request,
            _jsonl(REVIEWER_THREAD, _reviewer_message(request)),
        ),
    )


def test_default_job_is_unconstructably_blocked_and_never_calls_a_model() -> None:
    curation, context = _valid_inputs()

    result = CodexQualificationJob().run(curation=curation, user_context_json=context)

    assert result.outcome is QualificationOutcome.BLOCKED_PREQUALIFICATION
    assert result.failure_code is JobFailureCode.NATIVE_SEAM_BLOCKED
    assert result.decision_set is None
    assert result.live_qualified is False
    assert result.production_eligible is False


def test_real_t13a_closure_runs_one_sealed_generator_and_fresh_reviewer_chain() -> None:
    curation, context = _valid_inputs()
    invoker = _happy_invoker()
    validator = SyntheticValidator()

    result = CodexQualificationJob(invoker=invoker, validator=validator).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.outcome is QualificationOutcome.SYNTHETIC_CHAIN_PASSED
    assert result.failure_code is None
    assert result.decision_set is not None
    assert [decision.verdict.value for decision in result.decision_set.decisions] == ["ACCEPT"]
    assert result.generator_thread_id == GENERATOR_THREAD
    assert result.reviewer_thread_id == REVIEWER_THREAD
    assert result.generator_thread_id != result.reviewer_thread_id
    assert result.live_qualified is False
    assert result.production_eligible is False
    assert validator.calls == 2
    assert [request.role for request in invoker.requests] == [
        CodexRole.GENERATOR,
        CodexRole.REVIEWER,
    ]
    assert invoker.requests[0].previous_thread_id is None
    assert invoker.requests[1].previous_thread_id == GENERATOR_THREAD
    assert all(request.fresh_session_required is True for request in invoker.requests)
    assert not hasattr(result, "digest")
    assert not hasattr(result, "vault_target")


def test_generator_abstention_is_explicitly_incomplete_and_skips_reviewer() -> None:
    curation, context = _valid_inputs()
    invoker = ScriptedSyntheticInvoker(
        lambda request: _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _generator_message(request, abstain=True)),
        )
    )

    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.outcome is QualificationOutcome.GENERATOR_ABSTAINED_QUALIFICATION_INCOMPLETE
    assert result.failure_code is JobFailureCode.GENERATOR_ABSTAINED
    assert len(invoker.requests) == 1
    assert result.decision_set is None
    assert result.live_qualified is False


@pytest.mark.parametrize(
    ("native_code", "job_code", "outcome"),
    [
        (
            NativeFailureCode.BLOCKED_PREQUALIFICATION,
            JobFailureCode.NATIVE_SEAM_BLOCKED,
            QualificationOutcome.BLOCKED_PREQUALIFICATION,
        ),
        (
            NativeFailureCode.TIMEOUT,
            JobFailureCode.NATIVE_TIMEOUT,
            QualificationOutcome.CHAIN_FAILED,
        ),
        (
            NativeFailureCode.AUTH_REQUIRED,
            JobFailureCode.AUTH_REQUIRED,
            QualificationOutcome.CHAIN_FAILED,
        ),
        (
            NativeFailureCode.QUOTA_DELAYED,
            JobFailureCode.QUOTA_DELAYED,
            QualificationOutcome.CHAIN_FAILED,
        ),
        (
            NativeFailureCode.SECURITY_BLOCKED,
            JobFailureCode.NATIVE_SECURITY_BLOCKED,
            QualificationOutcome.CHAIN_FAILED,
        ),
        (
            NativeFailureCode.PROCESS_FAILED,
            JobFailureCode.NATIVE_PROCESS_FAILED,
            QualificationOutcome.CHAIN_FAILED,
        ),
        (
            NativeFailureCode.INTERNAL_ERROR,
            JobFailureCode.INTERNAL_FAILURE,
            QualificationOutcome.CHAIN_FAILED,
        ),
    ],
)
def test_native_blocker_timeout_auth_quota_and_security_all_fail_closed(
    native_code: NativeFailureCode,
    job_code: JobFailureCode,
    outcome: QualificationOutcome,
) -> None:
    curation, context = _valid_inputs()

    def fail(_request: NativeInvocationRequest) -> NativeInvocationEvidence:
        raise NativeInvocationFailure(native_code)

    result = CodexQualificationJob(
        invoker=ScriptedSyntheticInvoker(fail),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)

    assert result.outcome is outcome
    assert result.failure_code is job_code
    assert result.decision_set is None
    assert result.live_qualified is False


@pytest.mark.parametrize("attack", ["tool", "unknown", "partial"])
def test_tool_unknown_and_partial_jsonl_never_reach_model_parser(attack: str) -> None:
    curation, context = _valid_inputs()

    def generator(request: NativeInvocationRequest) -> NativeInvocationEvidence:
        if attack == "partial":
            stdout = canonical_json_bytes(
                {"type": "thread.started", "thread_id": GENERATOR_THREAD}
            )
        else:
            item_type = "command_execution" if attack == "tool" else "future_item"
            stdout = b"\n".join(
                (
                    canonical_json_bytes(
                        {"type": "thread.started", "thread_id": GENERATOR_THREAD}
                    ),
                    canonical_json_bytes({"type": "turn.started"}),
                    canonical_json_bytes(
                        {
                            "type": "item.completed",
                            "item": {"id": "item-1", "type": item_type, "text": "bad"},
                        }
                    ),
                )
            )
        return _evidence(request, stdout)

    result = CodexQualificationJob(
        invoker=ScriptedSyntheticInvoker(generator),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)

    assert result.outcome is QualificationOutcome.CHAIN_FAILED
    assert result.failure_code is JobFailureCode.MODEL_EVENT_AUDIT_REJECTED
    assert result.decision_set is None


def test_generator_and_reviewer_must_use_distinct_thread_ids() -> None:
    curation, context = _valid_inputs()
    invoker = ScriptedSyntheticInvoker(
        lambda request: _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _generator_message(request)),
        ),
        lambda request: _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _reviewer_message(request)),
        ),
    )

    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.outcome is QualificationOutcome.CHAIN_FAILED
    assert result.failure_code is JobFailureCode.REVIEWER_THREAD_NOT_FRESH
    assert result.decision_set is None


def test_reviewer_decision_binding_mismatch_rejects_the_whole_chain() -> None:
    curation, context = _valid_inputs()

    def reviewer(request: NativeInvocationRequest) -> NativeInvocationEvidence:
        def mutate(result: dict[str, object]) -> None:
            decisions = result["decisions"]
            assert isinstance(decisions, list)
            decisions[0]["evidence_ids"] = ["forged-evidence"]

        return _evidence(
            request,
            _jsonl(REVIEWER_THREAD, _reviewer_message(request, mutate=mutate)),
        )

    invoker = ScriptedSyntheticInvoker(
        lambda request: _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _generator_message(request)),
        ),
        reviewer,
    )

    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.outcome is QualificationOutcome.CHAIN_FAILED
    assert result.failure_code is JobFailureCode.REVIEW_DECISION_MISMATCH
    assert result.decision_set is None


def test_content_addressed_manifest_tampering_is_a_security_failure() -> None:
    curation, context = _valid_inputs()

    def generator(request: NativeInvocationRequest) -> NativeInvocationEvidence:
        evidence = _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _generator_message(request)),
        )
        return replace(evidence, manifest_hash=SHA_C)

    result = CodexQualificationJob(
        invoker=ScriptedSyntheticInvoker(generator),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)

    assert result.outcome is QualificationOutcome.CHAIN_FAILED
    assert result.failure_code is JobFailureCode.NATIVE_EVIDENCE_INVALID
    assert result.decision_set is None


def test_injectable_non_synthetic_transport_cannot_claim_live_qualification() -> None:
    curation, context = _valid_inputs()

    class FakeNativeInvoker(ScriptedSyntheticInvoker):
        is_synthetic = False

        def invoke(self, request: NativeInvocationRequest) -> NativeInvocationEvidence:
            synthetic = super().invoke(request)
            manifest = json.loads(synthetic.manifest_json)
            manifest["synthetic"] = False
            manifest["qualification_terminal"] = "NATIVE_QUALIFICATION_SUCCEEDED"
            manifest_json = canonical_json_bytes(manifest)
            manifest_hash = "sha256:" + hashlib.sha256(manifest_json).hexdigest()
            attestation = json.loads(synthetic.attestation_json)
            attestation["manifest_hash"] = manifest_hash
            closure = {
                key: value
                for key, value in attestation.items()
                if key
                not in {
                    "schema_version",
                    "request_hash",
                    "result_closure_hash",
                }
            }
            closure["qualification_terminal"] = "NATIVE_QUALIFICATION_SUCCEEDED"
            attestation["qualification_terminal"] = "NATIVE_QUALIFICATION_SUCCEEDED"
            attestation["result_closure_hash"] = (
                "sha256:" + hashlib.sha256(canonical_json_bytes(closure)).hexdigest()
            )
            return NativeInvocationEvidence.from_native_bytes(
                stdout_bytes=synthetic.stdout_bytes,
                stderr_bytes=synthetic.stderr_bytes,
                manifest_json=manifest_json,
                attestation_json=canonical_json_bytes(attestation),
            )

    result = CodexQualificationJob(
        invoker=FakeNativeInvoker(
            lambda request: _evidence(
                request,
                _jsonl(GENERATOR_THREAD, _generator_message(request)),
            )
        ),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)

    assert result.outcome is QualificationOutcome.BLOCKED_PREQUALIFICATION
    assert result.failure_code is JobFailureCode.NATIVE_CLOSED_ADAPTER_NOT_INTEGRATED
    assert result.live_qualified is False
    assert result.production_eligible is False
    assert result.decision_set is None


def test_attestation_rejection_blocks_before_model_output_is_parsed() -> None:
    curation, context = _valid_inputs()

    result = CodexQualificationJob(
        invoker=_happy_invoker(),
        validator=SyntheticValidator(accepted=False),
    ).run(curation=curation, user_context_json=context)

    assert result.outcome is QualificationOutcome.BLOCKED_PREQUALIFICATION
    assert result.failure_code is JobFailureCode.NATIVE_ATTESTATION_REJECTED
    assert result.decision_set is None


def test_invalid_user_context_and_unexpected_invoker_error_do_not_escape() -> None:
    curation, _context = _valid_inputs()
    unused = _happy_invoker()

    invalid = CodexQualificationJob(invoker=unused, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=b"{}",
    )
    assert invalid.failure_code is JobFailureCode.SEALED_INPUT_REJECTED
    assert unused.requests == []

    def crash(_request: NativeInvocationRequest) -> NativeInvocationEvidence:
        raise RuntimeError("host detail must not escape")

    crashed = CodexQualificationJob(
        invoker=ScriptedSyntheticInvoker(crash),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=build_valid_closure()[6])
    assert crashed.failure_code is JobFailureCode.INTERNAL_FAILURE
    assert crashed.failure_detail is None


def test_native_request_rejects_role_replay_and_byte_binding_mutations() -> None:
    curation, context = _valid_inputs()
    invoker = _happy_invoker()
    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )
    assert result.decision_set is not None
    generator, reviewer = invoker.requests

    invalid_changes = (
        {"model": "gpt-fake"},
        {"prompt_hash": "bad"},
        {"run_id": "bad"},
        {"run_nonce_hash": "sha256:" + "0" * 64},
        {"invocation_seq": 2},
        {"schema_json": b"{}"},
        {"stdin_bytes": b""},
        {"stdin_bytes": b"x" * (MAX_NATIVE_STDIN_BYTES + 1)},
        {"fresh_session_required": False},
        {"previous_thread_id": GENERATOR_THREAD},
        {"parent_receipt_hash": SHA_A},
    )
    for changes in invalid_changes:
        with pytest.raises((TypeError, ValueError)):
            replace(generator, **changes)
    with pytest.raises(ValueError, match="Reviewer request"):
        replace(reviewer, previous_thread_id=None)
    with pytest.raises(ValueError, match="Generator receipt"):
        replace(reviewer, parent_receipt_hash="sha256:" + "0" * 64)
    with pytest.raises(TypeError, match="typed"):
        NativeInvocationFailure("TIMEOUT")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "manifest",
    [
        b"\xff",
        b"[]",
        b'{"synthetic":NaN}',
        b'{"synthetic":1}',
        b'{"synthetic":true,"synthetic":true}',
        b'{ "synthetic": true }',
        b"x" * (MAX_NATIVE_EVIDENCE_BYTES + 1),
    ],
    ids=("invalid-utf8", "array", "nan", "typed-marker", "duplicate", "noncanonical", "oversized"),
)
def test_native_artifact_wrapper_rejects_noncanonical_or_ambiguous_json(manifest: bytes) -> None:
    with pytest.raises((TypeError, ValueError)):
        NativeInvocationEvidence.from_native_bytes(
            stdout_bytes=b"",
            stderr_bytes=b"",
            manifest_json=manifest,
            attestation_json=b"{}",
        )


def test_native_manifest_uses_exact_json_types_not_python_numeric_equality() -> None:
    curation, context = _valid_inputs()
    invoker = _happy_invoker()
    CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )
    request = invoker.requests[0]
    evidence = _evidence(
        request,
        _jsonl(GENERATOR_THREAD, _generator_message(request)),
    )

    for wrong_exit in (False, 0.0):
        manifest = json.loads(evidence.manifest_json)
        manifest["exit_code"] = wrong_exit
        raw = canonical_json_bytes(manifest)
        forged = replace(
            evidence,
            manifest_json=raw,
            manifest_hash="sha256:" + hashlib.sha256(raw).hexdigest(),
        )
        with pytest.raises(ValueError, match="exactly bound"):
            forged.validate(request)

    with pytest.raises(TypeError, match="raw bytes"):
        replace(evidence, stdout_bytes="not-bytes").validate(request)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="synthetic marker"):
        replace(evidence, synthetic=1).validate(request)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="attestation content hash"):
        replace(evidence, attestation_hash=SHA_A).validate(request)

    attestation_with_extra = json.loads(evidence.attestation_json)
    attestation_with_extra["unexpected"] = SHA_A
    extra_raw = canonical_json_bytes(attestation_with_extra)
    with pytest.raises(ValueError, match="fields are not exact"):
        replace(
            evidence,
            attestation_json=extra_raw,
            attestation_hash="sha256:" + hashlib.sha256(extra_raw).hexdigest(),
        ).validate(request)

    attestation_with_wrong_schema = json.loads(evidence.attestation_json)
    attestation_with_wrong_schema["schema_version"] = "wrong"
    wrong_schema_raw = canonical_json_bytes(attestation_with_wrong_schema)
    with pytest.raises(ValueError, match="exact result closure"):
        replace(
            evidence,
            attestation_json=wrong_schema_raw,
            attestation_hash="sha256:" + hashlib.sha256(wrong_schema_raw).hexdigest(),
        ).validate(request)
    with pytest.raises(TypeError, match="raw bytes"):
        NativeInvocationEvidence.create_synthetic(
            request=request,
            stdout_bytes="bad",  # type: ignore[arg-type]
            qualifier_release_hash=SHA_A,
            isolation_evidence_hash=SHA_B,
            file_trace_hash=SHA_C,
            network_trace_hash=SHA_C,
            process_outcome_hash=SHA_C,
            proxy_terminal_hash=SHA_C,
            joint_terminal_hash=SHA_C,
            stdout_artifact_identity_hash=SHA_C,
            windows_build_hash=SHA_C,
            official_binary_identity_hash=SHA_C,
            package_sid_hash=SHA_C,
            wfp_policy_hash=SHA_C,
        )


def test_attestation_decision_is_exactly_content_addressed_and_typed() -> None:
    curation, context = _valid_inputs()
    invoker = _happy_invoker()
    CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )
    request = invoker.requests[0]
    evidence = _evidence(
        request,
        _jsonl(GENERATOR_THREAD, _generator_message(request)),
    )
    valid = SyntheticValidator().validate(request=request, evidence=evidence)
    valid.validate(request=request, evidence=evidence)

    mutations = (
        {"accepted": 1},
        {"request_hash": SHA_B},
        {"blocker_codes": ["BAD"]},
        {"blocker_codes": ("BAD",)},
        {"accepted": False, "blocker_codes": ()},
        {"accepted": False, "blocker_codes": ("BAD", "BAD")},
    )
    for changes in mutations:
        with pytest.raises(ValueError):
            replace(valid, **changes).validate(request=request, evidence=evidence)


def test_late_reviewer_failure_retains_generator_receipt_and_usage() -> None:
    curation, context = _valid_inputs()

    def reviewer(_request: NativeInvocationRequest) -> NativeInvocationEvidence:
        raise NativeInvocationFailure(NativeFailureCode.TIMEOUT)

    invoker = ScriptedSyntheticInvoker(
        lambda request: _evidence(
            request,
            _jsonl(GENERATOR_THREAD, _generator_message(request)),
        ),
        reviewer,
    )
    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.failure_code is JobFailureCode.NATIVE_TIMEOUT
    assert result.attempted_invocations == 2
    assert len(result.invocation_receipts) == 1
    receipt = result.invocation_receipts[0]
    assert receipt.role is CodexRole.GENERATOR
    assert receipt.terminal is InvocationReceiptTerminal.MODEL_AUDITED
    assert receipt.thread_id == GENERATOR_THREAD
    assert receipt.usage is not None and receipt.usage.input_tokens == 10
    assert receipt.stdout_bytes > 0
    assert receipt.orchestration_elapsed_ns >= 0


def test_audit_failure_retains_content_hash_without_claiming_usage() -> None:
    curation, context = _valid_inputs()
    invoker = ScriptedSyntheticInvoker(
        lambda request: _evidence(
            request,
            canonical_json_bytes({"type": "thread.started", "thread_id": GENERATOR_THREAD}),
        )
    )
    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.failure_code is JobFailureCode.MODEL_EVENT_AUDIT_REJECTED
    assert len(result.invocation_receipts) == 1
    receipt = result.invocation_receipts[0]
    assert receipt.terminal is InvocationReceiptTerminal.MODEL_EVENT_AUDIT_REJECTED
    assert receipt.thread_id is None
    assert receipt.usage is None


def test_generator_schema_and_review_contract_failures_keep_all_or_nothing() -> None:
    curation, context = _valid_inputs()
    invalid_output = ScriptedSyntheticInvoker(
        lambda request: _evidence(request, _jsonl(GENERATOR_THREAD, b"{}"))
    )
    invalid_result = CodexQualificationJob(
        invoker=invalid_output,
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)
    assert invalid_result.failure_code is JobFailureCode.GENERATOR_OUTPUT_REJECTED
    assert len(invalid_result.invocation_receipts) == 1

    def invalid_contract(request: NativeInvocationRequest) -> NativeInvocationEvidence:
        output = json.loads(_generator_message(request))
        output["stories"][0]["title_claim_id"] = "claim-missing"
        return _evidence(request, _jsonl(GENERATOR_THREAD, canonical_json_bytes(output)))

    contract_result = CodexQualificationJob(
        invoker=ScriptedSyntheticInvoker(invalid_contract),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)
    assert contract_result.failure_code is JobFailureCode.REVIEW_CONTRACT_REJECTED
    assert len(contract_result.invocation_receipts) == 1
    assert contract_result.decision_set is None


def test_receipt_and_result_rederive_hashes_and_ordered_chain() -> None:
    curation, context = _valid_inputs()
    invoker = _happy_invoker()
    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )
    assert result.decision_set is not None
    receipt = result.invocation_receipts[0]

    receipt_mutations = (
        {"receipt_hash": SHA_A},
        {"synthetic": 1},
        {"orchestration_elapsed_ns": -1},
        {"thread_id": None},
        {"usage": CodexUsage(-1, 0, 0, 0, 0)},
        {"terminal": InvocationReceiptTerminal.MODEL_EVENT_AUDIT_REJECTED},
    )
    for changes in receipt_mutations:
        with pytest.raises(ValueError):
            replace(receipt, **changes)

    result_mutations = (
        {"production_eligible": True},
        {"run_id": "bad"},
        {"attempted_invocations": -1},
        {"native_manifest_hashes": ()},
        {"decision_set": None},
        {"live_qualified": True},
        {"reviewer_thread_id": GENERATOR_THREAD},
        {"chain_closure_hash": SHA_A},
        {"invocation_receipts": tuple(reversed(result.invocation_receipts))},
    )
    for changes in result_mutations:
        with pytest.raises(ValueError):
            replace(result, **changes)

    failed = replace(
        result,
        outcome=QualificationOutcome.CHAIN_FAILED,
        failure_code=JobFailureCode.INTERNAL_FAILURE,
        decision_set=None,
        generator_thread_id=None,
        reviewer_thread_id=None,
        chain_closure_hash=None,
        live_qualified=False,
    )
    with pytest.raises(ValueError, match="incomplete qualification"):
        replace(failed, chain_closure_hash=SHA_A)


def test_unexpected_review_contract_exception_is_structurally_contained(monkeypatch) -> None:
    curation, context = _valid_inputs()
    invoker = _happy_invoker()

    def crash(*_args, **_kwargs):
        raise RuntimeError("must not escape")

    monkeypatch.setattr("pkm_workflow.codex_job.ReviewContract.derive", crash)
    result = CodexQualificationJob(invoker=invoker, validator=SyntheticValidator()).run(
        curation=curation,
        user_context_json=context,
    )

    assert result.failure_code is JobFailureCode.INTERNAL_FAILURE
    assert result.failure_detail is None
    assert len(result.invocation_receipts) == 1


def test_unknown_native_evidence_origin_and_validator_shapes_fail_closed() -> None:
    curation, context = _valid_inputs()

    class UnknownEvidenceInvoker:
        is_synthetic = True

        def invoke(self, _request: NativeInvocationRequest) -> object:
            return object()

    unknown_evidence = CodexQualificationJob(
        invoker=UnknownEvidenceInvoker(),  # type: ignore[arg-type]
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)
    assert unknown_evidence.failure_code is JobFailureCode.NATIVE_EVIDENCE_INVALID
    assert unknown_evidence.invocation_receipts == ()

    class OriginMismatchInvoker(ScriptedSyntheticInvoker):
        is_synthetic = False

    origin_mismatch = CodexQualificationJob(
        invoker=OriginMismatchInvoker(
            lambda request: _evidence(
                request,
                _jsonl(GENERATOR_THREAD, _generator_message(request)),
            )
        ),
        validator=SyntheticValidator(),
    ).run(curation=curation, user_context_json=context)
    assert origin_mismatch.failure_code is JobFailureCode.NATIVE_EVIDENCE_INVALID
    assert len(origin_mismatch.invocation_receipts) == 1
    assert (
        origin_mismatch.invocation_receipts[0].terminal
        is InvocationReceiptTerminal.NATIVE_EVIDENCE_REJECTED
    )

    class UnknownValidator:
        def validate(
            self,
            *,
            request: NativeInvocationRequest,
            evidence: NativeInvocationEvidence,
        ) -> object:
            del request, evidence
            return object()

    unknown_validation = CodexQualificationJob(
        invoker=_happy_invoker(),
        validator=UnknownValidator(),  # type: ignore[arg-type]
    ).run(curation=curation, user_context_json=context)
    assert unknown_validation.failure_code is JobFailureCode.NATIVE_EVIDENCE_INVALID
    assert len(unknown_validation.invocation_receipts) == 1
    assert (
        unknown_validation.invocation_receipts[0].terminal
        is InvocationReceiptTerminal.NATIVE_ATTESTATION_REJECTED
    )
