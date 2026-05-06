#!/usr/bin/env python3
"""
state_schema.py - Typed state files for digest history and health tracking.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


def read_json_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        text = path.read_text(encoding="utf-8-sig")
    return text.lstrip("\ufeff")


def write_json_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


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
    except Exception:
        return UsedArticlesState()


def save_used_articles_state(path: Path, state: UsedArticlesState) -> None:
    write_json_file(path, state.model_dump())


def compact_used_articles_state(path: Path, retention_days: int) -> UsedArticlesState:
    state = load_used_articles_state(path)
    cutoff = datetime.now().date() - timedelta(days=retention_days)
    latest: dict[str, UsedArticleEntry] = {}
    for article in state.articles:
        item_date = datetime.strptime(article.date, "%Y-%m-%d").date()
        if item_date < cutoff:
            continue
        previous = latest.get(article.url)
        if previous is None or previous.date < article.date:
            latest[article.url] = article
    compacted = UsedArticlesState(
        description=state.description,
        articles=sorted(latest.values(), key=lambda row: (row.date, row.url)),
    )
    save_used_articles_state(path, compacted)
    return compacted


def load_source_rotation_state(path: Path) -> SourceRotationState:
    if not path.exists():
        return SourceRotationState()
    try:
        raw = json.loads(read_json_text(path))
        if isinstance(raw.get("weekly_summary"), dict) and "3blue1brown_used_this_week" in raw["weekly_summary"]:
            raw["weekly_summary"]["threeblue1brown_used_this_week"] = raw["weekly_summary"].pop(
                "3blue1brown_used_this_week"
            )
        return SourceRotationState.model_validate(raw)
    except Exception:
        return SourceRotationState()


def save_source_rotation_state(path: Path, state: SourceRotationState) -> None:
    payload = state.model_dump()
    weekly = payload.get("weekly_summary", {})
    if "threeblue1brown_used_this_week" in weekly:
        weekly["3blue1brown_used_this_week"] = weekly.pop("threeblue1brown_used_this_week")
    write_json_file(path, payload)


def refresh_source_rotation_week(path: Path, today: str) -> SourceRotationState:
    state = load_source_rotation_state(path)
    week_start = week_start_for_date(today)
    if state.weekly_summary.week_start != week_start:
        state.weekly_summary.week_start = week_start
        state.weekly_summary.threeblue1brown_used_this_week = False
        save_source_rotation_state(path, state)
    return state


def load_source_health_state(path: Path) -> SourceHealthState:
    if not path.exists():
        return SourceHealthState()
    try:
        return SourceHealthState.model_validate(json.loads(read_json_text(path)))
    except Exception:
        return SourceHealthState()


def append_source_health_run(
    path: Path,
    *,
    run_date: str,
    run_at: str,
    entries: list[dict],
    keep_runs: int,
) -> SourceHealthState:
    state = load_source_health_state(path)
    state.runs.append(
        SourceHealthRun(
            run_date=run_date,
            run_at=run_at,
            entries=[SourceHealthEntry.model_validate(entry) for entry in entries],
        )
    )
    state.runs = state.runs[-keep_runs:]
    write_json_file(path, state.model_dump())
    return state


def week_start_for_date(date_str: str) -> str:
    current = datetime.strptime(date_str, "%Y-%m-%d").date()
    monday = current - timedelta(days=current.weekday())
    return monday.strftime("%Y-%m-%d")
