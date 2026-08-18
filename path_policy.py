"""Path validation shared by CLI, HTTP, bridge, automation, and writers."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from ctypes import Structure, byref, create_unicode_buffer, wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from workflow_contracts import AuthorizationContext, ExecutionMode

SHORT_NAME_PATTERN = re.compile(r"~\d+(?:\.|$)", re.IGNORECASE)


class PathPolicyError(ValueError):
    """A supplied path is unsafe or outside the declared purpose."""


@dataclass(frozen=True)
class FileIdentity:
    canonical_path: Path
    device_id: int
    file_id: int
    size: int
    is_directory: bool


class VaultIdentityStatus(str, Enum):
    MISSING = "MISSING"
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class VaultIdentityResult:
    status: VaultIdentityStatus
    vault_id: str | None
    detail: str


class CapacityState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class CapacityDecision:
    state: CapacityState
    free_ratio: float
    free_bytes: int
    estimated_run_bytes: int
    estimated_exhaustion_at: datetime | None
    can_write_content: bool
    can_write_blocked_event: bool
    max_blocked_event_bytes: int
    recommended_actions: tuple[str, ...] = (
        "expand-storage",
        "pause-automation",
    )


@dataclass(frozen=True)
class CapacityPolicy:
    warning_free_ratio: float = 0.15
    blocked_free_ratio: float = 0.05
    blocked_min_free_gib: int = 2
    warning_exhaustion_days: int = 90
    blocked_exhaustion_days: int = 14
    critical_event_floor_bytes: int = 65536
    max_blocked_event_bytes: int = 8192

    def decide(
        self,
        *,
        total_bytes: int,
        free_bytes: int,
        estimated_run_bytes: int,
        daily_growth_bytes: int,
        now: datetime,
    ) -> CapacityDecision:
        if total_bytes <= 0:
            raise ValueError("total_bytes must be positive")
        if min(free_bytes, estimated_run_bytes, daily_growth_bytes) < 0:
            raise ValueError("capacity inputs cannot be negative")
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        free_ratio = free_bytes / total_bytes
        exhaustion_days = free_bytes / daily_growth_bytes if daily_growth_bytes > 0 else None
        exhaustion_at = (
            now + timedelta(days=exhaustion_days) if exhaustion_days is not None else None
        )
        blocked_floor = max(
            self.blocked_min_free_gib * 1024**3,
            estimated_run_bytes * 3,
        )

        if free_bytes <= self.critical_event_floor_bytes:
            state = CapacityState.CRITICAL
        elif (
            free_ratio < self.blocked_free_ratio
            or free_bytes < blocked_floor
            or (exhaustion_days is not None and exhaustion_days <= self.blocked_exhaustion_days)
        ):
            state = CapacityState.BLOCKED
        elif free_ratio < self.warning_free_ratio or (
            exhaustion_days is not None and exhaustion_days <= self.warning_exhaustion_days
        ):
            state = CapacityState.WARNING
        else:
            state = CapacityState.NORMAL

        return CapacityDecision(
            state=state,
            free_ratio=free_ratio,
            free_bytes=free_bytes,
            estimated_run_bytes=estimated_run_bytes,
            estimated_exhaustion_at=exhaustion_at,
            can_write_content=state
            in {
                CapacityState.NORMAL,
                CapacityState.WARNING,
            },
            can_write_blocked_event=state is CapacityState.BLOCKED,
            max_blocked_event_bytes=(
                self.max_blocked_event_bytes if state is CapacityState.BLOCKED else 0
            ),
        )


def _reject_unsafe_text(value: str) -> None:
    if not value or "\x00" in value:
        raise PathPolicyError("path is empty or contains NUL")
    normalized = value.replace("/", "\\")
    if normalized.startswith(("\\\\", "\\?\\", "\\.\\")):
        raise PathPolicyError("UNC and device paths are not allowed")
    if any(part == ".." for part in PureWindowsPath(normalized).parts):
        raise PathPolicyError("parent traversal is not allowed")
    _, tail = os.path.splitdrive(normalized)
    if ":" in tail:
        raise PathPolicyError("alternate data streams are not allowed")
    if SHORT_NAME_PATTERN.search(normalized):
        raise PathPolicyError("8.3 short-name aliases are not allowed")


def _is_within(candidate: Path, root: Path) -> bool:
    candidate_text = os.path.normcase(str(candidate))
    root_text = os.path.normcase(str(root))
    try:
        return os.path.commonpath([candidate_text, root_text]) == root_text
    except ValueError:
        return False


def _assert_no_reparse_ancestry(path: Path, stop: Path) -> None:
    current = path
    while True:
        stat = current.stat(follow_symlinks=False)
        attributes = getattr(stat, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if current.is_symlink() or attributes & reparse_flag:
            raise PathPolicyError(f"reparse ancestry is not allowed: {current}")
        if current == stop:
            return
        if current.parent == current:
            raise PathPolicyError(f"path escaped declared root: {path}")
        current = current.parent


def _identity(path: Path) -> FileIdentity:
    if os.name == "nt":
        return _windows_handle_identity(path)
    stat = path.stat(follow_symlinks=False)
    return FileIdentity(
        canonical_path=path,
        device_id=int(stat.st_dev),
        file_id=int(stat.st_ino),
        size=0 if path.is_dir() else int(stat.st_size),
        is_directory=path.is_dir(),
    )


def get_file_identity(path: Path) -> FileIdentity:
    """Return a handle-backed identity for an existing local path."""

    return _identity(Path(path).resolve(strict=True))


class _ByHandleFileInformation(Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time_low", wintypes.DWORD),
        ("creation_time_high", wintypes.DWORD),
        ("last_access_time_low", wintypes.DWORD),
        ("last_access_time_high", wintypes.DWORD),
        ("last_write_time_low", wintypes.DWORD),
        ("last_write_time_high", wintypes.DWORD),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


def _windows_handle_identity(path: Path) -> FileIdentity:
    kernel32 = ctypes.windll.kernel32
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    backup_semantics = 0x02000000
    handle = create_file(
        str(path),
        0,
        share_read_write,
        None,
        open_existing,
        backup_semantics,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        raise PathPolicyError(f"unable to open a stable Windows handle: {path}")

    try:
        return _windows_identity_from_handle(handle, path)
    finally:
        kernel32.CloseHandle(handle)


def _windows_identity_from_handle(handle: int, expected_path: Path) -> FileIdentity:
    kernel32 = ctypes.windll.kernel32
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    get_information.restype = wintypes.BOOL
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD

    information = _ByHandleFileInformation()
    if not get_information(handle, byref(information)):
        raise PathPolicyError(f"unable to read Windows file identity: {expected_path}")
    buffer = create_unicode_buffer(32768)
    length = get_final_path(handle, buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        raise PathPolicyError(f"unable to resolve final Windows handle path: {expected_path}")
    final_text = buffer.value
    if final_text.startswith("\\\\?\\UNC\\"):
        final_text = "\\\\" + final_text[8:]
    elif final_text.startswith("\\\\?\\"):
        final_text = final_text[4:]
    final_path = Path(final_text)
    expected = Path(expected_path).resolve(strict=True)
    if os.path.normcase(str(final_path)) != os.path.normcase(str(expected)):
        raise PathPolicyError(f"final handle path mismatch: expected {expected}, got {final_path}")
    file_id = (int(information.file_index_high) << 32) | int(information.file_index_low)
    size = (int(information.file_size_high) << 32) | int(information.file_size_low)
    is_directory = expected.is_dir()
    return FileIdentity(
        canonical_path=expected,
        device_id=int(information.volume_serial_number),
        file_id=file_id,
        size=0 if is_directory else size,
        is_directory=is_directory,
    )


def get_open_file_identity(stream: BinaryIO, expected_path: Path) -> FileIdentity:
    """Resolve identity from the exact open handle used for a transition."""

    if os.name == "nt":
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        return _windows_identity_from_handle(handle, expected_path)
    stat = os.fstat(stream.fileno())
    expected = Path(expected_path).resolve(strict=True)
    return FileIdentity(
        canonical_path=expected,
        device_id=int(stat.st_dev),
        file_id=int(stat.st_ino),
        size=int(stat.st_size),
        is_directory=False,
    )


class LocalArtifactPathPolicy:
    """Map each path purpose to explicit read or CREATE_NEW roots."""

    def __init__(
        self,
        *,
        read_roots: Mapping[str, Sequence[Path]] | None = None,
        create_roots: Mapping[str, Sequence[Path]] | None = None,
    ) -> None:
        self._read_roots = self._normalize_roots(read_roots or {})
        self._create_roots = self._normalize_roots(create_roots or {})

    @staticmethod
    def _normalize_roots(
        roots: Mapping[str, Sequence[Path]],
    ) -> dict[str, tuple[Path, ...]]:
        normalized: dict[str, tuple[Path, ...]] = {}
        for purpose, values in roots.items():
            if not purpose.strip():
                raise PathPolicyError("path purpose cannot be blank")
            normalized[purpose] = tuple(Path(value).resolve(strict=True) for value in values)
        return normalized

    def validate_read(self, purpose: str, candidate: Path) -> FileIdentity:
        roots = self._read_roots.get(purpose)
        if not roots:
            raise PathPolicyError(f"read purpose is not configured: {purpose}")
        _reject_unsafe_text(str(candidate))
        resolved = Path(candidate).resolve(strict=True)
        root = next(
            (
                allowed
                for allowed in roots
                if resolved == allowed or (allowed.is_dir() and _is_within(resolved, allowed))
            ),
            None,
        )
        if root is None:
            raise PathPolicyError(f"read path is outside purpose roots: {purpose}")
        ancestry_stop = root if root.is_dir() else root.parent
        _assert_no_reparse_ancestry(resolved, ancestry_stop)
        return _identity(resolved)

    def validate_create(self, purpose: str, candidate: Path) -> Path:
        roots = self._create_roots.get(purpose)
        if not roots:
            raise PathPolicyError(f"create purpose is not configured: {purpose}")
        _reject_unsafe_text(str(candidate))
        candidate_path = Path(candidate)
        parent = candidate_path.parent.resolve(strict=True)
        root = next(
            (
                allowed
                for allowed in roots
                if allowed.is_dir() and (parent == allowed or _is_within(parent, allowed))
            ),
            None,
        )
        if root is None:
            raise PathPolicyError(f"create path is outside purpose roots: {purpose}")
        _assert_no_reparse_ancestry(parent, root)
        self._assert_no_case_collision(parent, candidate_path.name)
        if candidate_path.exists():
            raise PathPolicyError(f"create target already exists: {candidate_path}")
        return parent / candidate_path.name

    @staticmethod
    def _assert_no_case_collision(parent: Path, name: str) -> None:
        for existing in parent.iterdir():
            if existing.name.casefold() == name.casefold():
                if existing.name == name:
                    raise PathPolicyError(f"create target already exists: {existing}")
                raise PathPolicyError(
                    f"case-insensitive path collision: {name} conflicts with {existing.name}"
                )


class VaultPathPolicy:
    """Validate CREATE_NEW targets against the workflow-owned Vault allowlist."""

    def __init__(self, vault_root: Path) -> None:
        self._vault_root = Path(vault_root).resolve(strict=True)
        if not self._vault_root.is_dir():
            raise PathPolicyError("Vault root must be a directory")
        _assert_no_reparse_ancestry(self._vault_root, self._vault_root)
        self._local = LocalArtifactPathPolicy(
            create_roots={"vault-workflow-artifact": [self._vault_root]}
        )

    @property
    def vault_root(self) -> Path:
        return self._vault_root

    def validate_create(self, relative_path: str) -> Path:
        candidate = self.resolve_target(relative_path)
        return self._local.validate_create("vault-workflow-artifact", candidate)

    def validate_create_with_parents(self, relative_path: str) -> Path:
        """Validate a create target whose parent already exists.

        This is a staging helper for disposable-Vault and Home qualification.
        Native broker qualification is still required for durable production
        publication.  It never creates a directory or file.
        """

        parts = self._validated_parts(relative_path)
        candidate = self._vault_root.joinpath(*parts)
        try:
            parent = candidate.parent.resolve(strict=True)
        except FileNotFoundError as error:
            raise PathPolicyError("Vault target parent does not exist") from error
        if not _is_within(parent, self._vault_root):
            raise PathPolicyError("Vault target escaped the Workflow root")
        _assert_no_reparse_ancestry(parent, self._vault_root)
        return self._local.validate_create("vault-workflow-artifact", parent / candidate.name)

    def resolve_target(self, relative_path: str) -> Path:
        """Resolve an allowlisted target without changing or creating it."""

        parts = self._validated_parts(relative_path)
        candidate = self._vault_root.joinpath(*parts)
        parent = candidate.parent.resolve(strict=True)
        if not _is_within(parent, self._vault_root):
            raise PathPolicyError("Vault target escaped the Workflow root")
        _assert_no_reparse_ancestry(parent, self._vault_root)
        return parent / candidate.name

    @classmethod
    def _validated_parts(cls, relative_path: str) -> tuple[str, ...]:
        _reject_unsafe_text(relative_path)
        windows_path = PureWindowsPath(relative_path.replace("/", "\\"))
        if windows_path.is_absolute() or windows_path.drive or windows_path.root:
            raise PathPolicyError("Vault target must be relative")
        parts = tuple(part for part in windows_path.parts if part not in ("", "."))
        if not cls._is_allowlisted(parts):
            raise PathPolicyError("Vault target is outside the Workflow allowlist")
        return parts

    def inspect_existing(self, relative_path: str) -> FileIdentity:
        candidate = self.resolve_target(relative_path)
        if not candidate.is_file():
            raise PathPolicyError("Vault artifact does not exist as a file")
        _assert_no_reparse_ancestry(candidate, self._vault_root)
        return get_file_identity(candidate)

    @staticmethod
    def _is_allowlisted(parts: tuple[str, ...]) -> bool:
        joined = "/".join(parts)
        exact = {
            "30-Daily/PKM-Workflow-Home.md",
            "30-Daily/PKM-Workflow-Status/Bases/PKM-Home.base",
            "30-Daily/PKM-Workflow-Status/Bases/PKM-Corrections.base",
            "30-Daily/PKM-Workflow-Status/Control/SLA-AI.md",
            "30-Daily/PKM-Workflow-Status/Control/SLA-IELTS.md",
            "30-Daily/PKM-Workflow-Status/Control/SLA-Weekly.md",
        }
        if joined in exact:
            return True

        leaf_patterns = (
            r"30-Daily/AI-News/AI-Daily-\d{4}-\d{2}-\d{2}\.md",
            r"30-Daily/IELTS-Preview/IELTS-Preview-\d{4}-\d{2}-\d{2}\.md",
            r"30-Daily/Weekly/AI-Weekly-\d{4}-\d{2}-\d{2}\.md",
            r"01-Raw/daily-feeds/\d{4}-\d{2}/\d{4}-\d{2}-\d{2}-Raw-Daily-Feeds\.md",
            r"30-Daily/PKM-Workflow-Status/Status/(?:ai|ielts|weekly)/\d{4}-\d{2}-\d{2}/\d{12}-(?:SCHEDULED|RUNNING|PUBLISHED|NO_SIGNIFICANT_NEWS|CATCH_UP_DELAYED|COVERAGE_INSUFFICIENT|DELAYED_QUOTA|ACTION_REQUIRED_AUTH|STATUS_UNKNOWN|CONFLICT)\.md",
            r"30-Daily/PKM-Workflow-Status/StatusEvents/(?:ai|ielts|weekly)/\d{4}-\d{2}-\d{2}/\d{12}-[0-9a-f]{64}\.md",
            r"30-Daily/PKM-Workflow-Status/Corrections/correction-[0-9a-f]{64}\.md",
            r"30-Daily/PKM-Workflow-Status/LateDiscovery/late-discovery-[0-9a-f]{64}\.md",
            r"30-Daily/PKM-Workflow-Status/Preferences/preference-projection-\d{12}-[0-9a-f]{64}\.md",
        )
        return any(re.fullmatch(pattern, joined) is not None for pattern in leaf_patterns)


VAULT_ID_RELATIVE_PATH = "00-Inbox/PKM-Workflow-Vault-ID.json"


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_create_new_bytes(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def create_vault_identity(
    vault_root: Path,
    registry_target: Path,
    *,
    authorization: AuthorizationContext,
) -> VaultIdentityResult:
    """Create the disk sentinel and append-only registry record for Gate A."""

    if authorization.effective_mode is not ExecutionMode.OBSERVED:
        raise PathPolicyError("Vault identity creation requires observed Gate A authority")

    vault = Path(vault_root).resolve(strict=True)
    policy = VaultPathPolicy(vault)
    sentinel_path = policy.validate_create(VAULT_ID_RELATIVE_PATH)
    registry_parent = Path(registry_target).parent.resolve(strict=True)
    registry_policy = LocalArtifactPathPolicy(
        create_roots={"vault-identity-registry": [registry_parent]}
    )
    registry_path = registry_policy.validate_create(
        "vault-identity-registry",
        Path(registry_target),
    )
    root_identity = _identity(vault)
    vault_id = f"vault-{uuid.uuid4()}"
    sentinel_payload = _canonical_json_bytes(
        {
            "schema_version": 1,
            "vault_id": vault_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    sentinel_hash = hashlib.sha256(sentinel_payload).hexdigest()
    registry_payload = _canonical_json_bytes(
        {
            "schema_version": 1,
            "vault_id": vault_id,
            "sentinel_sha256": sentinel_hash,
            "vault_device_id": root_identity.device_id,
            "vault_file_id": root_identity.file_id,
            "authorization_lineage_id": authorization.authorization_lineage_id,
            "gate_receipt_id": authorization.gate_receipt_id,
        }
    )

    _write_create_new_bytes(sentinel_path, sentinel_payload)
    _write_create_new_bytes(registry_path, registry_payload)
    return inspect_vault_identity(vault, registry_path, write_backend="disk")


def inspect_vault_identity(
    vault_root: Path,
    registry_path: Path,
    *,
    write_backend: str,
    rest_reader: Callable[[str], bytes] | None = None,
) -> VaultIdentityResult:
    """Read and compare sentinel/registry state without creating anything."""

    if write_backend not in {"disk", "both"}:
        raise PathPolicyError("write_backend must be disk or both")
    vault = Path(vault_root).resolve(strict=True)
    sentinel_path = vault / Path(VAULT_ID_RELATIVE_PATH)
    registry = Path(registry_path)
    sentinel_exists = sentinel_path.exists()
    registry_exists = registry.exists()
    if not sentinel_exists and not registry_exists:
        return VaultIdentityResult(
            status=VaultIdentityStatus.MISSING,
            vault_id=None,
            detail="disk sentinel and registry are both missing",
        )
    if not sentinel_exists or not registry_exists:
        return VaultIdentityResult(
            status=VaultIdentityStatus.MISMATCH,
            vault_id=None,
            detail="disk sentinel and registry presence differ",
        )

    try:
        sentinel_bytes = sentinel_path.read_bytes()
        sentinel = json.loads(sentinel_bytes.decode("utf-8-sig"))
        registry_value = json.loads(registry.read_text(encoding="utf-8-sig"))
        root_identity = _identity(vault)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return VaultIdentityResult(
            status=VaultIdentityStatus.MISMATCH,
            vault_id=None,
            detail=f"identity record is unreadable: {exc}",
        )

    vault_id = str(sentinel.get("vault_id", ""))
    expected_hash = hashlib.sha256(sentinel_bytes).hexdigest()
    checks = (
        bool(vault_id),
        vault_id == str(registry_value.get("vault_id", "")),
        expected_hash == str(registry_value.get("sentinel_sha256", "")),
        root_identity.device_id == int(registry_value.get("vault_device_id", -1)),
        root_identity.file_id == int(registry_value.get("vault_file_id", -1)),
    )
    if not all(checks):
        return VaultIdentityResult(
            status=VaultIdentityStatus.MISMATCH,
            vault_id=vault_id or None,
            detail="disk sentinel, registry, or Vault file identity differ",
        )

    if write_backend == "both":
        if rest_reader is None:
            return VaultIdentityResult(
                status=VaultIdentityStatus.MISMATCH,
                vault_id=vault_id,
                detail="REST readback is required in both mode",
            )
        try:
            rest_bytes = rest_reader(VAULT_ID_RELATIVE_PATH)
        except Exception as exc:
            return VaultIdentityResult(
                status=VaultIdentityStatus.MISMATCH,
                vault_id=vault_id,
                detail=f"REST readback failed: {exc}",
            )
        if hashlib.sha256(rest_bytes).digest() != hashlib.sha256(sentinel_bytes).digest():
            return VaultIdentityResult(
                status=VaultIdentityStatus.MISMATCH,
                vault_id=vault_id,
                detail="REST and disk sentinel hashes differ",
            )

    return VaultIdentityResult(
        status=VaultIdentityStatus.MATCHED,
        vault_id=vault_id,
        detail="disk sentinel and registry match",
    )
