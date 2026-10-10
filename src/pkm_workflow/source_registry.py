"""Append-only source identity and health registry."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .workflow_contracts import ObservedSourceRecord, SourceHealth

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EVENT_PATTERN = re.compile(r"^(\d{8})-([a-z-]+)\.json$")


class SourceRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceState:
    source_id: str
    observed_record_id: str
    name: str
    adapter: str
    endpoint: str
    enabled: bool
    health: SourceHealth | None
    consecutive_failures: int
    last_attempt_at: datetime | None
    next_probe_at: datetime | None


def _safe_id(value: str) -> str:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise SourceRegistryError("source_id contains unsafe characters")
    return value


def _write_create_new(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SourceRegistry:
    def __init__(
        self,
        runtime_root: Path,
        *,
        degraded_after_failures: int = 3,
        probe_interval_hours: int = 24,
    ) -> None:
        if degraded_after_failures < 1 or probe_interval_hours < 1:
            raise ValueError("source health policy values must be positive")
        self.runtime_root = Path(runtime_root).resolve(strict=True)
        self.sources_root = self.runtime_root / "sources"
        self.sources_root.mkdir(exist_ok=True)
        self.degraded_after_failures = degraded_after_failures
        self.probe_interval = timedelta(hours=probe_interval_hours)

    def _source_root(self, source_id: str) -> Path:
        return self.sources_root / _safe_id(source_id)

    def _read_events(self, source_id: str) -> list[dict[str, object]]:
        source_root = self._source_root(source_id)
        if not source_root.exists():
            raise SourceRegistryError(f"unknown source: {source_id}")
        events: list[dict[str, object]] = []
        for expected, path in enumerate(sorted(source_root.glob("*.json")), start=1):
            match = EVENT_PATTERN.fullmatch(path.name)
            if match is None or int(match.group(1)) != expected:
                raise SourceRegistryError(f"invalid source event sequence: {path}")
            try:
                event = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SourceRegistryError(f"invalid source event: {path}") from exc
            if int(event.get("sequence", -1)) != expected:
                raise SourceRegistryError(f"source event sequence mismatch: {path}")
            events.append(event)
        if not events:
            raise SourceRegistryError(f"source has no registration event: {source_id}")
        return events

    def _append_event(
        self,
        source_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        source_root = self._source_root(source_id)
        if not source_root.is_dir():
            raise SourceRegistryError(f"unknown source: {source_id}")
        existing = sorted(source_root.glob("*.json"))
        sequence = len(existing) + 1
        target = source_root / f"{sequence:08d}-{event_type}.json"
        _write_create_new(
            target,
            {
                "schema_version": 1,
                "sequence": sequence,
                "source_id": source_id,
                "event_type": event_type,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            },
        )

    def register_observations(
        self,
        observations: Sequence[ObservedSourceRecord],
    ) -> None:
        proposed_ids = [record.source_id for record in observations]
        if len(proposed_ids) != len(set(proposed_ids)):
            raise SourceRegistryError("source_id cannot be reused in one update")
        proposed_observed = [record.observed_record_id for record in observations]
        if len(proposed_observed) != len(set(proposed_observed)):
            raise SourceRegistryError("observed_record_id cannot be merged")

        for record in observations:
            source_root = self._source_root(record.source_id)
            if source_root.exists():
                current = self.get_source(record.source_id)
                if current.observed_record_id != record.observed_record_id:
                    raise SourceRegistryError("source_id was reused for another observation")
                continue
            source_root.mkdir()
            self._append_event(
                record.source_id,
                "registered",
                record.model_dump(mode="json"),
            )

    def list_sources(self) -> list[SourceState]:
        states: list[SourceState] = []
        for path in sorted(self.sources_root.iterdir(), key=lambda value: value.name):
            if not path.is_dir():
                raise SourceRegistryError(f"unexpected registry object: {path}")
            states.append(self.get_source(path.name))
        return states

    def get_source(self, source_id: str) -> SourceState:
        events = self._read_events(source_id)
        registered = events[0]
        if registered.get("event_type") != "registered":
            raise SourceRegistryError("first source event must be registered")
        registered_payload = registered.get("payload")
        if not isinstance(registered_payload, dict):
            raise SourceRegistryError("registered source payload must be an object")
        raw = dict(registered_payload)
        enabled = bool(raw["enabled"])
        health = SourceHealth.ACTIVE if enabled else None
        endpoint = str(raw["endpoint"])
        failures = 0
        last_attempt: datetime | None = None
        next_probe: datetime | None = None

        for event in events[1:]:
            event_type = str(event["event_type"])
            event_payload = event.get("payload")
            if not isinstance(event_payload, dict):
                raise SourceRegistryError("source event payload must be an object")
            payload = dict(event_payload)
            if event_type == "endpoint-updated":
                endpoint = str(payload["endpoint"])
            elif event_type in {"fetch-result", "probe-result"}:
                occurred_at = _parse_time(str(payload["occurred_at"]))
                success = bool(payload["success"])
                last_attempt = occurred_at
                if success:
                    failures = 0
                    health = SourceHealth.ACTIVE
                    next_probe = None
                else:
                    failures += 1
                    if failures >= self.degraded_after_failures:
                        health = SourceHealth.DEGRADED
                        if occurred_at is not None:
                            next_probe = occurred_at + self.probe_interval
                    elif event_type == "probe-result":
                        health = SourceHealth.PROBING
            elif event_type == "probe-started":
                occurred_at = _parse_time(str(payload["occurred_at"]))
                last_attempt = occurred_at
                health = SourceHealth.PROBING

        return SourceState(
            source_id=str(raw["source_id"]),
            observed_record_id=str(raw["observed_record_id"]),
            name=str(raw["name"]),
            adapter=str(raw["adapter"]),
            endpoint=endpoint,
            enabled=enabled,
            health=health,
            consecutive_failures=failures,
            last_attempt_at=last_attempt,
            next_probe_at=next_probe,
        )

    def record_fetch_result(
        self,
        source_id: str,
        *,
        success: bool,
        occurred_at: datetime,
        detail: str,
    ) -> None:
        state = self.get_source(source_id)
        if not state.enabled:
            raise SourceRegistryError("disabled source cannot be fetched")
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        self._append_event(
            source_id,
            "fetch-result",
            {
                "success": success,
                "occurred_at": occurred_at.isoformat(),
                "detail": detail,
            },
        )

    def record_probe_result(
        self,
        source_id: str,
        *,
        success: bool,
        occurred_at: datetime,
        detail: str,
    ) -> None:
        state = self.get_source(source_id)
        if not state.enabled:
            raise SourceRegistryError("disabled source cannot be probed")
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        self._append_event(
            source_id,
            "probe-result",
            {
                "success": success,
                "occurred_at": occurred_at.isoformat(),
                "detail": detail,
            },
        )

    def mark_probe_started(self, source_id: str, *, occurred_at: datetime) -> None:
        state = self.get_source(source_id)
        if not state.enabled:
            raise SourceRegistryError("disabled source cannot be probed")
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not self.probe_due(source_id, at=occurred_at):
            raise SourceRegistryError("source probe is not due")
        self._append_event(
            source_id,
            "probe-started",
            {"occurred_at": occurred_at.isoformat()},
        )

    def probe_due(self, source_id: str, *, at: datetime) -> bool:
        state = self.get_source(source_id)
        if not state.enabled or state.health is SourceHealth.ACTIVE:
            return False
        return state.next_probe_at is not None and at >= state.next_probe_at

    def update_endpoint(
        self,
        source_id: str,
        *,
        endpoint: str,
        reason: str,
    ) -> None:
        if not endpoint.strip() or not reason.strip():
            raise ValueError("endpoint and reason are required")
        state = self.get_source(source_id)
        self._append_event(
            source_id,
            "endpoint-updated",
            {
                "previous_endpoint": state.endpoint,
                "endpoint": endpoint,
                "reason": reason,
            },
        )

    def validate_additive_observations(
        self,
        proposed: Iterable[ObservedSourceRecord],
    ) -> None:
        proposed_list = list(proposed)
        by_source = {record.source_id: record for record in proposed_list}
        if len(by_source) != len(proposed_list):
            raise SourceRegistryError("source_id was reused in proposed observations")
        by_observed = {record.observed_record_id: record.source_id for record in proposed_list}
        if len(by_observed) != len(proposed_list):
            raise SourceRegistryError("observed_record_id was reused in proposed observations")

        existing = self.list_sources()
        missing = [state.source_id for state in existing if state.source_id not in by_source]
        if missing:
            raise SourceRegistryError(
                "additive update would remove existing sources: " + ", ".join(missing)
            )
        for state in existing:
            proposed_record = by_source[state.source_id]
            if proposed_record.observed_record_id != state.observed_record_id:
                raise SourceRegistryError(
                    f"source_id reused for another observation: {state.source_id}"
                )
