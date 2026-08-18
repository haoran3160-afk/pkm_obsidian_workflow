from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

import fetcher
from workflow_contracts import FetchReasonCode


@pytest.mark.parametrize(
    ("kwargs", "expected", "retryable"),
    [
        (
            {"exception": requests.exceptions.Timeout("slow")},
            FetchReasonCode.TIMEOUT,
            True,
        ),
        (
            {"exception": requests.exceptions.SSLError("certificate")},
            FetchReasonCode.SSL_ERROR,
            False,
        ),
        (
            {"status_code": 404, "content_type": "application/rss+xml"},
            FetchReasonCode.HTTP_404,
            False,
        ),
        (
            {"status_code": 200, "content_type": "text/html"},
            FetchReasonCode.HTML_NOT_FEED,
            False,
        ),
        (
            {"status_code": 200, "content_type": "application/rss+xml", "bozo": True},
            FetchReasonCode.BOZO,
            True,
        ),
        (
            {"status_code": 200, "content_type": "application/rss+xml"},
            FetchReasonCode.ZERO_YIELD,
            True,
        ),
    ],
)
def test_error_and_zero_yield_reasons_are_distinct(
    kwargs: dict,
    expected: FetchReasonCode,
    retryable: bool,
) -> None:
    diagnostic = fetcher.classify_fetch_result(entries_count=0, **kwargs)

    assert diagnostic.reason_code is expected
    assert diagnostic.retryable is retryable
    assert diagnostic.yield_count == 0


def test_success_has_an_explicit_ok_reason() -> None:
    diagnostic = fetcher.classify_fetch_result(
        status_code=200,
        content_type="application/atom+xml",
        entries_count=3,
    )

    assert diagnostic.reason_code is FetchReasonCode.OK
    assert diagnostic.yield_count == 3


def test_rss_return_meta_exposes_reason_code_without_network(monkeypatch) -> None:
    parsed = SimpleNamespace(entries=[], bozo=False)
    monkeypatch.setattr(
        fetcher,
        "_parse_feed_with_fallback",
        lambda *_args, **_kwargs: (
            parsed,
            "https://example.test/feed",
            {
                "mode": "direct-empty",
                "status": "warn",
                "notes": "",
                "reason_code": FetchReasonCode.ZERO_YIELD.value,
            },
        ),
    )
    feed = {
        "name": "Fixture",
        "url": "https://example.test/feed",
        "note_folder": "00-Inbox",
        "domain": "ai-news",
        "content_type": "news",
        "filter_keywords": [],
    }

    items, meta = fetcher.fetch_rss_feed(
        feed,
        {},
        "2026-07-26",
        quality_config={"enable_fulltext_enrichment": False},
        return_meta=True,
    )

    assert items == []
    assert meta["reason_code"] == FetchReasonCode.ZERO_YIELD.value
