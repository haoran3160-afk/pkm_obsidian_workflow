from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from path_policy import CapacityPolicy
from run_service import RunService
from run_store import FencingError, RunStore
from workflow_contracts import (
    AuthorizationContext,
    EventType,
    ExecutionMode,
    FeedbackAction,
    GateKind,
    RunStatus,
    SafetyInputs,
)


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _authorization(mode: ExecutionMode = ExecutionMode.SHADOW) -> AuthorizationContext:
    gate_fields: dict[str, str] = {}
    if mode is not ExecutionMode.SHADOW:
        gate_fields = {
            "gate_receipt_id": "receipt-1",
            "gate_consumption_id": "consumption-1",
        }
    return AuthorizationContext(
        effective_mode=mode,
        mode_epoch="mode-1",
        release_manifest_hash="a" * 64,
        spec_hash="b" * 64,
        config_hash="c" * 64,
        source_baseline_hash="d" * 64,
        authorization_lineage_id="lineage-1",
        **gate_fields,
    )


def test_repeated_idempotency_key_returns_the_existing_run_ref() -> None:
    root = _case_root("run-service-idempotency")
    service = RunService(RunStore(root))

    first = service.start_run(
        idempotency_key="daily:2026-07-26:ingest",
        command="ingest",
        authorization=_authorization(),
    )
    second = service.start_run(
        idempotency_key="daily:2026-07-26:ingest",
        command="ingest",
        authorization=_authorization(),
    )

    assert second == first
    assert len(tuple((root / "idempotency").glob("*.json"))) == 1
    assert len(tuple((root / "tokens").glob("*.json"))) == 1
    assert len(service.get_run(first.run_id).events) == 1


def test_same_key_with_different_request_is_blocked_without_new_artifacts() -> None:
    root = _case_root("run-service-conflict")
    service = RunService(RunStore(root))
    service.start_run(
        idempotency_key="stable-key",
        command="ingest",
        authorization=_authorization(),
    )
    before = tuple(root.rglob("*.json"))

    with pytest.raises(ValueError, match="different request"):
        service.start_run(
            idempotency_key="stable-key",
            command="curate",
            authorization=_authorization(),
        )

    assert tuple(root.rglob("*.json")) == before


def test_retry_always_creates_a_new_recovery_run_and_preserves_incident_root() -> None:
    root = _case_root("run-service-retry")
    store = RunStore(root)
    service = RunService(store)
    original = service.start_run(
        idempotency_key="original",
        command="ingest",
        authorization=_authorization(),
    )
    store.append_run_event(
        run_id=original.run_id,
        fencing_token=original.fencing_token,
        event_type=EventType.ABORTED,
        payload={"run_status": RunStatus.BLOCKED.value},
    )

    first_retry = service.retry_run(original.run_id)
    store.append_run_event(
        run_id=first_retry.run_id,
        fencing_token=first_retry.fencing_token,
        event_type=EventType.ABORTED,
        payload={"run_status": RunStatus.BLOCKED.value},
    )
    second_retry = service.retry_run(first_retry.run_id)

    assert len({original.run_id, first_retry.run_id, second_retry.run_id}) == 3
    assert first_retry.incident_root_run_id == original.run_id
    assert first_retry.recovers_run_id == original.run_id
    assert second_retry.incident_root_run_id == original.run_id
    assert second_retry.recovers_run_id == first_retry.run_id
    assert service.get_run(original.run_id).status is RunStatus.BLOCKED


def test_verify_uses_the_current_fencing_token_and_updates_canonical_view() -> None:
    root = _case_root("run-service-verify")
    service = RunService(RunStore(root))
    run = service.start_run(
        idempotency_key="verify-me",
        command="ingest",
        authorization=_authorization(),
    )

    result = service.verify_run(run.run_id)
    view = service.get_run(run.run_id)

    assert result.safe is True
    assert result.run_id == run.run_id
    assert result.safety_summary["delete_count"] == 0
    assert view.stages["verify"].value == "SUCCEEDED"


def test_stale_run_cannot_be_verified_after_a_new_owner_is_fenced_in() -> None:
    root = _case_root("run-service-stale")
    service = RunService(RunStore(root))
    stale = service.start_run(
        idempotency_key="stale",
        command="ingest",
        authorization=_authorization(),
    )
    service.start_run(
        idempotency_key="current",
        command="ingest",
        authorization=_authorization(),
    )
    before = tuple((root / "runs" / stale.run_id).glob("*.json"))

    with pytest.raises(FencingError, match="stale"):
        service.verify_run(stale.run_id)

    assert tuple((root / "runs" / stale.run_id).glob("*.json")) == before


def test_gate_check_never_elevates_missing_authorization() -> None:
    service = RunService(RunStore(_case_root("run-service-gate")))

    shadow = service.check_gate(ExecutionMode.SHADOW, _authorization())
    missing = service.check_gate(ExecutionMode.OBSERVED, None)
    observed = service.check_gate(
        ExecutionMode.OBSERVED,
        _authorization(ExecutionMode.OBSERVED),
    )

    assert shadow.allowed is True
    assert missing.allowed is False
    assert observed.allowed is True


def _capacity(*, total_gib: int, free_bytes: int):
    return CapacityPolicy().decide(
        total_bytes=total_gib * 1024**3,
        free_bytes=free_bytes,
        estimated_run_bytes=1024**2,
        daily_growth_bytes=0,
        now=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    "field",
    [
        "existing_delete_count",
        "existing_move_count",
        "overwrite_count",
        "out_of_scope_write_count",
        "new_artifact_disappear_count",
        "new_artifact_move_count",
        "new_artifact_truncate_count",
        "monitor_overflow_count",
        "monitor_gap_count",
    ],
)
def test_each_safety_risk_blocks_the_run_and_cannot_commit(field: str) -> None:
    root = _case_root(f"safety-{field}")
    service = RunService(RunStore(root))
    run = service.start_run(
        idempotency_key=f"safety:{field}",
        command="ingest",
        authorization=_authorization(),
    )
    inputs = SafetyInputs(**{field: 1})

    result = service.verify_run(
        run.run_id,
        safety_inputs=inputs,
        capacity=_capacity(total_gib=100, free_bytes=50 * 1024**3),
    )

    assert result.safe is False
    assert result.overall_status is RunStatus.BLOCKED
    assert service.get_run(run.run_id).status is RunStatus.BLOCKED
    assert not tuple((root / "commits").glob("*.json"))


def test_source_view_reduction_is_a_machine_count_and_blocks() -> None:
    root = _case_root("safety-source-reduction")
    service = RunService(RunStore(root))
    run = service.start_run(
        idempotency_key="source-reduction",
        command="ingest",
        authorization=_authorization(),
    )

    result = service.verify_run(
        run.run_id,
        safety_inputs=SafetyInputs(
            observed_sources_before=10,
            observed_sources_after=9,
            fetchable_sources_before=7,
            fetchable_sources_after=6,
            disabled_sources_before=2,
            disabled_sources_after=1,
        ),
        capacity=_capacity(total_gib=100, free_bytes=50 * 1024**3),
    )

    assert result.safety_summary["source_reduction_count"] == 3
    assert result.overall_status is RunStatus.BLOCKED


def test_blocked_capacity_writes_only_one_bounded_minimal_event() -> None:
    root = _case_root("safety-capacity-blocked")
    service = RunService(RunStore(root))
    run = service.start_run(
        idempotency_key="capacity-blocked",
        command="ingest",
        authorization=_authorization(),
    )
    decision = _capacity(total_gib=100, free_bytes=3 * 1024**3)
    before = tuple(root.rglob("*.json"))

    result = service.verify_run(
        run.run_id,
        safety_inputs=SafetyInputs(),
        capacity=decision,
    )
    after = tuple(root.rglob("*.json"))
    created = [path for path in after if path not in before]

    assert result.safe is False
    assert result.overall_status is RunStatus.BLOCKED
    assert result.persisted is True
    assert len(created) == 1
    assert created[0].parent == root / "events"
    assert created[0].stat().st_size <= decision.max_blocked_event_bytes
    assert service.get_run(run.run_id).status is RunStatus.PENDING


def test_critical_capacity_performs_zero_disk_writes() -> None:
    root = _case_root("safety-capacity-critical")
    service = RunService(RunStore(root))
    run = service.start_run(
        idempotency_key="capacity-critical",
        command="ingest",
        authorization=_authorization(),
    )
    before = tuple(
        (str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in root.rglob("*")
    )

    result = service.verify_run(
        run.run_id,
        safety_inputs=SafetyInputs(),
        capacity=_capacity(total_gib=100, free_bytes=65536),
    )
    after = tuple(
        (str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in root.rglob("*")
    )

    assert result.safe is False
    assert result.persisted is False
    assert result.overall_status is RunStatus.BLOCKED
    assert after == before


def test_feedback_actions_are_append_only_idempotent_and_never_write_vault() -> None:
    root = _case_root("feedback-events")
    runtime = root / "runtime"
    vault = root / "vault"
    runtime.mkdir()
    vault.mkdir()
    existing = vault / "existing.md"
    with existing.open("xb") as stream:
        stream.write(b"# existing knowledge\n")
        stream.flush()
        os.fsync(stream.fileno())
    before_hash = hashlib.sha256(existing.read_bytes()).hexdigest()
    service = RunService(RunStore(runtime))
    run = service.start_run(
        idempotency_key="feedback:source-run",
        command="curate",
        authorization=_authorization(),
    )

    receipts = [
        service.record_feedback(
            run_id=run.run_id,
            item_id="item-1",
            action=action,
            idempotency_key=f"feedback:{action.value}",
            reason="user review",
        )
        for action in FeedbackAction
    ]
    repeated = service.record_feedback(
        run_id=run.run_id,
        item_id="item-1",
        action=FeedbackAction.PROMOTE,
        idempotency_key="feedback:promote",
        reason="user review",
    )

    assert repeated == receipts[0]
    assert len(tuple((runtime / "events").glob("feedback-*.json"))) == 3
    assert hashlib.sha256(existing.read_bytes()).hexdigest() == before_hash
    assert not (vault / "20-Knowledge").exists()


def test_feedback_idempotency_key_cannot_be_rebound() -> None:
    root = _case_root("feedback-conflict")
    service = RunService(RunStore(root))
    run = service.start_run(
        idempotency_key="feedback:run",
        command="curate",
        authorization=_authorization(),
    )
    service.record_feedback(
        run_id=run.run_id,
        item_id="item-1",
        action=FeedbackAction.SKIP,
        idempotency_key="feedback:stable",
    )
    before = tuple((root / "events").glob("feedback-*.json"))

    with pytest.raises(ValueError, match="different feedback"):
        service.record_feedback(
            run_id=run.run_id,
            item_id="item-1",
            action=FeedbackAction.REVISIT,
            idempotency_key="feedback:stable",
        )

    assert tuple((root / "events").glob("feedback-*.json")) == before


def _gate_hashes() -> dict[str, str]:
    return {
        "baseline_hash": "1" * 64,
        "evidence_hash": "2" * 64,
        "release_hash": "3" * 64,
        "spec_hash": "4" * 64,
        "config_hash": "5" * 64,
        "source_baseline_hash": "6" * 64,
    }


def test_gate_record_requires_local_tty_and_derived_windows_identity() -> None:
    service = RunService(RunStore(_case_root("gate-tty")))
    challenge, phrase = service.create_gate_challenge(
        gate=GateKind.A,
        **_gate_hashes(),
    )

    with pytest.raises(PermissionError, match="TTY"):
        service.record_gate_from_tty(
            challenge_id=challenge.challenge_id,
            typed_phrase=phrase,
            is_tty=False,
            caller="local_cli",
            identity_resolver=lambda: ("S-1-5-21-test", "owner"),
        )
    with pytest.raises(PermissionError, match="local CLI"):
        service.record_gate_from_tty(
            challenge_id=challenge.challenge_id,
            typed_phrase=phrase,
            is_tty=True,
            caller="http",
            identity_resolver=lambda: ("S-1-5-21-test", "owner"),
        )


def test_gate_a_receipt_is_single_pipeline_and_hash_bound() -> None:
    service = RunService(RunStore(_case_root("gate-a")))
    hashes = _gate_hashes()
    challenge, phrase = service.create_gate_challenge(
        gate=GateKind.A,
        **hashes,
    )
    receipt = service.record_gate_from_tty(
        challenge_id=challenge.challenge_id,
        typed_phrase=phrase,
        is_tty=True,
        caller="local_cli",
        identity_resolver=lambda: ("S-1-5-21-test", "owner"),
    )

    with pytest.raises(PermissionError, match="drift"):
        service.consume_gate_receipt(
            receipt_id=receipt.receipt_id,
            requested_mode=ExecutionMode.OBSERVED,
            current_windows_identity="S-1-5-21-test",
            **{**hashes, "config_hash": "9" * 64},
        )

    authorization = service.consume_gate_receipt(
        receipt_id=receipt.receipt_id,
        requested_mode=ExecutionMode.OBSERVED,
        current_windows_identity="S-1-5-21-test",
        **hashes,
    )

    assert authorization.effective_mode is ExecutionMode.OBSERVED
    assert authorization.gate_receipt_id == receipt.receipt_id
    assert authorization.gate_consumption_id
    with pytest.raises(PermissionError, match="consumed"):
        service.consume_gate_receipt(
            receipt_id=receipt.receipt_id,
            requested_mode=ExecutionMode.OBSERVED,
            current_windows_identity="S-1-5-21-test",
            **hashes,
        )


def test_gate_challenge_and_receipt_cannot_be_replayed_or_used_after_expiry() -> None:
    service = RunService(RunStore(_case_root("gate-expiry")))
    challenge, phrase = service.create_gate_challenge(
        gate=GateKind.A,
        lifetime_seconds=-1,
        **_gate_hashes(),
    )
    with pytest.raises(PermissionError, match="expired"):
        service.record_gate_from_tty(
            challenge_id=challenge.challenge_id,
            typed_phrase=phrase,
            is_tty=True,
            caller="local_cli",
            identity_resolver=lambda: ("S-1-5-21-test", "owner"),
        )

    challenge, phrase = service.create_gate_challenge(
        gate=GateKind.A,
        **_gate_hashes(),
    )
    service.record_gate_from_tty(
        challenge_id=challenge.challenge_id,
        typed_phrase=phrase,
        is_tty=True,
        caller="local_cli",
        identity_resolver=lambda: ("S-1-5-21-test", "owner"),
    )
    with pytest.raises(PermissionError, match="recorded"):
        service.record_gate_from_tty(
            challenge_id=challenge.challenge_id,
            typed_phrase=phrase,
            is_tty=True,
            caller="local_cli",
            identity_resolver=lambda: ("S-1-5-21-test", "owner"),
        )


def test_gate_b_mode_epoch_is_permanently_superseded_by_shadow_rollback() -> None:
    root = _case_root("gate-b-rollback")
    service = RunService(RunStore(root))
    hashes = _gate_hashes()
    challenge, phrase = service.create_gate_challenge(
        gate=GateKind.B,
        **hashes,
    )
    receipt = service.record_gate_from_tty(
        challenge_id=challenge.challenge_id,
        typed_phrase=phrase,
        is_tty=True,
        caller="local_cli",
        identity_resolver=lambda: ("S-1-5-21-test", "owner"),
    )
    authorization = service.consume_gate_receipt(
        receipt_id=receipt.receipt_id,
        requested_mode=ExecutionMode.UNATTENDED,
        current_windows_identity="S-1-5-21-test",
        **hashes,
    )

    service.transition_mode(
        target_mode=ExecutionMode.SHADOW,
        reason="owner rollback",
    )

    assert (
        service.check_gate(
            ExecutionMode.UNATTENDED,
            authorization,
        ).allowed
        is False
    )
    with pytest.raises(PermissionError, match="consumed"):
        service.consume_gate_receipt(
            receipt_id=receipt.receipt_id,
            requested_mode=ExecutionMode.UNATTENDED,
            current_windows_identity="S-1-5-21-test",
            **hashes,
        )
