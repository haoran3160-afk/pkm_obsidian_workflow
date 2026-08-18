from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pkm_workflow.source_import import build_source_baseline, write_source_baseline


def test_import_keeps_duplicates_disabled_rows_and_nested_ielts_records(tmp_path: Path) -> None:
    shared = {"name": "same", "url": "https://example.com/feed", "enabled": True}
    private = {
        "rss_feeds": [shared, shared, {**shared, "enabled": False}],
        "youtube_channels": [{"name": "channel", "channel_id": "UC123"}],
        "ielts_resources": {
            "practice_websites": ["https://example.com/ielts"],
            "books": ["Book title"],
        },
    }
    public = {"rss_feeds": [shared], "youtube_channels": []}
    documents = (
        ("private", private, hashlib.sha256(b"private").hexdigest()),
        ("public", public, hashlib.sha256(b"public").hexdigest()),
    )

    baseline = build_source_baseline(documents)

    assert len(baseline.records) == 7
    assert len({record.source_id for record in baseline.records}) == 7
    assert sum(record.collection == "rss_feeds" for record in baseline.records) == 4
    assert sum(not record.enabled for record in baseline.records) == 1
    assert all(record.lane is None for record in baseline.records)
    assert all(
        record.approval_state == "UNCLASSIFIED_REQUIRES_APPROVAL" for record in baseline.records
    )

    target = tmp_path / "source-baseline.json"
    written = write_source_baseline(target, baseline)
    before = written.read_bytes()
    with pytest.raises(FileExistsError):
        write_source_baseline(target, baseline)
    assert written.read_bytes() == before
    assert json.loads(before)["baseline_id"] == baseline.baseline_id


def test_import_rejects_missing_source_endpoint_instead_of_silently_dropping() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        build_source_baseline((("private", {"rss_feeds": [{"name": "broken"}]}, "a" * 64),))


def test_import_rejects_missing_duplicate_or_unverifiable_documents() -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_source_baseline(())

    with pytest.raises(ValueError, match="origins must be unique"):
        build_source_baseline(
            (
                ("private", {}, "a" * 64),
                ("private", {}, "b" * 64),
            )
        )

    for origin, document_hash in (("", "a" * 64), ("private", "A" * 64)):
        with pytest.raises(ValueError, match="origin and source document SHA-256"):
            build_source_baseline(((origin, {}, document_hash),))


@pytest.mark.parametrize(
    "document",
    (
        {"rss_feeds": "not-a-list"},
        {"youtube_channels": "not-a-list"},
        {"rss_feeds": ["not-an-object"]},
        {"youtube_channels": ["not-an-object"]},
        {"youtube_channels": [{"name": "missing endpoint"}]},
        {"ielts_resources": []},
        {"ielts_resources": {"books": "not-a-list"}},
        {"ielts_resources": {"books": [{"name": "missing endpoint"}]}},
    ),
)
def test_import_rejects_malformed_collections_without_dropping_rows(
    document: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        build_source_baseline((("private", document, "a" * 64),))


def test_import_preserves_youtube_url_fallback_and_every_ielts_adapter() -> None:
    document = {
        "youtube_channels": [
            {
                "name": "URL-only channel",
                "url": "https://www.youtube.com/@example",
                "enabled": False,
            }
        ],
        "ielts_resources": {
            "youtube_channels": ["https://www.youtube.com/watch?v=example"],
            "books": ["Book title"],
            "practice_websites": ["https://example.com/practice"],
        },
    }

    baseline = build_source_baseline((("private", document, "a" * 64),))

    assert [record.adapter for record in baseline.records] == [
        "youtube",
        "youtube-url",
        "book",
        "manual-url",
    ]
    assert baseline.records[0].endpoint == "https://www.youtube.com/@example"
    assert baseline.records[0].enabled is False


def test_import_derives_official_atom_feed_for_youtube_channel_id() -> None:
    baseline = build_source_baseline(
        (
            (
                "private",
                {"youtube_channels": [{"name": "Example", "channel_id": "UC123"}]},
                "a" * 64,
            ),
        )
    )

    assert baseline.records[0].adapter == "youtube"
    assert baseline.records[0].endpoint == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
    )


def test_import_accepts_explicitly_empty_or_null_ielts_without_inventing_rows() -> None:
    for ielts_resources in ({}, None):
        baseline = build_source_baseline(
            (("private", {"ielts_resources": ielts_resources}, "a" * 64),)
        )
        assert baseline.records == ()
