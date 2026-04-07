#!/usr/bin/env python3
"""
config_schema.py - Pydantic v2 schema for pkm_config.json.

Validates configuration at startup and provides typed access to settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RssFeed(BaseModel):
    """A single RSS/Atom feed source."""

    name: str = Field(..., min_length=1)
    url: str = Field(..., description="Full HTTP/HTTPS URL of the RSS feed")
    domain: str = ""
    note_folder: str = Field(..., min_length=1)
    filter_keywords: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        value = v.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"RSS feed URL must start with http:// or https://. Got: {v!r}")
        return value


class YouTubeChannel(BaseModel):
    """A single YouTube channel source."""

    name: str = Field(..., min_length=1)
    channel_id: str = Field(..., min_length=1)
    domain: str = ""
    note_folder: str = Field(..., min_length=1)
    enabled: bool = True

    @field_validator("channel_id")
    @classmethod
    def channel_id_looks_valid(cls, v: str) -> str:
        return v.strip()


class ObsidianAPIConfig(BaseModel):
    """Obsidian Local REST API connection settings."""

    base_url: str = "http://localhost:27123"
    api_key: str = ""


class IELTSPracticeSite(BaseModel):
    name: str
    url: str
    skills: list[str] = Field(default_factory=list)


class IELTSResources(BaseModel):
    practice_sites: list[IELTSPracticeSite] = Field(default_factory=list)
    youtube_channels: list[dict] = Field(default_factory=list)
    books: list[str] = Field(default_factory=list)


class PKMConfig(BaseModel):
    """Root configuration schema for pkm_config.json."""

    rss_feeds: list[RssFeed] = Field(default_factory=list)
    youtube_channels: list[YouTubeChannel] = Field(default_factory=list)

    obsidian_api: ObsidianAPIConfig = Field(default_factory=ObsidianAPIConfig)
    write_mode: Literal["disk", "api", "both"] = "disk"

    max_papers_per_day: int = Field(default=10, ge=1, le=100)
    max_videos_per_channel: int = Field(default=3, ge=1, le=20)

    # AI content quality controls
    max_ai_items_per_feed: int = Field(default=8, ge=1, le=50)
    min_ai_interest_score: int = Field(default=4, ge=0, le=30)
    ai_interest_topics: list[str] = Field(default_factory=list)
    ai_priority_topics: list[str] = Field(default_factory=list)
    ai_exclude_keywords: list[str] = Field(default_factory=list)

    # IELTS accessibility controls
    validate_ielts_urls: bool = True
    ielts_request_timeout_sec: int = Field(default=8, ge=2, le=30)
    ielts_accessible_domains: list[str] = Field(default_factory=list)

    # Retention and health tracking controls
    used_articles_retention_days: int = Field(default=30, ge=1, le=365)
    source_health_keep_runs: int = Field(default=30, ge=1, le=365)
    raw_archive_folder: str = "01-Raw/daily-feeds"

    daily_fetch_time: str = "07:00"
    domain_mapping: dict[str, str] = Field(default_factory=dict)
    ielts_resources: IELTSResources | None = None
    vault_path: str | None = None

    @field_validator("daily_fetch_time")
    @classmethod
    def time_format_is_valid(cls, v: str) -> str:
        try:
            h, m = v.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59  # noqa: S101
        except Exception as exc:
            raise ValueError(f"daily_fetch_time must be HH:MM format. Got: {v!r}") from exc
        return v

    @field_validator(
        "ai_interest_topics",
        "ai_priority_topics",
        "ai_exclude_keywords",
        "ielts_accessible_domains",
    )
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        return [v.strip() for v in values if v and v.strip()]

    @model_validator(mode="after")
    def at_least_one_source(self) -> PKMConfig:
        if not self.rss_feeds and not self.youtube_channels:
            raise ValueError(
                "pkm_config.json must define at least one rss_feeds or youtube_channels entry."
            )
        return self


def load_and_validate(config_path: Path) -> PKMConfig:
    """Load and validate pkm_config.json."""
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            "Please ensure pkm_config.json exists in the project directory."
        )

    raw = json.loads(config_path.read_text(encoding="utf-8"))

    try:
        return PKMConfig.model_validate(raw)
    except Exception as exc:  # pragma: no cover - validation path
        raise ValueError(
            f"Invalid pkm_config.json:\n{exc}\n\n"
            "Run `python main.py --doctor` for a guided diagnosis."
        ) from exc
