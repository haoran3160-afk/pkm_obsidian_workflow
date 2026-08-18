#!/usr/bin/env python3
"""
fetcher_registry.py — Plugin-based Source Registry

Allows new data source types (e.g., Reddit, Twitter) to be registered
without modifying fetcher.py or main.py.

Usage (built-in sources are pre-registered):
    from fetcher_registry import get_fetcher, register_fetcher

    # Register a custom community-provided fetcher:
    @register_fetcher("reddit")
    def fetch_reddit(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
        ...

    # Dispatch dynamically:
    fn = get_fetcher("rss")
    items = fn(feed_config, cache, today)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("pkm.registry")

FetcherCallable = Callable[..., object]


class FetcherRegistry:
    """Additive adapter registry used by every ingest and probe path."""

    def __init__(self) -> None:
        self._fetchers: dict[str, FetcherCallable] = {}

    def register(self, source_type: str) -> Callable[[FetcherCallable], FetcherCallable]:
        normalized = source_type.strip().lower()
        if not normalized or not normalized.replace("_", "").replace("-", "").isalnum():
            raise ValueError("source_type contains unsafe characters")

        def decorator(fn: FetcherCallable) -> FetcherCallable:
            if normalized in self._fetchers:
                raise ValueError(f"fetcher is already registered for '{normalized}'")
            self._fetchers[normalized] = fn
            log.debug("Registered fetcher for source type: '%s'", normalized)
            return fn

        return decorator

    def get(self, source_type: str) -> FetcherCallable:
        normalized = source_type.strip().lower()
        if normalized not in self._fetchers:
            available = ", ".join(sorted(self._fetchers)) or "(none)"
            raise KeyError(
                f"No fetcher registered for source type '{normalized}'. Available: {available}"
            )
        return self._fetchers[normalized]

    def list_registered(self) -> list[str]:
        return sorted(self._fetchers)


DEFAULT_REGISTRY = FetcherRegistry()


def register_fetcher(source_type: str) -> Callable[[FetcherCallable], FetcherCallable]:
    """Backward-compatible decorator bound to the canonical registry."""

    return DEFAULT_REGISTRY.register(source_type)


def get_fetcher(source_type: str) -> FetcherCallable:
    return DEFAULT_REGISTRY.get(source_type)


def list_registered() -> list[str]:
    return DEFAULT_REGISTRY.list_registered()


def dispatch_source(
    *,
    adapter: str,
    config: dict,
    cache: dict,
    today: str,
    registry: FetcherRegistry = DEFAULT_REGISTRY,
    **kwargs,
) -> object:
    """Resolve the adapter before invoking any source/network implementation."""

    fetch = registry.get(adapter)
    return fetch(config, cache, today, **kwargs)


# ── Register Built-in Fetchers ────────────────────────────────────────────────


def _register_builtins() -> None:
    """Lazy import and register the built-in fetcher functions."""
    import fetcher as _fetcher  # avoid circular import

    @DEFAULT_REGISTRY.register("rss")
    def _fetch_rss(config: dict, cache: dict, today: str, **kwargs) -> object:
        return _fetcher.fetch_rss_feed(
            config,
            cache,
            today,
            max_papers=kwargs.get("max_papers", 10),
            raw_only=kwargs.get("raw_only", False),
            quality_config=kwargs.get("quality_config"),
            return_meta=kwargs.get("return_meta", False),
        )

    @DEFAULT_REGISTRY.register("youtube")
    def _fetch_youtube(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
        return _fetcher.fetch_youtube_channel(
            config,
            cache,
            today,
            max_videos=kwargs.get("max_videos", 3),
        )

    @DEFAULT_REGISTRY.register("youtube_raw")
    def _fetch_youtube_raw(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
        del cache, today
        return _fetcher.fetch_youtube_channel_raw(
            config,
            max_videos=kwargs.get("max_videos", 2),
        )

    @DEFAULT_REGISTRY.register("rss_probe")
    def _probe_rss(config: dict, cache: dict, today: str, **kwargs) -> list[object]:
        del cache, today, kwargs
        parsed = _fetcher._parse_feed(config["url"])
        return list(getattr(parsed, "entries", []))

    @DEFAULT_REGISTRY.register("youtube_probe")
    def _probe_youtube(
        config: dict,
        cache: dict,
        today: str,
        **kwargs,
    ) -> list[object]:
        del cache, today, kwargs
        parsed = _fetcher._parse_youtube_feed(config["channel_id"])
        return list(getattr(parsed, "entries", []))


_register_builtins()
