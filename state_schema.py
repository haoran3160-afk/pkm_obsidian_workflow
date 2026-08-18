#!/usr/bin/env python3
"""
state_schema.py - Read-only legacy state importer and typed legacy schemas.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from run_store import RunStore


class LegacyStateImportError(RuntimeError):
    """Legacy state was invalid, changed during import, or requested mutation."""


class LegacyStateKind(str, Enum):
    FEED_CACHE = "feed_cache"
    USED_ARTICLES = "used_articles"
    SOURCE_ROTATION = "source_rotation"
    SOURCE_HEALTH = "source_health"


def read_json_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        text = path.read_text(encoding="utf-8-sig")
    return text.lstrip("\ufeff")


def write_json_file(path: Path, payload: dict) -> None:
    del path, payload
    raise LegacyStateImportError(
        "legacy state is read-only; write append-only runtime events instead"
    )


class UsedArticleEntry(BaseModel):
    date: str
    url: str

    @field_validator("date")
    @classmethod
    def date_must_be_iso_day(cls, value: str) -> str:
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @field_validator("url")
    @classmethod
    def url_must_not_be_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("url cannot be empty")
        return normalized


class UsedArticlesState(BaseModel):
    description: str = "Tracks URLs used in past daily digests to prevent duplication."
    articles: list[UsedArticleEntry] = Field(default_factory=list)


class SourceRotationEntry(BaseModel):
    category: str = ""
    last_used: str = ""

    @field_validator("last_used")
    @classmethod
    def allow_blank_or_iso_day(cls, value: str) -> str:
        normalized = value.strip()
        if normalized:
            datetime.strptime(normalized, "%Y-%m-%d")
        return normalized


class WeeklySummaryState(BaseModel):
    week_start: str = ""
    threeblue1brown_used_this_week: bool = False

    @field_validator("week_start")
    @classmethod
    def allow_blank_or_iso_day(cls, value: str) -> str:
        normalized = value.strip()
        if normalized:
            datetime.strptime(normalized, "%Y-%m-%d")
        return normalized


class SourceRotationState(BaseModel):
    sources: dict[str, SourceRotationEntry] = Field(default_factory=dict)
    weekly_summary: WeeklySummaryState = Field(default_factory=WeeklySummaryState)


class SourceHealthEntry(BaseModel):
    timestamp: str
    source: str
    kind: str
    status: str
    item_count: int = 0
    detail: str = ""

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_iso_like(cls, value: str) -> str:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        return value


class SourceHealthRun(BaseModel):
    run_date: str
    run_at: str
    entries: list[SourceHealthEntry] = Field(default_factory=list)

    @field_validator("run_date")
    @classmethod
    def run_date_must_be_iso_day(cls, value: str) -> str:
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @field_validator("run_at")
    @classmethod
    def run_at_must_be_iso_like(cls, value: str) -> str:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        return value


class SourceHealthState(BaseModel):
    runs: list[SourceHealthRun] = Field(default_factory=list)


def load_used_articles_state(path: Path) -> UsedArticlesState:
    if not path.exists():
        return UsedArticlesState()
    try:
        return UsedArticlesState.model_validate(json.loads(read_json_text(path)))
    except Exception as exc:
        raise LegacyStateImportError(f"invalid legacy state: {path}") from exc


def save_used_articles_state(path: Path, state: UsedArticlesState) -> None:
    write_json_file(path, state.model_dump())


def compact_used_articles_state(path: Path, retention_days: int) -> UsedArticlesState:
    del path, retention_days
    raise LegacyStateImportError(
        "legacy state is read-only; retention is retain-all under no-delete"
    )


def load_source_rotation_state(path: Path) -> SourceRotationState:
    if not path.exists():
        return SourceRotationState()
    try:
        raw = json.loads(read_json_text(path))
        if (
            isinstance(raw.get("weekly_summary"), dict)
            and "3blue1brown_used_this_week" in raw["weekly_summary"]
        ):
            raw["weekly_summary"]["threeblue1brown_used_this_week"] = raw["weekly_summary"].pop(
                "3blue1brown_used_this_week"
            )
        return SourceRotationState.model_validate(raw)
    except Exception as exc:
        raise LegacyStateImportError(f"invalid legacy state: {path}") from exc


def save_source_rotation_state(path: Path, state: SourceRotationState) -> None:
    payload = state.model_dump()
    weekly = payload.get("weekly_summary", {})
    if "threeblue1brown_used_this_week" in weekly:
        weekly["3blue1brown_used_this_week"] = weekly.pop("threeblue1brown_used_this_week")
    write_json_file(path, payload)


def refresh_source_rotation_week(path: Path, today: str) -> SourceRotationState:
    del path, today
    raise LegacyStateImportError("legacy state is read-only; write a source-rotation event instead")


def load_source_health_state(path: Path) -> SourceHealthState:
    if not path.exists():
        return SourceHealthState()
    try:
        return SourceHealthState.model_validate(json.loads(read_json_text(path)))
    except Exception as exc:
        raise LegacyStateImportError(f"invalid legacy state: {path}") from exc


def append_source_health_run(
    path: Path,
    *,
    run_date: str,
    run_at: str,
    entries: list[dict],
    keep_runs: int,
) -> SourceHealthState:
    del path, run_date, run_at, entries, keep_runs
    raise LegacyStateImportError("legacy state is read-only; write a source-health event instead")


def week_start_for_date(date_str: str) -> str:
    current = datetime.strptime(date_str, "%Y-%m-%d").date()
    monday = current.fromordinal(current.toordinal() - current.weekday())
    return monday.strftime("%Y-%m-%d")


class LegacyStateImporter:
    """Import exact legacy files into immutable events without mutating sources."""

    def __init__(
        self,
        *,
        store: RunStore,
        sources: Mapping[LegacyStateKind, Path],
    ) -> None:
        self.store = store
        self.sources = {kind: Path(path).resolve(strict=True) for kind, path in sources.items()}
        if not self.sources:
            raise ValueError("at least one legacy state source is required")
        if any(not path.is_file() for path in self.sources.values()):
            raise ValueError("every legacy state source must be a regular file")

    def import_all(
        self,
        *,
        legacy_retention_days: tuple[int, ...] = (7, 30),
    ) -> dict[LegacyStateKind, dict[str, object]]:
        return {
            kind: self.import_one(
                kind,
                legacy_retention_days=legacy_retention_days,
            )
            for kind in self.sources
        }

    def import_one(
        self,
        kind: LegacyStateKind,
        *,
        legacy_retention_days: tuple[int, ...] = (7, 30),
    ) -> dict[str, object]:
        if kind not in self.sources:
            raise LegacyStateImportError(
                f"legacy state kind is not bound to an exact source: {kind.value}"
            )
        if any(days <= 0 for days in legacy_retention_days):
            raise ValueError("legacy retention values must be positive")
        source = self.sources[kind]
        before = source.stat()
        raw = source.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
            record_count = self._validate_payload(kind, payload)
        except Exception as exc:
            if isinstance(exc, LegacyStateImportError):
                raise
            raise LegacyStateImportError(f"invalid legacy state: {source}") from exc
        after_raw = source.read_bytes()
        after = source.stat()
        if (
            raw != after_raw
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
        ):
            raise LegacyStateImportError(f"legacy state changed during read-only import: {source}")
        source_sha256 = hashlib.sha256(raw).hexdigest()
        retention_values = sorted(set(legacy_retention_days))
        return self.store.record_legacy_state_import(
            state_kind=kind.value,
            source_path=str(source),
            source_sha256=source_sha256,
            legacy_payload=payload,
            record_count=record_count,
            retention_resolution={
                "legacy_values_days": retention_values,
                "effective_policy": "retain_all_no_delete",
            },
        )

    @staticmethod
    def _validate_payload(kind: LegacyStateKind, payload: object) -> int:
        if not isinstance(payload, dict):
            raise LegacyStateImportError(
                f"invalid legacy state: {kind.value} must be a JSON object"
            )
        if kind is LegacyStateKind.FEED_CACHE:
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
            ):
                raise LegacyStateImportError(
                    "invalid legacy state: feed_cache must map strings to strings"
                )
            return len(payload)
        if kind is LegacyStateKind.USED_ARTICLES:
            used_model = UsedArticlesState.model_validate(payload)
            return len(used_model.articles)
        if kind is LegacyStateKind.SOURCE_ROTATION:
            compatible = json.loads(json.dumps(payload, ensure_ascii=False))
            weekly = compatible.get("weekly_summary")
            if isinstance(weekly, dict) and "3blue1brown_used_this_week" in weekly:
                weekly["threeblue1brown_used_this_week"] = weekly["3blue1brown_used_this_week"]
            rotation_model = SourceRotationState.model_validate(compatible)
            return len(rotation_model.sources)
        health_model = SourceHealthState.model_validate(payload)
        return len(health_model.runs)
