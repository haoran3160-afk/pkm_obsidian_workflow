"""No-delete commit protocol for workflow-owned Vault artifacts."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from filesystem_broker import (
    ArtifactSnapshot,
    BrokerDenied,
    BrokerReservation,
    CreateOnlyBroker,
)
from path_policy import (
    FileIdentity,
    VaultPathPolicy,
    get_file_identity,
    get_open_file_identity,
)
from run_service import RunService
from run_store import FencingError, ReservationConflict
from workflow_contracts import (
    CommitFileResult,
    CommitResult,
    CommitStatus,
    ExecutionMode,
    NoteDraft,
    PublicationState,
    RunStatus,
)

STATUS_PREFIX = b"publication_state: "
STATUS_OFFSET = len(STATUS_PREFIX)
STATUS_LENGTH = 9


class CommitConflict(RuntimeError):
    """The requested commit cannot safely reuse or mutate a path."""


@dataclass(frozen=True)
class ArtifactInput:
    draft: NoteDraft
    body: str


def _artifact_bytes(body: str, state: PublicationState) -> bytes:
    body_bytes = body.encode("utf-8")
    return STATUS_PREFIX + state.value.encode("ascii") + b"\n" + body_bytes


def stable_body_hash(body: str) -> str:
    """Hash every persisted byte except the mutable nine-byte state slot."""

    payload = STATUS_PREFIX + b"\n" + body.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshot_from_bytes(
    path: Path,
    payload: bytes,
    identity: FileIdentity,
) -> ArtifactSnapshot:
    if len(payload) < STATUS_OFFSET + STATUS_LENGTH + 1:
        raise CommitConflict("artifact is too short to contain a status slot")
    if payload[:STATUS_OFFSET] != STATUS_PREFIX:
        raise CommitConflict("artifact status prefix is invalid")
    try:
        state = PublicationState(
            payload[STATUS_OFFSET : STATUS_OFFSET + STATUS_LENGTH].decode("ascii")
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise CommitConflict("artifact status slot is invalid") from exc
    body_bytes = payload[:STATUS_OFFSET] + payload[STATUS_OFFSET + STATUS_LENGTH :]
    return ArtifactSnapshot(
        state=state,
        body_hash=hashlib.sha256(body_bytes).hexdigest(),
        full_hash=hashlib.sha256(payload).hexdigest(),
        identity=identity,
    )


def _inspect_artifact(path: Path) -> ArtifactSnapshot:
    target = Path(path).resolve(strict=True)
    identity = get_file_identity(target)
    payload = target.read_bytes()
    if len(payload) != identity.size:
        raise CommitConflict("artifact changed while being inspected")
    return _snapshot_from_bytes(target, payload, identity)


def _create_pending_artifact(path: Path, body: str) -> ArtifactSnapshot:
    target = Path(path)
    payload = _artifact_bytes(body, PublicationState.PENDING)
    with target.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        identity = get_open_file_identity(stream, target)
    snapshot = _snapshot_from_bytes(target, payload, identity)
    if snapshot.body_hash != stable_body_hash(body):
        raise CommitConflict("created artifact body hash is inconsistent")
    return snapshot


def _same_identity(left: FileIdentity, right: FileIdentity) -> bool:
    return (
        left.canonical_path == right.canonical_path
        and left.device_id == right.device_id
        and left.file_id == right.file_id
        and left.size == right.size
        and left.is_directory == right.is_directory
    )


def _finalize_artifact(
    path: Path,
    expected: ArtifactSnapshot,
    target_state: PublicationState,
) -> ArtifactSnapshot:
    if target_state not in {PublicationState.COMMITTED, PublicationState.PARTIAL}:
        raise CommitConflict("terminal state must be COMMITTED or PARTIAL__")
    target = Path(path).resolve(strict=True)
    with target.open("r+b", buffering=0) as stream:
        before_identity = get_open_file_identity(stream, target)
        stream.seek(0)
        before_payload = stream.read()
        before = _snapshot_from_bytes(target, before_payload, before_identity)
        if before.state is not PublicationState.PENDING:
            raise CommitConflict("status transition requires the current value PENDING__")
        if (
            not _same_identity(before.identity, expected.identity)
            or before.body_hash != expected.body_hash
            or before.full_hash != expected.full_hash
        ):
            raise CommitConflict("artifact identity or hash changed before finalization")
        stream.seek(STATUS_OFFSET)
        written = stream.write(target_state.value.encode("ascii"))
        if written != STATUS_LENGTH:
            raise CommitConflict("status slot write was incomplete")
        stream.flush()
        os.fsync(stream.fileno())
        after_identity = get_open_file_identity(stream, target)
        stream.seek(0)
        after_payload = stream.read()
    after = _snapshot_from_bytes(target, after_payload, after_identity)
    if not _same_identity(before.identity, after.identity):
        raise CommitConflict("artifact identity changed during finalization")
    if after.body_hash != before.body_hash or after.state is not target_state:
        raise CommitConflict("artifact did not preserve its body and terminal state")
    return after


class CommitService:
    """Create, reconcile, and terminally mark a generation without deletion."""

    def __init__(
        self,
        runs: RunService,
        vault_policy: VaultPathPolicy,
        broker: CreateOnlyBroker | None = None,
    ) -> None:
        self.runs = runs
        self.store = runs.store
        self.vault_policy = vault_policy
        self.broker = broker or CreateOnlyBroker(self.store, vault_policy)

    def commit(
        self,
        *,
        run_id: str,
        generation_id: str,
        artifacts: Sequence[ArtifactInput],
    ) -> CommitResult:
        existing_result = self.store.get_generation_result(generation_id)
        if existing_result is not None:
            if existing_result.run_id != run_id:
                raise CommitConflict("generation is bound to another run")
            return existing_result

        view = self.runs.get_run(run_id)
        if view.status is not RunStatus.PENDING:
            raise CommitConflict("only a pending run can commit a new generation")
        authorization = view.ref.authorization
        if authorization.effective_mode is ExecutionMode.SHADOW:
            raise PermissionError("shadow runs cannot create formal Vault artifacts")
        if not artifacts:
            raise ValueError("a generation must contain at least one artifact")

        resolved: list[tuple[ArtifactInput, Path]] = []
        seen_paths: set[str] = set()
        targets: list[dict[str, object]] = []
        for artifact in artifacts:
            if artifact.draft.run_id != run_id:
                raise CommitConflict("draft run_id does not match the canonical run")
            if artifact.draft.body_hash != stable_body_hash(artifact.body):
                raise CommitConflict("draft body_hash does not match persisted content")
            target = self.vault_policy.resolve_target(artifact.draft.target_path)
            key = os.path.normcase(str(target))
            if key in seen_paths:
                raise CommitConflict("generation contains a duplicate target path")
            seen_paths.add(key)
            resolved.append((artifact, target))
            targets.append(
                {
                    "relative_path": artifact.draft.target_path,
                    "absolute_path": str(target),
                    "body_hash": artifact.draft.body_hash,
                }
            )

        authorization_payload = authorization.model_dump(mode="json")
        try:
            _, reservation_created = self.store.get_or_create_reservation(
                generation_id=generation_id,
                run_id=run_id,
                fencing_token=view.ref.fencing_token,
                authorization=authorization_payload,
                targets=targets,
            )
        except ReservationConflict as exc:
            message = str(exc).replace(
                "target existed before reservation",
                "target existed before reservation",
            )
            raise CommitConflict(message) from exc

        capabilities = {
            artifact.draft.target_path: BrokerReservation(
                generation_id=generation_id,
                run_id=run_id,
                fencing_token=view.ref.fencing_token,
                authorization=authorization,
                relative_path=artifact.draft.target_path,
                body_hash=artifact.draft.body_hash,
            )
            for artifact, _ in resolved
        }
        file_results: list[CommitFileResult] = []

        def record_partial(error: OSError) -> CommitResult:
            marker = self.store.generation_marker_path(
                generation_id,
                CommitStatus.PARTIAL,
            )
            partial = CommitResult(
                run_id=run_id,
                generation_id=generation_id,
                status=CommitStatus.PARTIAL,
                files=file_results,
                commit_marker=str(marker),
                checkpoint_status="NOT_ADVANCED",
                errors=[f"{type(error).__name__}: {error}"],
            )
            try:
                return self.store.record_generation_result(
                    result=partial,
                    fencing_token=view.ref.fencing_token,
                    authorization=authorization_payload,
                )
            except (FencingError, ReservationConflict) as marker_error:
                raise CommitConflict(str(marker_error)) from marker_error

        for artifact, target in resolved:
            capability = capabilities[artifact.draft.target_path]
            try:
                if target.exists():
                    if reservation_created:
                        raise CommitConflict(
                            "target appeared after reservation and cannot be claimed"
                        )
                    pending = _inspect_artifact(target)
                else:
                    pending = self.broker.create_new_file(capability, artifact.body)
            except BrokerDenied as exc:
                raise CommitConflict(str(exc)) from exc
            except OSError as exc:
                if not file_results:
                    raise
                return record_partial(exc)
            if pending.body_hash != artifact.draft.body_hash:
                raise CommitConflict("reserved target body hash differs")
            if pending.state is PublicationState.PARTIAL:
                raise CommitConflict("PARTIAL artifact is immutable; create a new revision")

            try:
                current = _inspect_artifact(target)
                if current.body_hash != artifact.draft.body_hash:
                    raise CommitConflict("artifact body changed before finalization")
                if current.state is PublicationState.PENDING:
                    terminal = self.broker.finalize_fixed_status_slot(
                        capability,
                        expected_file_id=current.identity.file_id,
                        expected_body_hash=current.body_hash,
                        target_state=PublicationState.COMMITTED,
                    )
                elif current.state is PublicationState.COMMITTED:
                    terminal = current
                else:
                    raise CommitConflict("PARTIAL artifact is immutable; create a new revision")
            except BrokerDenied as exc:
                raise CommitConflict(str(exc)) from exc
            except OSError as exc:
                if not file_results:
                    raise
                return record_partial(exc)
            file_results.append(
                CommitFileResult(
                    path=artifact.draft.target_path,
                    hash=terminal.full_hash,
                    write_status=terminal.state,
                )
            )

        marker = self.store.generation_marker_path(
            generation_id,
            CommitStatus.COMMITTED,
        )
        result = CommitResult(
            run_id=run_id,
            generation_id=generation_id,
            status=CommitStatus.COMMITTED,
            files=file_results,
            commit_marker=str(marker),
            checkpoint_status="ADVANCED",
        )
        try:
            return self.store.record_generation_result(
                result=result,
                fencing_token=view.ref.fencing_token,
                authorization=authorization_payload,
            )
        except (FencingError, ReservationConflict) as exc:
            raise CommitConflict(str(exc)) from exc
