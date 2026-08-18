from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest

from no_delete_guard import (
    ChangeEvent,
    ChangeKind,
    NoDeleteGuard,
    PathObservation,
)


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


class FakeFeed:
    def __init__(self, events: list[ChangeEvent] | None = None) -> None:
        self.events = events or []
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> tuple[ChangeEvent, ...]:
        assert self.started
        return tuple(self.events)


def _observation(
    path: Path,
    *,
    size: int = 10,
    file_id: int = 1,
    is_directory: bool = False,
) -> PathObservation:
    return PathObservation(
        path=path,
        is_directory=is_directory,
        size=size,
        device_id=1,
        file_id=file_id,
        sha256=None if is_directory else "a" * 64,
    )


@pytest.mark.parametrize(
    ("kind", "counter"),
    [
        (ChangeKind.DELETED, "delete_count"),
        (ChangeKind.RENAMED, "rename_count"),
        (ChangeKind.TRUNCATED, "truncate_count"),
        (ChangeKind.OVERFLOW, "monitor_gap_count"),
        (ChangeKind.GAP, "monitor_gap_count"),
    ],
)
def test_destructive_or_incomplete_monitor_events_fail_closed(
    kind: ChangeKind,
    counter: str,
) -> None:
    root = _case_root(f"guard-event-{kind.value.lower()}")
    event = ChangeEvent(kind=kind, path=root / "artifact.md", detail="injected")
    feed = FakeFeed([event])
    snapshots = iter(({}, {}))
    guard = NoDeleteGuard(
        roots=[root],
        feed=feed,
        snapshot_provider=lambda _roots: next(snapshots),
    )

    guard.start()
    report = guard.finish()

    assert report.safe is False
    assert getattr(report, counter) >= 1


def test_created_then_deleted_is_detected_even_when_snapshots_match() -> None:
    root = _case_root("guard-create-delete")
    path = root / "ephemeral.md"
    feed = FakeFeed(
        [
            ChangeEvent(ChangeKind.CREATED, path, "mock create"),
            ChangeEvent(ChangeKind.DELETED, path, "mock delete"),
        ]
    )
    snapshots = iter(({}, {}))
    guard = NoDeleteGuard(
        roots=[root],
        feed=feed,
        snapshot_provider=lambda _roots: next(snapshots),
    )

    guard.start()
    report = guard.finish()

    assert report.safe is False
    assert report.delete_count == 1


def test_snapshot_detects_missing_recreated_identity_and_truncation() -> None:
    root = _case_root("guard-snapshot")
    missing = root / "missing.md"
    recreated = root / "recreated.md"
    truncated = root / "truncated.md"
    before = {
        str(missing): _observation(missing, file_id=1),
        str(recreated): _observation(recreated, file_id=2),
        str(truncated): _observation(truncated, size=100, file_id=3),
    }
    after = {
        str(recreated): _observation(recreated, file_id=20),
        str(truncated): _observation(truncated, size=10, file_id=3),
    }
    snapshots = iter((before, after))
    guard = NoDeleteGuard(
        roots=[root],
        feed=FakeFeed(),
        snapshot_provider=lambda _roots: next(snapshots),
    )

    guard.start()
    report = guard.finish()

    assert report.safe is False
    assert report.delete_count == 1
    assert report.identity_violation_count == 1
    assert report.truncate_count == 1


def test_reservation_reconciliation_requires_file_hash_and_identity() -> None:
    root = _case_root("guard-reservation")
    existing = root / "created.md"
    missing = root / "missing.md"
    payload = b"create-only"
    guard = NoDeleteGuard(roots=[root], feed=FakeFeed())
    guard.start()
    existing.write_bytes(payload)
    guard.expect_created(
        existing,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    guard.expect_created(missing, sha256="b" * 64)

    report = guard.finish()

    assert report.safe is False
    assert report.reservation_missing_count == 1
    assert report.reservation_mismatch_count == 0


def test_safe_create_only_run_reports_zero_destructive_events() -> None:
    root = _case_root("guard-safe")
    target = root / "new.md"
    payload = b"new artifact"
    guard = NoDeleteGuard(roots=[root], feed=FakeFeed())

    guard.start()
    target.write_bytes(payload)
    guard.expect_created(target, sha256=hashlib.sha256(payload).hexdigest())
    report = guard.finish()

    assert report.safe is True
    assert report.delete_count == 0
    assert report.rename_count == 0
    assert report.truncate_count == 0
    assert report.monitor_gap_count == 0
