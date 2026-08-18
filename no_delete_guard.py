"""Realtime and reconciliation guard for the workflow no-delete invariant."""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from path_policy import get_file_identity


class ChangeKind(str, Enum):
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RENAMED = "RENAMED"
    TRUNCATED = "TRUNCATED"
    OVERFLOW = "OVERFLOW"
    GAP = "GAP"


@dataclass(frozen=True)
class ChangeEvent:
    kind: ChangeKind
    path: Path
    detail: str


@dataclass(frozen=True)
class PathObservation:
    path: Path
    is_directory: bool
    size: int
    device_id: int
    file_id: int
    sha256: str | None


@dataclass(frozen=True)
class CreationExpectation:
    path: Path
    sha256: str


@dataclass(frozen=True)
class NoDeleteReport:
    safe: bool
    delete_count: int
    rename_count: int
    truncate_count: int
    monitor_gap_count: int
    identity_violation_count: int
    reservation_missing_count: int
    reservation_mismatch_count: int
    events: tuple[ChangeEvent, ...]


class ChangeFeed(Protocol):
    def start(self) -> None: ...

    def stop(self) -> tuple[ChangeEvent, ...]: ...


SnapshotProvider = Callable[
    [Sequence[Path]],
    Mapping[str, PathObservation],
]


def _snapshot_paths(roots: Sequence[Path]) -> dict[str, PathObservation]:
    observations: dict[str, PathObservation] = {}
    for root in roots:
        candidates = (root, *root.rglob("*"))
        for path in candidates:
            identity = get_file_identity(path)
            digest = None
            if not identity.is_directory:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            key = os.path.normcase(str(identity.canonical_path))
            observations[key] = PathObservation(
                path=identity.canonical_path,
                is_directory=identity.is_directory,
                size=identity.size,
                device_id=identity.device_id,
                file_id=identity.file_id,
                sha256=digest,
            )
    return observations


class WindowsDirectoryChangeFeed:
    """ReadDirectoryChangesW feed that reports overflow as a blocking gap."""

    def __init__(self, roots: Sequence[Path]) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsDirectoryChangeFeed requires Windows")
        self._roots = tuple(Path(root).resolve(strict=True) for root in roots)
        self._handles: list[int] = []
        self._threads: list[threading.Thread] = []
        self._events: list[ChangeEvent] = []
        self._event_lock = threading.Lock()
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("change feed is already started")
        kernel32 = ctypes.windll.kernel32
        create_file = kernel32.CreateFileW
        create_file.restype = ctypes.c_void_p
        for index, root in enumerate(self._roots):
            handle = create_file(
                str(root),
                0x0001,
                0x00000001 | 0x00000002 | 0x00000004,
                None,
                3,
                0x02000000,
                None,
            )
            if handle in (None, ctypes.c_void_p(-1).value):
                raise OSError(f"cannot monitor managed root: {root}")
            self._handles.append(int(handle))
            thread = threading.Thread(
                target=self._watch,
                args=(root, int(handle)),
                name=f"pkm-no-delete-{index}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _watch(self, root: Path, handle: int) -> None:
        kernel32 = ctypes.windll.kernel32
        read_changes = kernel32.ReadDirectoryChangesW
        buffer = ctypes.create_string_buffer(65536)
        returned = ctypes.c_ulong()
        notify_filter = 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0040
        action_map = {
            1: ChangeKind.CREATED,
            2: ChangeKind.DELETED,
            3: ChangeKind.MODIFIED,
            4: ChangeKind.RENAMED,
            5: ChangeKind.RENAMED,
        }
        while not self._stopping.is_set():
            ok = read_changes(
                ctypes.c_void_p(handle),
                buffer,
                len(buffer),
                True,
                notify_filter,
                ctypes.byref(returned),
                None,
                None,
            )
            if not ok:
                error = kernel32.GetLastError()
                if self._stopping.is_set() and error == 995:
                    return
                self._add_event(
                    ChangeEvent(
                        ChangeKind.GAP,
                        root,
                        f"ReadDirectoryChangesW failed with {error}",
                    )
                )
                return
            if returned.value == 0:
                self._add_event(ChangeEvent(ChangeKind.OVERFLOW, root, "empty change buffer"))
                continue
            offset = 0
            while offset < returned.value:
                base = ctypes.addressof(buffer) + offset
                next_offset = ctypes.c_ulong.from_address(base).value
                action = ctypes.c_ulong.from_address(base + 4).value
                name_length = ctypes.c_ulong.from_address(base + 8).value
                name_bytes = ctypes.string_at(base + 12, name_length)
                name = name_bytes.decode("utf-16-le", errors="strict")
                kind = action_map.get(action, ChangeKind.GAP)
                self._add_event(ChangeEvent(kind, root / Path(name), f"action={action}"))
                if next_offset == 0:
                    break
                offset += next_offset

    def _add_event(self, event: ChangeEvent) -> None:
        with self._event_lock:
            self._events.append(event)

    def stop(self) -> tuple[ChangeEvent, ...]:
        self._stopping.set()
        kernel32 = ctypes.windll.kernel32
        for handle in self._handles:
            kernel32.CancelIoEx(ctypes.c_void_p(handle), None)
        for thread in self._threads:
            thread.join(timeout=5)
            if thread.is_alive():
                self._add_event(
                    ChangeEvent(
                        ChangeKind.GAP,
                        Path("."),
                        "monitor thread did not stop",
                    )
                )
        for handle in self._handles:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        with self._event_lock:
            return tuple(self._events)


class NoDeleteGuard:
    """Fail closed on destructive events, snapshot drift, or bad reservations."""

    def __init__(
        self,
        *,
        roots: Sequence[Path],
        feed: ChangeFeed | None = None,
        snapshot_provider: SnapshotProvider = _snapshot_paths,
    ) -> None:
        if not roots:
            raise ValueError("at least one managed root is required")
        self._roots = tuple(Path(root).resolve(strict=True) for root in roots)
        self._feed = feed or WindowsDirectoryChangeFeed(self._roots)
        self._snapshot_provider = snapshot_provider
        self._before: Mapping[str, PathObservation] | None = None
        self._expectations: list[CreationExpectation] = []
        self._finished = False

    def start(self) -> None:
        if self._before is not None:
            raise RuntimeError("guard is already started")
        self._feed.start()
        self._before = self._snapshot_provider(self._roots)

    def expect_created(self, path: Path, *, sha256: str) -> None:
        if self._before is None or self._finished:
            raise RuntimeError("creation expectations require an active guard")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        target = Path(path)
        resolved_parent = target.parent.resolve(strict=True)
        if not any(
            resolved_parent == root
            or os.path.commonpath([str(resolved_parent), str(root)]) == str(root)
            for root in self._roots
        ):
            raise ValueError("expected creation is outside managed roots")
        self._expectations.append(
            CreationExpectation(path=resolved_parent / target.name, sha256=sha256)
        )

    def finish(self) -> NoDeleteReport:
        if self._before is None:
            raise RuntimeError("guard must be started before finish")
        if self._finished:
            raise RuntimeError("guard can only be finished once")
        self._finished = True
        events = list(self._feed.stop())
        try:
            after = self._snapshot_provider(self._roots)
        except (OSError, ValueError) as exc:
            after = {}
            events.append(ChangeEvent(ChangeKind.GAP, self._roots[0], f"snapshot failed: {exc}"))

        deleted = sum(event.kind is ChangeKind.DELETED for event in events)
        renamed = sum(event.kind is ChangeKind.RENAMED for event in events)
        truncated = sum(event.kind is ChangeKind.TRUNCATED for event in events)
        gaps = sum(event.kind in {ChangeKind.OVERFLOW, ChangeKind.GAP} for event in events)
        identity_violations = 0
        for key, before in self._before.items():
            current = after.get(key)
            if current is None:
                deleted += 1
                continue
            if (
                before.device_id != current.device_id
                or before.file_id != current.file_id
                or before.is_directory != current.is_directory
            ):
                identity_violations += 1
            if not before.is_directory and current.size < before.size:
                truncated += 1

        reservation_missing = 0
        reservation_mismatch = 0
        for expectation in self._expectations:
            if not expectation.path.is_file():
                reservation_missing += 1
                continue
            try:
                actual_hash = hashlib.sha256(expectation.path.read_bytes()).hexdigest()
                get_file_identity(expectation.path)
            except OSError:
                reservation_mismatch += 1
                continue
            if actual_hash != expectation.sha256:
                reservation_mismatch += 1

        safe = not any(
            (
                deleted,
                renamed,
                truncated,
                gaps,
                identity_violations,
                reservation_missing,
                reservation_mismatch,
            )
        )
        return NoDeleteReport(
            safe=safe,
            delete_count=deleted,
            rename_count=renamed,
            truncate_count=truncated,
            monitor_gap_count=gaps,
            identity_violation_count=identity_violations,
            reservation_missing_count=reservation_missing,
            reservation_mismatch_count=reservation_mismatch,
            events=tuple(events),
        )
