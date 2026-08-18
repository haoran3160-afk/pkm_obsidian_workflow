from __future__ import annotations

from pathlib import Path

import pytest

from fetcher_registry import FetcherRegistry, dispatch_source


def test_custom_adapter_is_called_once_through_the_canonical_dispatch() -> None:
    registry = FetcherRegistry()
    calls: list[tuple[dict, dict, str]] = []

    @registry.register("fixture")
    def _fixture(config: dict, cache: dict, today: str, **_kwargs):
        calls.append((config, cache, today))
        return [{"title": "one"}]

    result = dispatch_source(
        registry=registry,
        adapter="fixture",
        config={"name": "fixture-source"},
        cache={},
        today="2026-07-26",
    )

    assert result == [{"title": "one"}]
    assert calls == [({"name": "fixture-source"}, {}, "2026-07-26")]


def test_unknown_adapter_fails_before_any_fetch_function_can_run() -> None:
    registry = FetcherRegistry()
    called = {"network": False}

    @registry.register("known")
    def _known(*_args, **_kwargs):
        called["network"] = True
        return []

    with pytest.raises(KeyError, match="unknown"):
        dispatch_source(
            registry=registry,
            adapter="unknown",
            config={},
            cache={},
            today="2026-07-26",
        )

    assert called["network"] is False


def test_adapter_names_are_additive_and_cannot_be_overwritten() -> None:
    registry = FetcherRegistry()
    registry.register("stable")(lambda *_args, **_kwargs: [])

    with pytest.raises(ValueError, match="already registered"):
        registry.register("stable")(lambda *_args, **_kwargs: [{"changed": True}])

    assert registry.list_registered() == ["stable"]


def test_main_fetch_paths_only_use_registry_dispatch() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")

    assert "fetcher_registry.dispatch_source(" in source
    assert "fetcher.fetch_rss_feed(" not in source
    assert "fetcher.fetch_youtube_channel(" not in source
    assert "fetcher.fetch_youtube_channel_raw(" not in source
