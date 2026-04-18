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
    content_type: Literal[
        "news",
        "paper",
        "tweet",
        "engineering",
        "tooling",
        "community",
        "other",
    ] = "news"
    note_folder: str = Field(..., min_length=1)
    filter_keywords: list[str] = Field(default_factory=list)
    fallback_urls: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        value = v.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"RSS feed URL must start with http:// or https://. Got: {v!r}")
        return value

    @field_validator("fallback_urls")
    @classmethod
    def fallback_urls_must_be_http(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            url = (value or "").strip()
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"Fallback URL must start with http:// or https://. Got: {value!r}"
                )
            cleaned.append(url)
        return cleaned


class YouTubeChannel(BaseModel):
    """A single YouTube channel source."""

    name: str = Field(..., min_length=1)
    channel_id: str = Field(..., min_length=1)
    domain: str = ""
    content_type: Literal["video"] = "video"
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


class PKMConfig(BaseModel):
    """Root configuration schema for pkm_config.json."""

    rss_feeds: list[RssFeed] = Field(default_factory=list)
    youtube_channels: list[YouTubeChannel] = Field(default_factory=list)

    obsidian_api: ObsidianAPIConfig = Field(default_factory=ObsidianAPIConfig)
    write_mode: Literal["disk", "api", "both"] = "disk"

    max_papers_per_day: int = Field(default=10, ge=1, le=100)
    max_videos_per_channel: int = Field(default=3, ge=1, le=20)
    max_paper_notes_per_day: int = Field(default=4, ge=0, le=50)
    max_video_notes_per_day: int = Field(default=3, ge=0, le=30)

    # Daily digest readability controls
    daily_digest_only_output: bool = True
    daily_digest_top_picks: int = Field(default=8, ge=1, le=30)
    daily_digest_max_items_per_source: int = Field(default=3, ge=1, le=20)
    daily_digest_action_items: int = Field(default=3, ge=1, le=10)
    daily_digest_max_deferred_items: int = Field(default=8, ge=1, le=30)
    daily_digest_include_mindmap: bool = True
    daily_digest_include_cognitive_lenses: bool = True
    daily_digest_quality_gate_enabled: bool = True
    daily_digest_tldr_min_quality_score: int = Field(default=1, ge=-5, le=10)
    daily_digest_tldr_max_undisclosed: int = Field(default=0, ge=0, le=5)
    daily_digest_tldr_min_items: int = Field(default=1, ge=1, le=12)
    daily_digest_tldr_min_hard_signal_ratio: float = Field(default=0.4, ge=0.0, le=1.0)
    daily_digest_tldr_max_undisclosed_ratio: float = Field(default=0.3, ge=0.0, le=3.0)
    daily_digest_min_top_nonpaper: int = Field(default=2, ge=0, le=10)
    daily_digest_min_top_content_types: int = Field(default=2, ge=1, le=10)
    daily_digest_max_paper_in_top: int = Field(default=1, ge=0, le=10)
    daily_digest_cognitive_questions: list[str] = Field(
        default_factory=lambda: [
            "能力边界是否真的前移，而不只是榜单数字更好看？",
            "架构范式是否发生变化，例如主模型加子代理或新型工具编排？",
            "成本、延迟、质量之间的前沿约束是否被改写？",
            "评测与治理是否可复现、可审计，而不只是宣传口径？",
            "这条信息能否沉淀为长期杠杆，例如 SOP、模板或基线？",
        ]
    )

    # AI content quality controls
    max_ai_items_per_feed: int = Field(default=8, ge=1, le=50)
    min_ai_interest_score: int = Field(default=4, ge=0, le=30)
    enable_fulltext_enrichment: bool = True
    fulltext_enrichment_per_feed: int = Field(default=2, ge=0, le=20)
    ai_interest_topics: list[str] = Field(default_factory=list)
    ai_priority_topics: list[str] = Field(default_factory=list)
    ai_exclude_keywords: list[str] = Field(default_factory=list)

    # Retention and health tracking controls
    used_articles_retention_days: int = Field(default=30, ge=1, le=365)
    source_health_keep_runs: int = Field(default=30, ge=1, le=365)
    raw_archive_folder: str = "01-Raw/daily-feeds"

    daily_fetch_time: str = "07:00"
    domain_mapping: dict[str, str] = Field(default_factory=dict)
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
        "daily_digest_cognitive_questions",
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
