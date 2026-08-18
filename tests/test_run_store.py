from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from run_store import FencingError, RunStore, RunStoreCorruption
from workflow_contracts import EventType, RunStatus, StageStatus


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def test_lock_anchor_is_create_once_and_tokens_are_monotonic() -> None:
    root = _case_root("run-lock")
    store = RunStore(root)
    anchor = root / "control.lock"
    before = anchor.stat()

    first = store.allocate_fencing_token("run-a")
    second = store.allocate_fencing_token("run-b")
    after = anchor.stat()

    assert (first, second) == (1, 2)
    assert anchor.read_bytes() == b"\x00"
    assert before.st_ino == after.st_ino
    assert sorted(path.name for path in (root / "tokens").glob("*.json")) == [
        "00000000000000000001-run-a.json",
        "00000000000000000002-run-b.json",
    ]


def test_run_events_are_append_only_and_replay_to_canonical_state() -> None:
    root = _case_root("run-events")
    store = RunStore(root)
    token = store.allocate_fencing_token("run-a")

    created = store.append_run_event(
        run_id="run-a",
        fencing_token=token,
        event_type=EventType.RUN_CREATED,
        payload={"run_status": RunStatus.PENDING.value},
    )
    completed = store.append_run_event(
        run_id="run-a",
        fencing_token=token,
        event_type=EventType.STAGE_CHANGED,
        stage="ingest",
        stage_status=StageStatus.SUCCEEDED,
        payload={"run_status": RunStatus.PARTIAL.value},
    )
    state = store.replay_run("run-a")

    assert created.sequence == 1
    assert completed.sequence == 2
    assert state.status is RunStatus.PARTIAL
    assert state.stages == {"ingest": StageStatus.SUCCEEDED}
    assert len(state.events) == 2


def test_stale_fencing_token_is_rejected_before_event_creation() -> None:
    root = _case_root("stale-token")
    store = RunStore(root)
    stale = store.allocate_fencing_token("run-a")
    current = store.allocate_fencing_token("run-b")
    before = tuple((root / "runs").rglob("*.json"))

    with pytest.raises(FencingError, match="stale"):
        store.append_run_event(
            run_id="run-a",
            fencing_token=stale,
            event_type=EventType.ABORTED,
            payload={"run_status": RunStatus.BLOCKED.value},
        )

    assert current == 2
    assert tuple((root / "runs").rglob("*.json")) == before


def test_corrupt_event_blocks_replay_without_resetting_history() -> None:
    root = _case_root("corrupt-event")
    store = RunStore(root)
    run_dir = root / "runs" / "run-a"
    run_dir.mkdir()
    corrupt = run_dir / "00000001-run-created.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    original = corrupt.read_bytes()

    with pytest.raises(RunStoreCorruption, match="invalid event"):
        store.replay_run("run-a")

    assert corrupt.read_bytes() == original
    assert len(tuple(run_dir.iterdir())) == 1
