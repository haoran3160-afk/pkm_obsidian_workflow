"""Minimal filesystem capability broker for the PKM workflow."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from path_policy import (
    FileIdentity,
    PathPolicyError,
    VaultPathPolicy,
    get_file_identity,
    get_open_file_identity,
)
from run_store import RunStore, RunStoreCorruption
from workflow_contracts import (
    AuthorizationContext,
    ContractModel,
    ExecutionMode,
    PublicationState,
)

STATUS_PREFIX = b"publication_state: "
STATUS_OFFSET = len(STATUS_PREFIX)
STATUS_LENGTH = 9
SAFE_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BrokerDenied(PermissionError):
    """A request does not match an immutable canonical reservation."""


class BrokerReservation(ContractModel):
    generation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)
    authorization: AuthorizationContext
    relative_path: str = Field(min_length=1)
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ArtifactSnapshot:
    state: PublicationState
    body_hash: str
    full_hash: str
    identity: FileIdentity


def stable_body_hash(body: str) -> str:
    payload = STATUS_PREFIX + b"\n" + body.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact_bytes(body: str, state: PublicationState) -> bytes:
    return STATUS_PREFIX + state.value.encode("ascii") + b"\n" + body.encode("utf-8")


def _snapshot_from_bytes(
    payload: bytes,
    identity: FileIdentity,
) -> ArtifactSnapshot:
    if len(payload) < STATUS_OFFSET + STATUS_LENGTH + 1:
        raise BrokerDenied("artifact is too short to contain a status slot")
    if payload[:STATUS_OFFSET] != STATUS_PREFIX:
        raise BrokerDenied("artifact status prefix is invalid")
    try:
        state = PublicationState(
            payload[STATUS_OFFSET : STATUS_OFFSET + STATUS_LENGTH].decode("ascii")
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise BrokerDenied("artifact status slot is invalid") from exc
    stable_bytes = payload[:STATUS_OFFSET] + payload[STATUS_OFFSET + STATUS_LENGTH :]
    return ArtifactSnapshot(
        state=state,
        body_hash=hashlib.sha256(stable_bytes).hexdigest(),
        full_hash=hashlib.sha256(payload).hexdigest(),
        identity=identity,
    )


def inspect_artifact(path: Path) -> ArtifactSnapshot:
    target = Path(path).resolve(strict=True)
    identity = get_file_identity(target)
    payload = target.read_bytes()
    if len(payload) != identity.size:
        raise BrokerDenied("artifact changed while being inspected")
    return _snapshot_from_bytes(payload, identity)


def create_pending_artifact(path: Path, body: str) -> ArtifactSnapshot:
    target = Path(path)
    payload = _artifact_bytes(body, PublicationState.PENDING)
    with target.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        identity = get_open_file_identity(stream, target)
    snapshot = _snapshot_from_bytes(payload, identity)
    if snapshot.body_hash != stable_body_hash(body):
        raise BrokerDenied("created artifact body hash is inconsistent")
    return snapshot


def _same_identity(left: FileIdentity, right: FileIdentity) -> bool:
    return (
        left.canonical_path == right.canonical_path
        and left.device_id == right.device_id
        and left.file_id == right.file_id
        and left.size == right.size
        and left.is_directory == right.is_directory
    )


def finalize_artifact(
    path: Path,
    expected: ArtifactSnapshot,
    target_state: PublicationState,
) -> ArtifactSnapshot:
    if target_state not in {PublicationState.COMMITTED, PublicationState.PARTIAL}:
        raise BrokerDenied("terminal state must be COMMITTED or PARTIAL__")
    target = Path(path).resolve(strict=True)
    with target.open("r+b", buffering=0) as stream:
        before_identity = get_open_file_identity(stream, target)
        stream.seek(0)
        before_payload = stream.read()
        before = _snapshot_from_bytes(before_payload, before_identity)
        if before.state is not PublicationState.PENDING:
            raise BrokerDenied("status transition requires the current value PENDING__")
        if (
            not _same_identity(before.identity, expected.identity)
            or before.body_hash != expected.body_hash
            or before.full_hash != expected.full_hash
        ):
            raise BrokerDenied("artifact identity or hash changed before finalization")
        stream.seek(STATUS_OFFSET)
        written = stream.write(target_state.value.encode("ascii"))
        if written != STATUS_LENGTH:
            raise BrokerDenied("status slot write was incomplete")
        stream.flush()
        os.fsync(stream.fileno())
        after_identity = get_open_file_identity(stream, target)
        stream.seek(0)
        after_payload = stream.read()
    after = _snapshot_from_bytes(after_payload, after_identity)
    if not _same_identity(before.identity, after.identity):
        raise BrokerDenied("artifact identity changed during finalization")
    if after.body_hash != before.body_hash or after.state is not target_state:
        raise BrokerDenied("artifact did not preserve body and terminal state")
    return after


def _write_json_create_new(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class CreateOnlyBroker:
    """The four-operation filesystem capability exposed to workflow code."""

    def __init__(self, store: RunStore, vault_policy: VaultPathPolicy) -> None:
        self._store = store
        self._vault_policy = vault_policy

    def create_new_file(
        self,
        reservation: BrokerReservation,
        body: str,
    ) -> ArtifactSnapshot:
        with self._store.coordination_lock():
            try:
                target = self._authorize_reservation(reservation)
                if stable_body_hash(body) != reservation.body_hash:
                    raise BrokerDenied("body does not match reservation")
                validated = self._vault_policy.validate_create(reservation.relative_path)
                if validated != target:
                    raise BrokerDenied("resolved target differs from reservation")
                return create_pending_artifact(validated, body)
            except PathPolicyError as exc:
                raise BrokerDenied(str(exc)) from exc

    def create_new_event(self, event_id: str, payload: dict[str, object]) -> Path:
        if SAFE_EVENT_ID.fullmatch(event_id) is None:
            raise BrokerDenied("event_id contains unsafe characters")
        if not payload or "schema_version" not in payload:
            raise BrokerDenied("event payload must be self-describing")
        target = self._store.events_root / f"{event_id}.json"
        with self._store.coordination_lock():
            _write_json_create_new(target, payload)
        return target

    def create_lock_anchor(self) -> FileIdentity:
        identity = get_file_identity(self._store.lock_path)
        if self._store.lock_path.read_bytes() != b"\x00" or identity.size != 1:
            raise RunStoreCorruption("control.lock is not a valid lock anchor")
        return identity

    def finalize_fixed_status_slot(
        self,
        reservation: BrokerReservation,
        *,
        expected_file_id: int,
        expected_body_hash: str,
        target_state: PublicationState,
    ) -> ArtifactSnapshot:
        with self._store.coordination_lock():
            target = self._authorize_reservation(reservation)
            current = inspect_artifact(target)
            if current.identity.file_id != expected_file_id:
                raise BrokerDenied("file identity does not match reservation")
            if (
                current.body_hash != expected_body_hash
                or expected_body_hash != reservation.body_hash
            ):
                raise BrokerDenied("body hash does not match reservation")
            return finalize_artifact(target, current, target_state)

    def _authorize_reservation(self, capability: BrokerReservation) -> Path:
        if capability.authorization.effective_mode is ExecutionMode.SHADOW:
            raise BrokerDenied("shadow authorization has no Vault write capability")
        self._store.assert_current_run_authorization(
            run_id=capability.run_id,
            fencing_token=capability.fencing_token,
            authorization=capability.authorization.model_dump(mode="json"),
        )
        if SAFE_EVENT_ID.fullmatch(capability.generation_id) is None:
            raise BrokerDenied("generation_id contains unsafe characters")
        reservation_path = (
            self._store.reservations_root / f"{capability.generation_id}.writing.json"
        )
        try:
            record = json.loads(reservation_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrokerDenied("canonical reservation is missing or invalid") from exc
        if (
            record.get("run_id") != capability.run_id
            or int(record.get("fencing_token", -1)) != capability.fencing_token
            or record.get("authorization") != capability.authorization.model_dump(mode="json")
        ):
            raise BrokerDenied("capability differs from canonical reservation")
        try:
            target = self._vault_policy.resolve_target(capability.relative_path)
        except PathPolicyError as exc:
            raise BrokerDenied(str(exc)) from exc
        expected = {
            "relative_path": capability.relative_path,
            "absolute_path": str(target),
            "body_hash": capability.body_hash,
        }
        if expected not in record.get("targets", []):
            raise BrokerDenied("logical target is not in canonical reservation")
        return target


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_ulong),
        ("dwY", ctypes.c_ulong),
        ("dwXSize", ctypes.c_ulong),
        ("dwYSize", ctypes.c_ulong),
        ("dwXCountChars", ctypes.c_ulong),
        ("dwYCountChars", ctypes.c_ulong),
        ("dwFillAttribute", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("wShowWindow", ctypes.c_ushort),
        ("cbReserved2", ctypes.c_ushort),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_ulong),
        ("dwThreadId", ctypes.c_ulong),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_ulong)]


class _TokenMandatoryLabel(ctypes.Structure):
    _fields_ = [("Label", _SidAndAttributes)]


def _last_windows_error(operation: str) -> OSError:
    return OSError(ctypes.windll.kernel32.GetLastError(), operation)


def _create_low_integrity_token() -> int:
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    advapi32.CreateRestrictedToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.CreateRestrictedToken.restype = ctypes.c_int
    advapi32.ConvertStringSidToSidW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = ctypes.c_int
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = ctypes.c_ulong
    advapi32.SetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    advapi32.SetTokenInformation.restype = ctypes.c_int
    process_token = ctypes.c_void_p()
    restricted_token = ctypes.c_void_p()
    desired_access = 0x0001 | 0x0002 | 0x0008 | 0x0080 | 0x0100
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        desired_access,
        ctypes.byref(process_token),
    ):
        raise _last_windows_error("OpenProcessToken")
    try:
        if not advapi32.CreateRestrictedToken(
            process_token,
            0x00000001,
            0,
            None,
            0,
            None,
            0,
            None,
            ctypes.byref(restricted_token),
        ):
            raise _last_windows_error("CreateRestrictedToken")
    finally:
        kernel32.CloseHandle(process_token)

    sid = ctypes.c_void_p()
    try:
        if not advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):
            raise _last_windows_error("ConvertStringSidToSidW")
        label = _TokenMandatoryLabel(_SidAndAttributes(sid, 0x00000020))
        size = ctypes.sizeof(label) + advapi32.GetLengthSid(sid)
        if not advapi32.SetTokenInformation(
            restricted_token,
            25,
            ctypes.byref(label),
            size,
        ):
            raise _last_windows_error("SetTokenInformation")
        token_value = restricted_token.value
        if token_value is None:
            raise BrokerDenied("CreateRestrictedToken returned a null handle")
        return int(token_value)
    except Exception:
        kernel32.CloseHandle(restricted_token)
        raise
    finally:
        kernel32.LocalFree(sid)


def run_low_integrity_probe(
    *,
    executable: Path,
    probe_script: Path,
    targets: Sequence[Path],
) -> int:
    """Launch a restricted, low-integrity child and return its exit code."""

    if os.name != "nt":
        raise RuntimeError("low-integrity probe requires Windows")
    executable_path = Path(executable).resolve(strict=True)
    script_path = Path(probe_script).resolve(strict=True)
    target_paths = [Path(target).resolve(strict=True) for target in targets]
    command_line = subprocess.list2cmdline(
        [str(executable_path), str(script_path), *map(str, target_paths)]
    )
    command_buffer = ctypes.create_unicode_buffer(command_line)
    startup = _StartupInfoW()
    startup.cb = ctypes.sizeof(startup)
    process = _ProcessInformation()
    token = _create_low_integrity_token()
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    advapi32.CreateProcessAsUserW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(_StartupInfoW),
        ctypes.POINTER(_ProcessInformation),
    ]
    advapi32.CreateProcessAsUserW.restype = ctypes.c_int
    advapi32.CreateProcessWithTokenW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(_StartupInfoW),
        ctypes.POINTER(_ProcessInformation),
    ]
    advapi32.CreateProcessWithTokenW.restype = ctypes.c_int
    try:
        created = advapi32.CreateProcessAsUserW(
            ctypes.c_void_p(token),
            str(executable_path),
            command_buffer,
            None,
            None,
            False,
            0x08000000,
            None,
            str(script_path.parent),
            ctypes.byref(startup),
            ctypes.byref(process),
        )
        if not created and kernel32.GetLastError() == 1314:
            command_buffer = ctypes.create_unicode_buffer(command_line)
            created = advapi32.CreateProcessWithTokenW(
                ctypes.c_void_p(token),
                0,
                str(executable_path),
                command_buffer,
                0x08000000,
                None,
                str(script_path.parent),
                ctypes.byref(startup),
                ctypes.byref(process),
            )
        if not created:
            raise _last_windows_error("restricted process creation")
        kernel32.WaitForSingleObject(process.hProcess, 60000)
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(
            process.hProcess,
            ctypes.byref(exit_code),
        ):
            raise _last_windows_error("GetExitCodeProcess")
        return int(exit_code.value)
    finally:
        if process.hThread:
            kernel32.CloseHandle(process.hThread)
        if process.hProcess:
            kernel32.CloseHandle(process.hProcess)
        kernel32.CloseHandle(ctypes.c_void_p(token))
