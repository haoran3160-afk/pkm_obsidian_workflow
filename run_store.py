"""Append-only run store with a permanent one-byte coordination lock."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from workflow_contracts import (
    AuthorizationContext,
    CommitResult,
    CommitStatus,
    EventType,
    ExecutionMode,
    FeedbackAction,
    FeedbackReceipt,
    GateChallenge,
    GateReceipt,
    RunEvent,
    RunStatus,
    StageStatus,
    assert_run_transition,
)

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TOKEN_FILE_PATTERN = re.compile(r"^(\d{20})-([A-Za-z0-9][A-Za-z0-9._-]*)\.json$")
EVENT_FILE_PATTERN = re.compile(r"^(\d{8})-[a-z0-9-]+\.json$")
IDEMPOTENCY_FILE_PATTERN = re.compile(r"^[0-9a-f]{64}\.json$")


class RunStoreError(RuntimeError):
    pass


class RunStoreCorruption(RunStoreError):
    pass


class LockTimeout(RunStoreError):
    pass


class FencingError(RunStoreError):
    pass


class ReservationConflict(RunStoreError):
    pass


@dataclass(frozen=True)
class DerivedRunState:
    run_id: str
    status: RunStatus
    stages: dict[str, StageStatus]
    events: tuple[RunEvent, ...]


def _validate_id(value: str, label: str) -> str:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} contains unsafe characters")
    return value


def _write_json_create_new(path: Path, value: object) -> None:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class RunStore:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root).resolve(strict=True)
        if not self.runtime_root.is_dir():
            raise ValueError("runtime_root must be a directory")
        self.tokens_root = self.runtime_root / "tokens"
        self.runs_root = self.runtime_root / "runs"
        self.events_root = self.runtime_root / "events"
        self.reservations_root = self.runtime_root / "reservations"
        self.commits_root = self.runtime_root / "commits"
        self.partial_root = self.runtime_root / "partial"
        self.idempotency_root = self.runtime_root / "idempotency"
        self.gate_challenges_root = self.runtime_root / "gate-challenges"
        self.gate_receipts_root = self.runtime_root / "gate-receipts"
        self.gate_consumptions_root = self.runtime_root / "gate-consumptions"
        self.mode_events_root = self.runtime_root / "mode-events"
        for directory in (
            self.tokens_root,
            self.runs_root,
            self.events_root,
            self.reservations_root,
            self.commits_root,
            self.partial_root,
            self.idempotency_root,
            self.gate_challenges_root,
            self.gate_receipts_root,
            self.gate_consumptions_root,
            self.mode_events_root,
        ):
            directory.mkdir(exist_ok=True)
        self.lock_path = self.runtime_root / "control.lock"
        self._ensure_lock_anchor()

    def _ensure_lock_anchor(self) -> None:
        try:
            with self.lock_path.open("xb") as stream:
                stream.write(b"\x00")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        if self.lock_path.read_bytes() != b"\x00":
            raise RunStoreCorruption("control.lock must remain exactly one zero byte")

    @contextmanager
    def coordination_lock(self, timeout_seconds: float = 5.0) -> Iterator[None]:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        stream = self.lock_path.open("r+b", buffering=0)
        deadline = time.monotonic() + timeout_seconds
        locked = False
        try:
            while not locked:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl = importlib.import_module("fcntl")
                        fcntl.flock(
                            stream.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                    locked = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise LockTimeout("control.lock acquisition timed out") from exc
                    time.sleep(0.05)
            stream.seek(0)
            if stream.read(1) != b"\x00":
                raise RunStoreCorruption("control.lock content changed while locked")
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def current_fencing_token(self) -> int:
        latest = 0
        for path in self.tokens_root.glob("*.json"):
            match = TOKEN_FILE_PATTERN.fullmatch(path.name)
            if match is None:
                raise RunStoreCorruption(f"invalid token event filename: {path.name}")
            sequence = int(match.group(1))
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RunStoreCorruption(f"invalid token event: {path}") from exc
            if int(payload.get("fencing_token", -1)) != sequence:
                raise RunStoreCorruption(f"token sequence mismatch: {path}")
            latest = max(latest, sequence)
        return latest

    def allocate_fencing_token(self, run_id: str) -> int:
        safe_run_id = _validate_id(run_id, "run_id")
        with self.coordination_lock():
            sequence = self.current_fencing_token() + 1
            target = self.tokens_root / f"{sequence:020d}-{safe_run_id}.json"
            _write_json_create_new(
                target,
                {
                    "schema_version": 1,
                    "fencing_token": sequence,
                    "run_id": safe_run_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return sequence

    def create_or_get_run(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        command: str,
        authorization: dict[str, object],
        incident_root_run_id: str | None = None,
        recovers_run_id: str | None = None,
    ) -> dict[str, object]:
        """Atomically resolve an idempotency key or create its first run.

        The mapping is written with CREATE_NEW while holding the permanent
        coordination lock. A repeated request can only reuse the mapping when
        its canonical request hash is identical.
        """

        safe_run_id = _validate_id(run_id, "run_id")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key cannot be blank")
        if not command.strip():
            raise ValueError("command cannot be blank")
        for label, value in (
            ("incident_root_run_id", incident_root_run_id),
            ("recovers_run_id", recovers_run_id),
        ):
            if value is not None:
                _validate_id(value, label)

        request = {
            "idempotency_key": idempotency_key,
            "command": command,
            "authorization": authorization,
            "incident_root_run_id": incident_root_run_id,
            "recovers_run_id": recovers_run_id,
        }
        canonical_request = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_hash = hashlib.sha256(canonical_request).hexdigest()
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        mapping_path = self.idempotency_root / f"{key_hash}.json"

        with self.coordination_lock():
            if mapping_path.exists():
                record = self._read_idempotency_record(mapping_path)
                if (
                    record["idempotency_key"] != idempotency_key
                    or record["request_hash"] != request_hash
                ):
                    raise ValueError("idempotency key is already bound to a different request")
                return record

            fencing_token = self.current_fencing_token() + 1
            token_path = self.tokens_root / f"{fencing_token:020d}-{safe_run_id}.json"
            created_at = datetime.now(timezone.utc)
            _write_json_create_new(
                token_path,
                {
                    "schema_version": 1,
                    "fencing_token": fencing_token,
                    "run_id": safe_run_id,
                    "created_at": created_at.isoformat(),
                },
            )

            run_root = self.runs_root / safe_run_id
            run_root.mkdir()
            event = RunEvent(
                event_id=f"event-{uuid.uuid4()}",
                run_id=safe_run_id,
                sequence=1,
                fencing_token=fencing_token,
                event_type=EventType.RUN_CREATED,
                created_at=created_at,
                payload={
                    "run_status": RunStatus.PENDING.value,
                    **request,
                },
            )
            _write_json_create_new(
                run_root / "00000001-run-created.json",
                event.model_dump(mode="json"),
            )
            record = {
                "schema_version": 1,
                "run_id": safe_run_id,
                "fencing_token": fencing_token,
                "request_hash": request_hash,
                "created_at": created_at.isoformat(),
                **request,
            }
            _write_json_create_new(mapping_path, record)
            return record

    def _read_idempotency_record(self, path: Path) -> dict[str, object]:
        if IDEMPOTENCY_FILE_PATTERN.fullmatch(path.name) is None:
            raise RunStoreCorruption(f"invalid idempotency filename: {path.name}")
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunStoreCorruption(f"invalid idempotency record: {path}") from exc
        required = {
            "idempotency_key",
            "request_hash",
            "run_id",
            "fencing_token",
            "command",
            "authorization",
        }
        if not isinstance(record, dict) or not required.issubset(record):
            raise RunStoreCorruption(f"incomplete idempotency record: {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record["request_hash"])):
            raise RunStoreCorruption(f"invalid request hash: {path}")
        _validate_id(str(record["run_id"]), "run_id")
        if int(record["fencing_token"]) < 1:
            raise RunStoreCorruption(f"invalid fencing token: {path}")
        return record

    def append_run_event(
        self,
        *,
        run_id: str,
        fencing_token: int,
        event_type: EventType,
        payload: dict[str, object],
        stage: str | None = None,
        stage_status: StageStatus | None = None,
    ) -> RunEvent:
        safe_run_id = _validate_id(run_id, "run_id")
        with self.coordination_lock():
            return self._append_run_event_unlocked(
                run_id=safe_run_id,
                fencing_token=fencing_token,
                event_type=event_type,
                payload=payload,
                stage=stage,
                stage_status=stage_status,
            )

    def _append_run_event_unlocked(
        self,
        *,
        run_id: str,
        fencing_token: int,
        event_type: EventType,
        payload: dict[str, object],
        stage: str | None = None,
        stage_status: StageStatus | None = None,
    ) -> RunEvent:
        current = self.current_fencing_token()
        if fencing_token != current:
            raise FencingError(f"stale fencing token {fencing_token}; current token is {current}")
        run_root = self.runs_root / run_id
        run_root.mkdir(exist_ok=True)
        existing = sorted(run_root.glob("*.json"))
        for path in existing:
            if EVENT_FILE_PATTERN.fullmatch(path.name) is None:
                raise RunStoreCorruption(f"invalid event filename: {path.name}")
        sequence = len(existing) + 1
        event = RunEvent(
            event_id=f"event-{uuid.uuid4()}",
            run_id=run_id,
            sequence=sequence,
            fencing_token=fencing_token,
            event_type=event_type,
            created_at=datetime.now(timezone.utc),
            stage=stage,
            stage_status=stage_status,
            payload=payload,
        )
        slug = event_type.value.lower().replace("_", "-")
        target = run_root / f"{sequence:08d}-{slug}.json"
        _write_json_create_new(target, event.model_dump(mode="json"))
        return event

    def assert_current_run_authorization(
        self,
        *,
        run_id: str,
        fencing_token: int,
        authorization: dict[str, object],
    ) -> None:
        current = self.current_fencing_token()
        if fencing_token != current:
            raise FencingError(f"stale fencing token {fencing_token}; current token is {current}")
        state = self.replay_run(run_id)
        if not state.events:
            raise RunStoreCorruption(f"run has no creation event: {run_id}")
        canonical = state.events[0].payload.get("authorization")
        if canonical != authorization:
            raise FencingError("run authorization differs from canonical state")

    def get_or_create_reservation(
        self,
        *,
        generation_id: str,
        run_id: str,
        fencing_token: int,
        authorization: dict[str, object],
        targets: list[dict[str, object]],
    ) -> tuple[dict[str, object], bool]:
        safe_generation_id = _validate_id(generation_id, "generation_id")
        safe_run_id = _validate_id(run_id, "run_id")
        request = {
            "generation_id": safe_generation_id,
            "run_id": safe_run_id,
            "fencing_token": fencing_token,
            "authorization": authorization,
            "targets": targets,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        reservation_path = self.reservations_root / f"{safe_generation_id}.writing.json"
        with self.coordination_lock():
            self.assert_current_run_authorization(
                run_id=safe_run_id,
                fencing_token=fencing_token,
                authorization=authorization,
            )
            if reservation_path.exists():
                try:
                    record = json.loads(reservation_path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RunStoreCorruption(f"invalid reservation: {reservation_path}") from exc
                if not isinstance(record, dict) or record.get("request_hash") != request_hash:
                    raise ReservationConflict(
                        "generation reservation differs from the requested artifacts"
                    )
                return record, False

            existing_targets = [
                str(target["absolute_path"])
                for target in targets
                if Path(str(target["absolute_path"])).exists()
            ]
            if existing_targets:
                raise ReservationConflict(
                    "target existed before reservation: " + ", ".join(existing_targets)
                )
            record = {
                "schema_version": 1,
                "request_hash": request_hash,
                "state": "WRITING",
                "created_at": datetime.now(timezone.utc).isoformat(),
                **request,
            }
            _write_json_create_new(reservation_path, record)
            return record, True

    def get_generation_result(self, generation_id: str) -> CommitResult | None:
        safe_generation_id = _validate_id(generation_id, "generation_id")
        candidates = (
            self.commits_root / f"{safe_generation_id}.commit.json",
            self.partial_root / f"{safe_generation_id}.partial.json",
        )
        existing = [path for path in candidates if path.exists()]
        if len(existing) > 1:
            raise RunStoreCorruption(
                f"generation has conflicting terminal markers: {safe_generation_id}"
            )
        if not existing:
            return None
        try:
            return CommitResult.model_validate_json(existing[0].read_text(encoding="utf-8-sig"))
        except (OSError, ValidationError, ValueError) as exc:
            raise RunStoreCorruption(f"invalid generation marker: {existing[0]}") from exc

    def generation_marker_path(
        self,
        generation_id: str,
        status: CommitStatus,
    ) -> Path:
        safe_generation_id = _validate_id(generation_id, "generation_id")
        if status is CommitStatus.COMMITTED:
            return self.commits_root / f"{safe_generation_id}.commit.json"
        return self.partial_root / f"{safe_generation_id}.partial.json"

    def record_generation_result(
        self,
        *,
        result: CommitResult,
        fencing_token: int,
        authorization: dict[str, object],
    ) -> CommitResult:
        target = self.generation_marker_path(result.generation_id, result.status)
        with self.coordination_lock():
            self.assert_current_run_authorization(
                run_id=result.run_id,
                fencing_token=fencing_token,
                authorization=authorization,
            )
            existing = self.get_generation_result(result.generation_id)
            if existing is not None:
                if existing != result:
                    raise ReservationConflict(
                        "generation terminal marker differs from requested result"
                    )
                return existing
            _write_json_create_new(target, result.model_dump(mode="json"))
            run_status = (
                RunStatus.COMMITTED
                if result.status is CommitStatus.COMMITTED
                else RunStatus.PARTIAL
            )
            stage_status = (
                StageStatus.SUCCEEDED
                if result.status is CommitStatus.COMMITTED
                else StageStatus.FAILED
            )
            self._append_run_event_unlocked(
                run_id=result.run_id,
                fencing_token=fencing_token,
                event_type=EventType.STAGE_CHANGED,
                stage="commit",
                stage_status=stage_status,
                payload={
                    "run_status": run_status.value,
                    "generation_id": result.generation_id,
                    "commit_status": result.status.value,
                },
            )
            return result

    def record_legacy_state_import(
        self,
        *,
        state_kind: str,
        source_path: str,
        source_sha256: str,
        legacy_payload: object,
        record_count: int,
        retention_resolution: dict[str, object],
    ) -> dict[str, object]:
        """Create one idempotent event for an exact legacy file snapshot."""

        safe_kind = _validate_id(state_kind, "state_kind")
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ValueError("source_sha256 must be lowercase SHA-256")
        if record_count < 0:
            raise ValueError("record_count cannot be negative")
        request = {
            "schema_version": 1,
            "event_type": "LEGACY_STATE_IMPORTED",
            "state_kind": safe_kind,
            "source_path": source_path,
            "source_sha256": source_sha256,
            "record_count": record_count,
            "retention_resolution": retention_resolution,
            "legacy_payload": legacy_payload,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        target = self.events_root / f"legacy-state-{safe_kind}-{request_hash[:24]}.json"
        with self.coordination_lock():
            if target.exists():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RunStoreCorruption(f"invalid legacy import event: {target}") from exc
                comparable = dict(existing)
                comparable.pop("imported_at", None)
                if comparable != request:
                    raise RunStoreCorruption(f"legacy import event differs from request: {target}")
                return existing
            record = {
                **request,
                "imported_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_create_new(target, record)
            return record

    def record_feedback(
        self,
        *,
        run_id: str,
        item_id: str,
        action: FeedbackAction,
        idempotency_key: str,
        reason: str,
    ) -> FeedbackReceipt:
        """Create an idempotent feedback event outside formal Vault content."""

        safe_run_id = _validate_id(run_id, "run_id")
        safe_item_id = _validate_id(item_id, "item_id")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key cannot be blank")
        self.replay_run(safe_run_id)
        request = {
            "schema_version": 1,
            "event_type": "USER_FEEDBACK",
            "feedback_id": "feedback-"
            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20],
            "run_id": safe_run_id,
            "item_id": safe_item_id,
            "action": action.value,
            "idempotency_key": idempotency_key,
            "reason": reason,
        }
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        target = self.events_root / f"feedback-{key_hash[:24]}.json"
        with self.coordination_lock():
            if target.exists():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RunStoreCorruption(f"invalid feedback event: {target}") from exc
                comparable = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"recorded_at", "event_path", "event_hash"}
                }
                if comparable != request:
                    raise ValueError("idempotency key is bound to different feedback")
                return FeedbackReceipt.model_validate(
                    {key: existing[key] for key in FeedbackReceipt.model_fields}
                )
            recorded_at = datetime.now(timezone.utc)
            event_without_hash = {
                **request,
                "recorded_at": recorded_at.isoformat(),
                "event_path": str(target),
            }
            event_hash = hashlib.sha256(
                json.dumps(
                    event_without_hash,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            record = {**event_without_hash, "event_hash": event_hash}
            _write_json_create_new(target, record)
            return FeedbackReceipt.model_validate(
                {key: record[key] for key in FeedbackReceipt.model_fields}
            )

    def create_gate_challenge(self, challenge: GateChallenge) -> GateChallenge:
        target = (
            self.gate_challenges_root
            / f"challenge-{_validate_id(challenge.challenge_id, 'challenge_id')}.json"
        )
        _write_json_create_new(target, challenge.model_dump(mode="json"))
        return challenge

    def load_gate_challenge(self, challenge_id: str) -> GateChallenge:
        safe_id = _validate_id(challenge_id, "challenge_id")
        target = self.gate_challenges_root / f"challenge-{safe_id}.json"
        if not target.exists():
            raise RunStoreCorruption("gate challenge does not exist")
        try:
            return GateChallenge.model_validate_json(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValidationError, ValueError) as exc:
            raise RunStoreCorruption(f"invalid gate challenge: {target}") from exc

    def record_gate_receipt(self, receipt: GateReceipt) -> GateReceipt:
        challenge = self.load_gate_challenge(receipt.challenge_id)
        if challenge.gate is not receipt.gate:
            raise RunStoreCorruption("gate receipt kind differs from challenge")
        replay_marker = self.gate_receipts_root / f"challenge-{receipt.challenge_id}.recorded.json"
        target = (
            self.gate_receipts_root
            / f"receipt-{_validate_id(receipt.receipt_id, 'receipt_id')}.json"
        )
        with self.coordination_lock():
            if replay_marker.exists():
                raise PermissionError("gate challenge was already recorded")
            _write_json_create_new(target, receipt.model_dump(mode="json"))
            _write_json_create_new(
                replay_marker,
                {
                    "schema_version": 1,
                    "challenge_id": receipt.challenge_id,
                    "receipt_id": receipt.receipt_id,
                    "recorded_at": receipt.issued_at.isoformat(),
                },
            )
        return receipt

    def load_gate_receipt(self, receipt_id: str) -> GateReceipt:
        safe_id = _validate_id(receipt_id, "receipt_id")
        target = self.gate_receipts_root / f"receipt-{safe_id}.json"
        if not target.exists():
            raise RunStoreCorruption("gate receipt does not exist")
        try:
            return GateReceipt.model_validate_json(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValidationError, ValueError) as exc:
            raise RunStoreCorruption(f"invalid gate receipt: {target}") from exc

    def consume_gate_receipt(
        self,
        *,
        receipt: GateReceipt,
        challenge: GateChallenge,
        requested_mode: ExecutionMode,
        authorization_lineage_id: str,
        mode_epoch: str,
    ) -> dict[str, object]:
        safe_receipt_id = _validate_id(receipt.receipt_id, "receipt_id")
        consumption_id = f"consumption-{uuid.uuid4()}"
        target = self.gate_consumptions_root / f"receipt-{safe_receipt_id}.consumed.json"
        record = {
            "schema_version": 1,
            "consumption_id": consumption_id,
            "receipt_id": safe_receipt_id,
            "challenge_id": challenge.challenge_id,
            "gate": receipt.gate.value,
            "requested_mode": requested_mode.value,
            "authorization_lineage_id": authorization_lineage_id,
            "mode_epoch": mode_epoch,
            "release_hash": challenge.release_hash,
            "spec_hash": challenge.spec_hash,
            "config_hash": challenge.config_hash,
            "source_baseline_hash": challenge.source_baseline_hash,
            "consumed_at": datetime.now(timezone.utc).isoformat(),
        }
        mode_target = self.mode_events_root / f"mode-{consumption_id}.json"
        with self.coordination_lock():
            if target.exists():
                raise PermissionError("gate receipt was already consumed")
            _write_json_create_new(target, record)
            _write_json_create_new(
                mode_target,
                {
                    "schema_version": 1,
                    "mode": requested_mode.value,
                    "mode_epoch": mode_epoch,
                    "receipt_id": safe_receipt_id,
                    "consumption_id": consumption_id,
                    "created_at": record["consumed_at"],
                },
            )
        return record

    def transition_mode_to_shadow(self, reason: str) -> dict[str, object]:
        if not reason.strip():
            raise ValueError("mode transition reason cannot be blank")
        latest = self.latest_mode_event()
        event_id = f"shadow-{uuid.uuid4()}"
        record = {
            "schema_version": 1,
            "mode": ExecutionMode.SHADOW.value,
            "mode_epoch": f"mode-{uuid.uuid4()}",
            "supersedes_mode_epoch": (latest.get("mode_epoch") if latest is not None else None),
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_create_new(
            self.mode_events_root / f"mode-{event_id}.json",
            record,
        )
        return record

    def latest_mode_event(self) -> dict[str, object] | None:
        events: list[dict[str, object]] = []
        for path in self.mode_events_root.glob("mode-*.json"):
            try:
                event = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RunStoreCorruption(f"invalid mode event: {path}") from exc
            events.append(event)
        if not events:
            return None
        return max(events, key=lambda event: str(event["created_at"]))

    def gate_authorization_is_current(
        self,
        authorization: AuthorizationContext,
    ) -> bool:
        if authorization.effective_mode is ExecutionMode.SHADOW:
            return True
        if not authorization.gate_receipt_id or not authorization.gate_consumption_id:
            return False
        target = (
            self.gate_consumptions_root / f"receipt-{authorization.gate_receipt_id}.consumed.json"
        )
        if not target.exists():
            return not any(self.gate_consumptions_root.glob("*.json"))
        try:
            record = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunStoreCorruption(f"invalid gate consumption: {target}") from exc
        if (
            record.get("consumption_id") != authorization.gate_consumption_id
            or record.get("authorization_lineage_id") != authorization.authorization_lineage_id
            or record.get("mode_epoch") != authorization.mode_epoch
            or record.get("release_hash") != authorization.release_manifest_hash
            or record.get("spec_hash") != authorization.spec_hash
            or record.get("config_hash") != authorization.config_hash
            or record.get("source_baseline_hash") != authorization.source_baseline_hash
        ):
            return False
        latest = self.latest_mode_event()
        return bool(
            latest
            and latest.get("mode") == authorization.effective_mode.value
            and latest.get("mode_epoch") == authorization.mode_epoch
        )

    def write_minimal_blocked_event(
        self,
        *,
        run_id: str,
        fencing_token: int,
        authorization: dict[str, object],
        capacity_state: str,
        risk_count: int,
        max_bytes: int,
    ) -> Path:
        """Write one bounded event when normal artifact creation is blocked."""

        safe_run_id = _validate_id(run_id, "run_id")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        target = self.events_root / f"blocked-{fencing_token:020d}-{safe_run_id}.json"
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": safe_run_id,
                    "fencing_token": fencing_token,
                    "run_status": RunStatus.BLOCKED.value,
                    "reason": "capacity-or-safety",
                    "capacity_state": capacity_state,
                    "risk_count": risk_count,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > max_bytes:
            raise ValueError("minimal BLOCKED event exceeds capacity policy")
        with self.coordination_lock():
            self.assert_current_run_authorization(
                run_id=safe_run_id,
                fencing_token=fencing_token,
                authorization=authorization,
            )
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        return target

    def replay_run(self, run_id: str) -> DerivedRunState:
        safe_run_id = _validate_id(run_id, "run_id")
        run_root = self.runs_root / safe_run_id
        if not run_root.exists():
            raise RunStoreCorruption(f"run history does not exist: {safe_run_id}")
        paths = sorted(run_root.glob("*.json"))
        events: list[RunEvent] = []
        for expected_sequence, path in enumerate(paths, start=1):
            match = EVENT_FILE_PATTERN.fullmatch(path.name)
            if match is None or int(match.group(1)) != expected_sequence:
                raise RunStoreCorruption(f"non-contiguous event history: {path}")
            try:
                event = RunEvent.model_validate_json(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValidationError, ValueError) as exc:
                raise RunStoreCorruption(f"invalid event: {path}") from exc
            if event.run_id != safe_run_id or event.sequence != expected_sequence:
                raise RunStoreCorruption(f"event identity mismatch: {path}")
            events.append(event)

        status = RunStatus.PENDING
        stages: dict[str, StageStatus] = {}
        for event in events:
            if event.stage is not None and event.stage_status is not None:
                stages[event.stage] = event.stage_status
            raw_status = event.payload.get("run_status")
            if raw_status is not None:
                try:
                    target = RunStatus(str(raw_status))
                    assert_run_transition(status, target)
                except ValueError as exc:
                    raise RunStoreCorruption(
                        f"invalid run status transition in event {event.sequence}"
                    ) from exc
                status = target
        return DerivedRunState(
            run_id=safe_run_id,
            status=status,
            stages=stages,
            events=tuple(events),
        )
