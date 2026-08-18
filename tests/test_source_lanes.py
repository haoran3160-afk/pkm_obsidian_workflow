from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fetcher
from fetcher_registry import FetcherRegistry
from source_registry import SourceRegistry
from workflow_contracts import ObservedSourceRecord, SourceHealth


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _observed(source_id: str, *, enabled: bool = True) -> ObservedSourceRecord:
    return ObservedSourceRecord(
        observed_record_id=f"observed-{source_id}",
        source_id=source_id,
        legacy_record_id=f"legacy-{source_id}",
        origin_path="fixture.json",
        origin_record_index=int(source_id.rsplit("-", 1)[-1]),
        origin_collection="rss_feeds",
        raw_record_hash=source_id.encode().hex().ljust(64, "0")[:64],
        name=source_id,
        adapter="fixture",
        endpoint=f"https://example.test/{source_id}",
        enabled=enabled,
        first_seen_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_eight_degraded_sources_probe_without_blocking_active_or_disabled() -> None:
    root = _case_root("source-lanes")
    registry = SourceRegistry(root)
    active = _observed("source-0")
    degraded = [_observed(f"source-{index}") for index in range(1, 9)]
    disabled = _observed("source-9", enabled=False)
    registry.register_observations([active, *degraded, disabled])
    failed_at = datetime(2026, 7, 25, 8, tzinfo=timezone.utc)
    for record in degraded:
        for attempt in range(3):
            registry.record_fetch_result(
                record.source_id,
                success=False,
                occurred_at=failed_at + timedelta(minutes=attempt),
                detail="fixture failure",
            )

    adapters = FetcherRegistry()
    calls: list[str] = []

    @adapters.register("fixture")
    def _fixture(config: dict, _cache: dict, _today: str, **_kwargs):
        source_id = config["source_id"]
        calls.append(source_id)
        if source_id != active.source_id:
            raise TimeoutError("probe failed")
        return [{"title": "healthy item"}]

    runner = fetcher.FetchLaneRunner(
        source_registry=registry,
        adapter_registry=adapters,
        per_source_timeout_seconds=1,
    )
    at = failed_at + timedelta(hours=25)
    result = runner.run(
        configs={
            record.source_id: {"source_id": record.source_id}
            for record in [active, *degraded, disabled]
        },
        cache={},
        today="2026-07-26",
        at=at,
    )

    assert result.main_results[0].source_id == active.source_id
    assert result.main_results[0].items == [{"title": "healthy item"}]
    assert len(result.probe_results) == 8
    assert all(not row.success for row in result.probe_results)
    assert result.skipped_disabled == (disabled.source_id,)
    assert calls[0] == active.source_id
    assert disabled.source_id not in calls
    assert len(registry.list_sources()) == 10
    assert all(
        registry.get_source(record.source_id).health is SourceHealth.DEGRADED for record in degraded
    )

    second = runner.run(
        configs={
            record.source_id: {"source_id": record.source_id}
            for record in [active, *degraded, disabled]
        },
        cache={},
        today="2026-07-26",
        at=at,
    )
    assert second.probe_results == ()


def test_successful_probe_recovers_the_same_source_id() -> None:
    root = _case_root("source-probe-recovery")
    registry = SourceRegistry(root)
    record = _observed("source-1")
    registry.register_observations([record])
    failed_at = datetime(2026, 7, 25, 8, tzinfo=timezone.utc)
    for attempt in range(3):
        registry.record_fetch_result(
            record.source_id,
            success=False,
            occurred_at=failed_at + timedelta(minutes=attempt),
            detail="fixture failure",
        )
    adapters = FetcherRegistry()
    adapters.register("fixture")(lambda *_args, **_kwargs: [{"title": "recovered"}])

    result = fetcher.FetchLaneRunner(
        source_registry=registry,
        adapter_registry=adapters,
        per_source_timeout_seconds=1,
    ).run(
        configs={record.source_id: {"source_id": record.source_id}},
        cache={},
        today="2026-07-26",
        at=failed_at + timedelta(hours=25),
    )

    assert result.probe_results[0].success is True
    assert registry.get_source(record.source_id).source_id == record.source_id
    assert registry.get_source(record.source_id).health is SourceHealth.ACTIVE


def test_main_exposes_the_canonical_lane_runner() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    assert "FetchLaneRunner" in source
