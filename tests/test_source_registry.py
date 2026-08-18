from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from source_registry import SourceRegistry, SourceRegistryError
from workflow_contracts import ObservedSourceRecord, SourceHealth


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _observed(
    source_id: str,
    observed_id: str,
    *,
    enabled: bool = True,
    endpoint: str = "https://example.com/feed.xml",
) -> ObservedSourceRecord:
    return ObservedSourceRecord(
        observed_record_id=observed_id,
        source_id=source_id,
        legacy_record_id=f"private::rss_feeds::{observed_id}",
        origin_path="private-config.json",
        origin_record_index=0,
        origin_collection="rss_feeds",
        raw_record_hash="a" * 64,
        name=observed_id,
        adapter="rss",
        endpoint=endpoint,
        enabled=enabled,
        first_seen_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )


def test_registry_keeps_duplicate_endpoints_as_separate_source_identities() -> None:
    root = _case_root("registry-identity")
    registry = SourceRegistry(root)
    first = _observed("source-a", "observed-a")
    second = _observed("source-b", "observed-b")

    registry.register_observations([first, second])
    states = registry.list_sources()

    assert {state.source_id for state in states} == {"source-a", "source-b"}
    assert {state.observed_record_id for state in states} == {
        "observed-a",
        "observed-b",
    }
    assert {state.endpoint for state in states} == {"https://example.com/feed.xml"}


def test_enabled_source_degrades_after_three_failures_and_recovers_same_identity() -> None:
    root = _case_root("registry-health")
    registry = SourceRegistry(root, degraded_after_failures=3, probe_interval_hours=24)
    registry.register_observations([_observed("source-a", "observed-a")])
    start = datetime(2026, 7, 26, tzinfo=timezone.utc)

    for offset in range(3):
        registry.record_fetch_result(
            "source-a",
            success=False,
            occurred_at=start + timedelta(hours=offset),
            detail=f"failure-{offset}",
        )

    degraded = registry.get_source("source-a")
    assert degraded.health is SourceHealth.DEGRADED
    assert registry.probe_due(
        "source-a",
        at=start + timedelta(hours=27),
    )

    registry.record_probe_result(
        "source-a",
        success=True,
        occurred_at=start + timedelta(hours=28),
        detail="recovered",
    )
    recovered = registry.get_source("source-a")

    assert recovered.source_id == "source-a"
    assert recovered.health is SourceHealth.ACTIVE
    assert recovered.consecutive_failures == 0


def test_disabled_source_is_preserved_but_never_auto_probed() -> None:
    root = _case_root("registry-disabled")
    registry = SourceRegistry(root)
    registry.register_observations(
        [_observed("source-disabled", "observed-disabled", enabled=False)]
    )

    state = registry.get_source("source-disabled")

    assert state.enabled is False
    assert state.health is None
    assert (
        registry.probe_due(
            "source-disabled",
            at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        is False
    )


def test_additive_update_rejects_missing_or_reused_source_identity() -> None:
    root = _case_root("registry-additive")
    registry = SourceRegistry(root)
    first = _observed("source-a", "observed-a")
    second = _observed("source-b", "observed-b")
    registry.register_observations([first, second])
    before = tuple(root.rglob("*.json"))

    with pytest.raises(SourceRegistryError, match="remove"):
        registry.validate_additive_observations([first])
    with pytest.raises(SourceRegistryError, match="reused"):
        registry.validate_additive_observations(
            [
                _observed("source-a", "different-observation"),
                second,
            ]
        )

    assert tuple(root.rglob("*.json")) == before
