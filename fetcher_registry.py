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
from typing import Callable

log = logging.getLogger("pkm.registry")

# Registry: source_type (str) → fetch function
_FETCHERS: dict[str, Callable[..., list[dict]]] = {}


def register_fetcher(source_type: str) -> Callable:
    """
    Decorator to register a fetcher function for a given source type.

    Example:
        @register_fetcher("rss")
        def fetch_rss(config, cache, today, **kwargs) -> list[dict]:
            ...
    """
    def decorator(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
        if source_type in _FETCHERS:
            log.warning("Fetcher already registered for '%s', overwriting.", source_type)
        _FETCHERS[source_type] = fn
        log.debug("Registered fetcher for source type: '%s'", source_type)
        return fn
    return decorator


def get_fetcher(source_type: str) -> Callable[..., list[dict]]:
    """
    Retrieve the registered fetcher function for a source type.

    Raises:
        KeyError: if no fetcher is registered for the given source type.
    """
    if source_type not in _FETCHERS:
        available = ", ".join(sorted(_FETCHERS.keys())) or "(none)"
        raise KeyError(
            f"No fetcher registered for source type '{source_type}'. "
            f"Available: {available}\n"
            "To add a custom fetcher, use @register_fetcher('<type>') decorator."
        )
    return _FETCHERS[source_type]


def list_registered() -> list[str]:
    """Return a sorted list of all registered source type names."""
    return sorted(_FETCHERS.keys())


# ── Register Built-in Fetchers ────────────────────────────────────────────────

def _register_builtins() -> None:
    """Lazy import and register the built-in fetcher functions."""
    import fetcher as _fetcher  # avoid circular import

    @register_fetcher("rss")
    def _fetch_rss(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
        return _fetcher.fetch_rss_feed(
            config, cache, today,
            max_papers=kwargs.get("max_papers", 10),
            raw_only=kwargs.get("raw_only", False),
        )

    @register_fetcher("youtube")
    def _fetch_youtube(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
        return _fetcher.fetch_youtube_channel(
            config, cache, today,
            max_videos=kwargs.get("max_videos", 3),
        )

    @register_fetcher("youtube_raw")
    def _fetch_youtube_raw(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
        return _fetcher.fetch_youtube_channel_raw(config)


_register_builtins()
