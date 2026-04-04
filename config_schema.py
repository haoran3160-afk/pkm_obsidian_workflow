#!/usr/bin/env python3
"""
config_schema.py — Pydantic v2 schema for pkm_config.json.

Validates the configuration at startup and provides typed access to all
settings. Any missing required field or wrong type will raise a clear,
human-readable error instead of a runtime KeyError or AttributeError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Sub-models ────────────────────────────────────────────────────────────────

class RssFeed(BaseModel):
    """A single RSS/Atom feed source."""

    name: str = Field(..., min_length=1)
    url: str = Field(..., description="Full HTTP/HTTPS URL of the RSS feed")
    domain: str = ""
    note_folder: str = Field(..., min_length=1)
    filter_keywords: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"RSS feed URL must start with http:// or https://. Got: {v!r}")
        return v.strip()


class YouTubeChannel(BaseModel):
    """A single YouTube channel source."""

    name: str = Field(..., min_length=1)
    channel_id: str = Field(..., min_length=1)
    domain: str = ""
    note_folder: str = Field(..., min_length=1)

    @field_validator("channel_id")
    @classmethod
    def channel_id_looks_valid(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("UC") or len(v) != 24:
            # Soft warning — don't block, just note.
            pass
        return v


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


# ── Root Config ───────────────────────────────────────────────────────────────

class PKMConfig(BaseModel):
    """
    Root configuration schema for pkm_config.json.

    All fields have sensible defaults so that a minimal config
    (just rss_feeds and vault-related settings) still works.
    """

    # Sources
    rss_feeds: list[RssFeed] = Field(default_factory=list)
    youtube_channels: list[YouTubeChannel] = Field(default_factory=list)

    # Optional Obsidian REST API
    obsidian_api: ObsidianAPIConfig = Field(default_factory=ObsidianAPIConfig)

    # Write mode: "disk" writes directly to vault, "api" uses REST API
    write_mode: Literal["disk", "api", "both"] = "disk"

    # Caps
    max_papers_per_day: int = Field(default=10, ge=1, le=100)
    max_videos_per_channel: int = Field(default=3, ge=1, le=20)

    # Scheduling
    daily_fetch_time: str = "07:00"

    # Domain mapping (lowercase domain → Vault sub-folder)
    domain_mapping: dict[str, str] = Field(default_factory=dict)

    # Optional IELTS resources block
    ielts_resources: IELTSResources | None = None

    # Legacy vault_path key (ignored — path comes from .env)
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

    @model_validator(mode="after")
    def at_least_one_source(self) -> PKMConfig:
        if not self.rss_feeds and not self.youtube_channels:
            raise ValueError(
                "pkm_config.json must define at least one rss_feeds or youtube_channels entry."
            )
        return self


# ── Loader ────────────────────────────────────────────────────────────────────

def load_and_validate(config_path: Path) -> PKMConfig:
    """
    Load pkm_config.json and validate it against PKMConfig schema.

    Raises:
        FileNotFoundError: if the config file doesn't exist.
        ValueError: if the config is invalid, with a clear human-readable message.
    """
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            "Please ensure pkm_config.json exists in the project directory."
        )

    raw = json.loads(config_path.read_text(encoding="utf-8"))

    try:
        return PKMConfig.model_validate(raw)
    except Exception as exc:
        # Re-raise with a friendlier prefix
        raise ValueError(
            f"Invalid pkm_config.json:\n{exc}\n\n"
            "Run `python main.py --doctor` for a guided diagnosis."
        ) from exc
