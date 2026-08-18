"""Application use-case layer for canonical PKM workflow runs."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from path_policy import CapacityDecision, CapacityState
from run_store import RunStore, RunStoreCorruption
from workflow_contracts import (
    AuthorizationContext,
    EventType,
    ExecutionMode,
    FeedbackAction,
    FeedbackReceipt,
    GateChallenge,
    GateKind,
    GateReceipt,
    GateResult,
    RunRef,
    RunStatus,
    RunView,
    SafetyInputs,
    StageStatus,
    VerificationResult,
)


class RunBudgetExceeded(RuntimeError):
    pass


class LlmBudgetLimits(Protocol):
    @property
    def max_candidates(self) -> int: ...

    @property
    def max_context_chars(self) -> int: ...

    @property
    def max_calls_per_run(self) -> int: ...

    @property
    def max_retries_per_call(self) -> int: ...

    @property
    def max_daily_calls(self) -> int: ...

    @property
    def max_weekly_calls(self) -> int: ...


@dataclass(frozen=True)
class BudgetReservation:
    run_id: str
    day: str
    week: str
    requested_calls: int
    retry_count: int


class RunService:
    """The only use-case API shared by CLI, HTTP, scheduler, and Codex."""

    def __init__(self, store: RunStore) -> None:
        self.store = store

    def start_run(
        self,
        *,
        idempotency_key: str,
        command: str,
        authorization: AuthorizationContext,
        incident_root_run_id: str | None = None,
        recovers_run_id: str | None = None,
    ) -> RunRef:
        gate = self.check_gate(authorization.effective_mode, authorization)
        if not gate.allowed:
            raise PermissionError("; ".join(gate.reasons))
        record = self.store.create_or_get_run(
            run_id=f"run-{uuid.uuid4()}",
            idempotency_key=idempotency_key,
            command=command,
            authorization=authorization.model_dump(mode="json"),
            incident_root_run_id=incident_root_run_id,
            recovers_run_id=recovers_run_id,
        )
        return self._record_to_ref(record)

    def get_run(self, run_id: str) -> RunView:
        state = self.store.replay_run(run_id)
        if not state.events:
            raise RunStoreCorruption(f"run has no creation event: {run_id}")
        payload = state.events[0].payload
        ref = RunRef(
            run_id=run_id,
            idempotency_key=str(payload["idempotency_key"]),
            fencing_token=state.events[0].fencing_token,
            command=str(payload["command"]),
            authorization=AuthorizationContext.model_validate(payload["authorization"]),
            incident_root_run_id=self._optional_string(payload.get("incident_root_run_id")),
            recovers_run_id=self._optional_string(payload.get("recovers_run_id")),
        )
        return RunView(
            ref=ref,
            status=state.status,
            stages=state.stages,
            events=state.events,
        )

    def retry_run(self, run_id: str) -> RunRef:
        previous = self.get_run(run_id)
        if previous.status not in {
            RunStatus.PARTIAL,
            RunStatus.BLOCKED,
            RunStatus.FAILED,
        }:
            raise ValueError("only a terminal non-committed run can be retried")
        incident_root = previous.ref.incident_root_run_id or previous.ref.run_id
        authorization = previous.ref.authorization
        gate = self.check_gate(authorization.effective_mode, authorization)
        if not gate.allowed:
            raise PermissionError("retry authorization is no longer valid")
        return self.start_run(
            idempotency_key=f"retry:{incident_root}:{uuid.uuid4()}",
            command=previous.ref.command,
            authorization=authorization.model_copy(update={"parent_run_id": previous.ref.run_id}),
            incident_root_run_id=incident_root,
            recovers_run_id=previous.ref.run_id,
        )

    def reserve_llm_budget(
        self,
        *,
        run_id: str,
        candidate_count: int,
        context_chars: int,
        requested_calls: int,
        retry_count: int,
        day: str,
        week: str,
        limits: LlmBudgetLimits,
    ) -> BudgetReservation:
        """Reserve a bounded model call or terminally block the run."""

        view = self.get_run(run_id)
        if view.status is not RunStatus.PENDING:
            raise RunBudgetExceeded("only a pending run can reserve LLM budget")
        if (
            min(
                candidate_count,
                context_chars,
                requested_calls,
                retry_count,
            )
            < 0
            or requested_calls < 1
        ):
            raise ValueError("budget request counts are invalid")

        prior_run_calls = 0
        prior_daily_calls = 0
        prior_weekly_calls = 0
        for run_root in self.store.runs_root.iterdir():
            if not run_root.is_dir():
                continue
            state = self.store.replay_run(run_root.name)
            for event in state.events:
                if event.event_type is not EventType.LLM_CALL_RESERVED:
                    continue
                calls = int(event.payload.get("requested_calls", 0))
                if event.run_id == run_id:
                    prior_run_calls += calls
                if event.payload.get("day") == day:
                    prior_daily_calls += calls
                if event.payload.get("week") == week:
                    prior_weekly_calls += calls

        reasons: list[str] = []
        checks = (
            (
                candidate_count > int(limits.max_candidates),
                "candidates exceed hard limit",
            ),
            (
                context_chars > int(limits.max_context_chars),
                "context exceeds hard limit",
            ),
            (
                prior_run_calls + requested_calls > int(limits.max_calls_per_run),
                "run calls exceed hard limit",
            ),
            (
                retry_count > int(limits.max_retries_per_call),
                "retries exceed hard limit",
            ),
            (
                prior_daily_calls + requested_calls > int(limits.max_daily_calls),
                "daily calls exceed hard limit",
            ),
            (
                prior_weekly_calls + requested_calls > int(limits.max_weekly_calls),
                "weekly calls exceed hard limit",
            ),
        )
        reasons.extend(message for failed, message in checks if failed)
        if reasons:
            self.store.append_run_event(
                run_id=run_id,
                fencing_token=view.ref.fencing_token,
                event_type=EventType.STAGE_CHANGED,
                stage="curate",
                stage_status=StageStatus.BLOCKED,
                payload={
                    "run_status": RunStatus.BLOCKED.value,
                    "reason": "llm-budget",
                    "budget_reasons": reasons,
                },
            )
            raise RunBudgetExceeded("; ".join(reasons))

        self.store.append_run_event(
            run_id=run_id,
            fencing_token=view.ref.fencing_token,
            event_type=EventType.LLM_CALL_RESERVED,
            payload={
                "day": day,
                "week": week,
                "candidate_count": candidate_count,
                "context_chars": context_chars,
                "requested_calls": requested_calls,
                "retry_count": retry_count,
            },
        )
        return BudgetReservation(
            run_id=run_id,
            day=day,
            week=week,
            requested_calls=requested_calls,
            retry_count=retry_count,
        )

    def record_feedback(
        self,
        *,
        run_id: str,
        item_id: str,
        action: FeedbackAction,
        idempotency_key: str,
        reason: str = "",
    ) -> FeedbackReceipt:
        """Record review intent only; promotion never writes knowledge notes."""

        self.get_run(run_id)
        return self.store.record_feedback(
            run_id=run_id,
            item_id=item_id,
            action=action,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def create_gate_challenge(
        self,
        *,
        gate: GateKind,
        baseline_hash: str,
        evidence_hash: str,
        release_hash: str,
        spec_hash: str,
        config_hash: str,
        source_baseline_hash: str,
        lifetime_seconds: int = 600,
    ) -> tuple[GateChallenge, str]:
        nonce = uuid.uuid4().hex
        phrase = f"APPROVE PKM GATE {gate.value} {nonce}"
        challenge = GateChallenge(
            challenge_id=f"gate-{uuid.uuid4()}",
            gate=gate,
            nonce=nonce,
            phrase_hash=hashlib.sha256(phrase.encode("utf-8")).hexdigest(),
            baseline_hash=baseline_hash,
            evidence_hash=evidence_hash,
            release_hash=release_hash,
            spec_hash=spec_hash,
            config_hash=config_hash,
            source_baseline_hash=source_baseline_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds),
        )
        return self.store.create_gate_challenge(challenge), phrase

    def record_gate_from_tty(
        self,
        *,
        challenge_id: str,
        typed_phrase: str,
        is_tty: bool,
        caller: str,
        identity_resolver: Callable[[], tuple[str, str]],
    ) -> GateReceipt:
        if not is_tty:
            raise PermissionError("gate receipt requires a local TTY")
        if caller != "local_cli":
            raise PermissionError("gate receipt is available only to the local CLI")
        challenge = self.store.load_gate_challenge(challenge_id)
        if datetime.now(timezone.utc) > challenge.expires_at:
            raise PermissionError("gate challenge expired")
        phrase = f"APPROVE PKM GATE {challenge.gate.value} {challenge.nonce}"
        if not hmac.compare_digest(
            hashlib.sha256(typed_phrase.encode("utf-8")).hexdigest(),
            challenge.phrase_hash,
        ) or not hmac.compare_digest(typed_phrase, phrase):
            raise PermissionError("gate challenge phrase did not match")
        windows_sid, account_name = identity_resolver()
        if not windows_sid.strip() or not account_name.strip():
            raise PermissionError("Windows identity could not be derived")
        challenge_hash = hashlib.sha256(challenge.model_dump_json().encode("utf-8")).hexdigest()
        receipt = GateReceipt(
            receipt_id=f"receipt-{uuid.uuid4()}",
            challenge_id=challenge.challenge_id,
            gate=challenge.gate,
            windows_sid_hash=hashlib.sha256(windows_sid.encode("utf-8")).hexdigest(),
            windows_account_hash=hashlib.sha256(account_name.encode("utf-8")).hexdigest(),
            challenge_hash=challenge_hash,
            issued_at=datetime.now(timezone.utc),
        )
        return self.store.record_gate_receipt(receipt)

    def consume_gate_receipt(
        self,
        *,
        receipt_id: str,
        requested_mode: ExecutionMode,
        current_windows_identity: str,
        baseline_hash: str,
        evidence_hash: str,
        release_hash: str,
        spec_hash: str,
        config_hash: str,
        source_baseline_hash: str,
    ) -> AuthorizationContext:
        receipt = self.store.load_gate_receipt(receipt_id)
        challenge = self.store.load_gate_challenge(receipt.challenge_id)
        expected_mode = (
            ExecutionMode.OBSERVED if receipt.gate is GateKind.A else ExecutionMode.UNATTENDED
        )
        if requested_mode is not expected_mode:
            raise PermissionError("gate kind cannot authorize requested mode")
        if datetime.now(timezone.utc) > challenge.expires_at:
            raise PermissionError("gate receipt challenge expired")
        if (
            receipt.windows_sid_hash
            != hashlib.sha256(current_windows_identity.encode("utf-8")).hexdigest()
        ):
            raise PermissionError("Windows SID drift blocks gate consumption")
        supplied_hashes = {
            "baseline_hash": baseline_hash,
            "evidence_hash": evidence_hash,
            "release_hash": release_hash,
            "spec_hash": spec_hash,
            "config_hash": config_hash,
            "source_baseline_hash": source_baseline_hash,
        }
        expected_hashes = {key: getattr(challenge, key) for key in supplied_hashes}
        if supplied_hashes != expected_hashes:
            raise PermissionError("gate-bound hash drift detected")
        lineage_id = f"lineage-{uuid.uuid4()}"
        mode_epoch = f"mode-{uuid.uuid4()}"
        record = self.store.consume_gate_receipt(
            receipt=receipt,
            challenge=challenge,
            requested_mode=requested_mode,
            authorization_lineage_id=lineage_id,
            mode_epoch=mode_epoch,
        )
        return AuthorizationContext(
            effective_mode=requested_mode,
            mode_epoch=mode_epoch,
            release_manifest_hash=release_hash,
            spec_hash=spec_hash,
            config_hash=config_hash,
            source_baseline_hash=source_baseline_hash,
            gate_receipt_id=receipt.receipt_id,
            gate_consumption_id=str(record["consumption_id"]),
            authorization_lineage_id=lineage_id,
        )

    def transition_mode(
        self,
        *,
        target_mode: ExecutionMode,
        reason: str,
    ) -> dict[str, object]:
        if target_mode is not ExecutionMode.SHADOW:
            raise PermissionError("mode elevation requires a new consumed Gate receipt")
        return self.store.transition_mode_to_shadow(reason)

    def verify_run(
        self,
        run_id: str,
        *,
        safety_inputs: SafetyInputs | None = None,
        capacity: CapacityDecision | None = None,
    ) -> VerificationResult:
        view = self.get_run(run_id)
        if view.status is not RunStatus.PENDING:
            raise ValueError("only a pending run can receive an in-place verification")
        inputs = safety_inputs or SafetyInputs()
        source_reduction_count = sum(
            max(0, before - after)
            for before, after in (
                (
                    inputs.observed_sources_before,
                    inputs.observed_sources_after,
                ),
                (
                    inputs.fetchable_sources_before,
                    inputs.fetchable_sources_after,
                ),
                (
                    inputs.disabled_sources_before,
                    inputs.disabled_sources_after,
                ),
                (
                    inputs.unresolved_sources_before,
                    inputs.unresolved_sources_after,
                ),
            )
        )
        safety_summary = {
            "source_reduction_count": source_reduction_count,
            "delete_count": inputs.existing_delete_count,
            "move_count": inputs.existing_move_count,
            "overwrite_count": inputs.overwrite_count,
            "out_of_scope_write_count": inputs.out_of_scope_write_count,
            "new_artifact_disappear_count": (inputs.new_artifact_disappear_count),
            "new_artifact_move_count": inputs.new_artifact_move_count,
            "truncate_count": inputs.new_artifact_truncate_count,
            "monitor_overflow_count": inputs.monitor_overflow_count,
            "monitor_gap_count": inputs.monitor_gap_count,
            "capacity_block_count": (0 if capacity is None or capacity.can_write_content else 1),
        }
        risk_count = sum(safety_summary.values())
        if risk_count:
            return self._record_blocked_verification(
                view=view,
                safety_summary=safety_summary,
                capacity=capacity,
                risk_count=risk_count,
            )
        event = self.store.append_run_event(
            run_id=run_id,
            fencing_token=view.ref.fencing_token,
            event_type=EventType.STAGE_CHANGED,
            stage="verify",
            stage_status=StageStatus.SUCCEEDED,
            payload={
                "run_status": RunStatus.PENDING.value,
                "safety_summary": safety_summary,
            },
        )
        return VerificationResult(
            run_id=run_id,
            safe=True,
            safety_summary=safety_summary,
            overall_status=RunStatus.PENDING,
            persisted=True,
            event_id=event.event_id,
        )

    def _record_blocked_verification(
        self,
        *,
        view: RunView,
        safety_summary: dict[str, int],
        capacity: CapacityDecision | None,
        risk_count: int,
    ) -> VerificationResult:
        authorization_payload = view.ref.authorization.model_dump(mode="json")
        if capacity is not None and capacity.state is CapacityState.CRITICAL:
            return VerificationResult(
                run_id=view.ref.run_id,
                safe=False,
                safety_summary=safety_summary,
                overall_status=RunStatus.BLOCKED,
                persisted=False,
            )
        if capacity is not None and not capacity.can_write_content:
            if not capacity.can_write_blocked_event:
                return VerificationResult(
                    run_id=view.ref.run_id,
                    safe=False,
                    safety_summary=safety_summary,
                    overall_status=RunStatus.BLOCKED,
                    persisted=False,
                )
            path = self.store.write_minimal_blocked_event(
                run_id=view.ref.run_id,
                fencing_token=view.ref.fencing_token,
                authorization=authorization_payload,
                capacity_state=capacity.state.value,
                risk_count=risk_count,
                max_bytes=capacity.max_blocked_event_bytes,
            )
            return VerificationResult(
                run_id=view.ref.run_id,
                safe=False,
                safety_summary=safety_summary,
                overall_status=RunStatus.BLOCKED,
                persisted=True,
                event_id=path.name,
            )
        event = self.store.append_run_event(
            run_id=view.ref.run_id,
            fencing_token=view.ref.fencing_token,
            event_type=EventType.ABORTED,
            stage="verify",
            stage_status=StageStatus.BLOCKED,
            payload={
                "run_status": RunStatus.BLOCKED.value,
                "safety_summary": safety_summary,
            },
        )
        return VerificationResult(
            run_id=view.ref.run_id,
            safe=False,
            safety_summary=safety_summary,
            overall_status=RunStatus.BLOCKED,
            persisted=True,
            event_id=event.event_id,
        )

    def check_gate(
        self,
        requested_mode: ExecutionMode,
        authorization: AuthorizationContext | None,
    ) -> GateResult:
        reasons: list[str] = []
        if authorization is None:
            reasons.append("authorization context is required")
        elif authorization.effective_mode is not requested_mode:
            reasons.append("authorization mode does not match requested mode")
        elif requested_mode is not ExecutionMode.SHADOW:
            if not authorization.gate_receipt_id:
                reasons.append("gate receipt is required")
            if not authorization.gate_consumption_id:
                reasons.append("gate consumption is required")
            if not self.store.gate_authorization_is_current(authorization):
                reasons.append("gate authorization is not current")
        return GateResult(
            requested_mode=requested_mode,
            allowed=not reasons,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _record_to_ref(record: dict[str, object]) -> RunRef:
        fencing_token = record["fencing_token"]
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
            raise RunStoreCorruption("fencing_token must be an integer")
        return RunRef(
            run_id=str(record["run_id"]),
            idempotency_key=str(record["idempotency_key"]),
            fencing_token=fencing_token,
            command=str(record["command"]),
            authorization=AuthorizationContext.model_validate(record["authorization"]),
            incident_root_run_id=RunService._optional_string(record.get("incident_root_run_id")),
            recovers_run_id=RunService._optional_string(record.get("recovers_run_id")),
        )

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return None if value is None else str(value)
