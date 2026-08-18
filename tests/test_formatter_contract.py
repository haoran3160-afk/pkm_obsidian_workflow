from __future__ import annotations

import pytest

import formatter


def _item(title: str, *, score: int = 1, summary: str = "signal 42") -> dict:
    return {
        "title": title,
        "link": f"https://example.test/{title}",
        "summary": summary,
        "score": score,
        "content_type": "news",
        "domain": "ai-news",
        "published": "2026-07-26",
    }


def test_per_source_limit_changes_raw_output() -> None:
    items = {"Source": [_item("one"), _item("two"), _item("three")]}

    _, one = formatter.format_daily_digest(
        items,
        raw_only=True,
        max_items_per_source=1,
        quality_gate_enabled=False,
    )
    _, three = formatter.format_daily_digest(
        items,
        raw_only=True,
        max_items_per_source=3,
        quality_gate_enabled=False,
    )

    assert "one" in one and "two" not in one
    assert "one" in three and "two" in three and "three" in three


def test_quality_threshold_filters_low_quality_items() -> None:
    items = {"Source": [_item("low", score=1), _item("high", score=8)]}

    _, content = formatter.format_daily_digest(
        items,
        raw_only=True,
        quality_gate_enabled=True,
        tldr_min_quality_score=5,
        tldr_min_items=1,
        tldr_min_hard_signal_ratio=0,
    )

    assert "high" in content
    assert "low" not in content


def test_quality_gate_minimum_and_undisclosed_thresholds_fail_explicitly() -> None:
    with pytest.raises(formatter.FormatterQualityError, match="minimum items"):
        formatter.format_daily_digest(
            {"Source": [_item("only", score=1)]},
            raw_only=True,
            quality_gate_enabled=True,
            tldr_min_quality_score=5,
            tldr_min_items=1,
        )

    with pytest.raises(formatter.FormatterQualityError, match="undisclosed"):
        formatter.format_daily_digest(
            {"Source": [_item("unknown", score=8, summary="未披露")]},
            raw_only=True,
            quality_gate_enabled=True,
            tldr_min_quality_score=1,
            tldr_min_items=1,
            tldr_max_undisclosed=0,
            tldr_max_undisclosed_ratio=0,
            tldr_min_hard_signal_ratio=0,
        )


def test_hard_signal_ratio_and_optional_sections_are_consumed() -> None:
    weak = _item("weak", score=8, summary="generic prose")
    weak["hard_signal"] = False
    with pytest.raises(formatter.FormatterQualityError, match="hard-signal"):
        formatter.format_daily_digest(
            {"Source": [weak]},
            raw_only=True,
            quality_gate_enabled=True,
            tldr_min_quality_score=1,
            tldr_min_items=1,
            tldr_min_hard_signal_ratio=1,
        )

    _, with_sections = formatter.format_daily_digest(
        {"Source": [_item("one")]},
        raw_only=True,
        quality_gate_enabled=False,
        include_mindmap=True,
        include_cognitive_lenses=True,
        cognitive_questions=["这对工作流意味着什么？"],
    )
    _, without_sections = formatter.format_daily_digest(
        {"Source": [_item("one")]},
        raw_only=True,
        quality_gate_enabled=False,
        include_mindmap=False,
        include_cognitive_lenses=False,
    )

    assert "## 思维导图" in with_sections
    assert "这对工作流意味着什么？" in with_sections
    assert "## 思维导图" not in without_sections
    assert "这对工作流意味着什么？" not in without_sections
