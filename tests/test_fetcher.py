import time
from types import SimpleNamespace

import fetcher


def _feed(entries):
    return SimpleNamespace(entries=entries)


def test_ai_filter_matches_default_keywords():
    assert fetcher.ai_filter("New GPT model release")
    assert not fetcher.ai_filter("garden tips for spring")


def test_fetch_rss_feed_respects_cache_and_custom_keywords(monkeypatch):
    feed_config = {
        "name": "HN",
        "url": "https://hnrss.org/best",
        "note_folder": "30-Daily/AI-News",
        "filter_keywords": ["gpt"],
    }
    feed_cache = {"seen-guid": "2026-03-25"}
    entries = [
        {"title": "GPT cached item", "link": "https://a", "id": "seen-guid", "summary": "cached"},
        {"title": "Database update", "link": "https://b", "id": "no-ai", "summary": "unrelated"},
        {
            "title": "Tooling",
            "link": "https://c",
            "id": "fresh-guid",
            "summary": "Great GPT workflow",
        },
    ]
    monkeypatch.setattr(fetcher, "_parse_feed", lambda url: _feed(entries))

    items = fetcher.fetch_rss_feed(feed_config, feed_cache, "2026-03-26")

    assert len(items) == 1
    assert items[0]["guid"] == "fresh-guid"
    assert "fresh-guid" in feed_cache


def test_fetch_rss_feed_raw_mode_ignores_cache(monkeypatch):
    feed_config = {
        "name": "Raw Mode Feed",
        "url": "https://example.com/feed.xml",
        "note_folder": "00-Inbox",
    }
    feed_cache = {"guid-1": "2026-03-25"}
    monkeypatch.setattr(
        fetcher,
        "_parse_feed",
        lambda url: _feed(
            [{"title": "Same item", "link": "https://x", "id": "guid-1", "summary": "text"}]
        ),
    )

    items = fetcher.fetch_rss_feed(feed_config, feed_cache, "2026-03-26", raw_only=True)

    assert len(items) == 1
    assert items[0]["guid"] == "guid-1"
    assert feed_cache["guid-1"] == "2026-03-25"


def test_fetch_rss_feed_returns_empty_when_parser_fails(monkeypatch):
    feed_config = {
        "name": "Broken Feed",
        "url": "https://broken-feed.example",
        "note_folder": "30-Daily/AI-News",
    }
    monkeypatch.setattr(
        fetcher, "_parse_feed", lambda url: (_ for _ in ()).throw(RuntimeError("network down"))
    )

    items = fetcher.fetch_rss_feed(feed_config, {}, "2026-03-26")

    assert items == []


def test_fetch_rss_feed_applies_content_type_from_config(monkeypatch):
    feed_config = {
        "name": "OpenAI X",
        "url": "https://rsshub.app/twitter/user/OpenAI",
        "note_folder": "30-Daily/AI-News",
        "content_type": "tweet",
    }
    entries = [
        {
            "title": "Agent eval thread",
            "link": "https://x.com/openai/status/1",
            "id": "tweet-1",
            "summary": "Thread summary",
        }
    ]
    monkeypatch.setattr(fetcher, "_parse_feed", lambda url: _feed(entries))

    items = fetcher.fetch_rss_feed(feed_config, {}, "2026-04-08", raw_only=True)

    assert len(items) == 1
    assert items[0]["content_type"] == "tweet"


def test_fetch_youtube_channel_skips_cached_and_cleans_html(monkeypatch):
    channel = {
        "name": "Test Channel",
        "channel_id": "abc123",
        "note_folder": "20-Sources/Videos",
        "domain": "AI-Stack",
    }
    feed_cache = {"old-guid": "2026-03-25"}
    entries = [
        {"title": "Old", "link": "https://yt/1", "id": "old-guid", "summary": "<p>old</p>"},
        {
            "title": "New",
            "link": "https://yt/2",
            "id": "new-guid",
            "summary": "<p>fresh <b>video</b></p>",
        },
    ]
    monkeypatch.setattr(fetcher, "_parse_youtube_feed", lambda cid: _feed(entries))

    videos = fetcher.fetch_youtube_channel(channel, feed_cache, "2026-03-26", max_videos=3)

    assert len(videos) == 1
    assert videos[0]["guid"] == "new-guid"
    assert videos[0]["summary"] == "fresh video"
    assert videos[0]["content_type"] == "video"
    assert feed_cache["new-guid"] == "2026-03-26"


def test_fetch_youtube_channel_raw_returns_empty_when_parser_fails(monkeypatch):
    channel = {"name": "Broken", "channel_id": "id"}
    monkeypatch.setattr(
        fetcher, "_parse_youtube_feed", lambda cid: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    videos = fetcher.fetch_youtube_channel_raw(channel)

    assert videos == []


def test_fetch_rss_feed_ai_scoring_threshold_bucket_and_cap(monkeypatch):
    feed_config = {
        "name": "AI Daily",
        "url": "https://example.com/ai.xml",
        "note_folder": "30-Daily/AI-News",
        "domain": "ai-news",
    }
    entries = [
        {
            "title": "Context Engineering playbook for coding agent eval",
            "link": "https://a.example/post",
            "id": "g1",
            "summary": "Implementation guide and benchmark notes.",
        },
        {
            "title": "Simple funding round update",
            "link": "https://b.example/post",
            "id": "g2",
            "summary": "press release only",
        },
        {
            "title": "Tool calling production checklist",
            "link": "https://c.example/post",
            "id": "g3",
            "summary": "workflow guide",
        },
    ]
    monkeypatch.setattr(fetcher, "_parse_feed", lambda url: _feed(entries))

    quality = {
        "min_ai_interest_score": 7,
        "max_ai_items_per_feed": 1,
        "ai_interest_topics": ["workflow", "guide", "eval"],
        "ai_priority_topics": ["context engineering", "tool calling", "coding agent"],
        "ai_exclude_keywords": ["funding round", "press release"],
    }
    items = fetcher.fetch_rss_feed(
        feed_config, {}, "2026-04-07", raw_only=True, quality_config=quality
    )

    assert len(items) == 1
    assert items[0]["guid"] in {"g1", "g3"}
    assert items[0]["score"] >= 7
    assert "score_reasons" in items[0]
    assert items[0]["ai_bucket"] in {
        "frontier",
        "practice",
        "tooling",
    }


def test_score_ai_interest_includes_show_hn_and_exclude_penalty():
    score, reasons = fetcher._score_ai_interest(
        title="Show HN: agent workflow tutorial",
        summary="practical guide for evaluation",
        source_name="HackerNews Best",
        ai_interest_topics=["workflow", "evaluation"],
        ai_priority_topics=["agent engineering"],
        ai_exclude_keywords=["coupon"],
    )

    assert score >= 4
    assert "base-ai" in reasons
    assert "show-hn" in reasons


def test_infer_content_type_prefers_hard_signal_over_default_news():
    content_type = fetcher.infer_content_type(
        source_name="arXiv cs.AI",
        url="https://arxiv.org/rss/cs.AI",
        domain="research",
        fallback="news",
    )
    assert content_type == "paper"


def test_fetch_rss_feed_includes_published_date(monkeypatch):
    feed_config = {
        "name": "OpenAI News",
        "url": "https://openai.com/news/rss.xml",
        "note_folder": "30-Daily/AI-News",
        "domain": "ai-news",
    }
    entries = [
        {
            "title": "Post",
            "link": "https://openai.com/post",
            "id": "post-1",
            "summary": "agent workflow",
            "published_parsed": time.strptime("2026-04-09", "%Y-%m-%d"),
        }
    ]
    monkeypatch.setattr(fetcher, "_parse_feed", lambda url: _feed(entries))

    items = fetcher.fetch_rss_feed(feed_config, {}, "2026-04-09", raw_only=True)

    assert len(items) == 1
    assert items[0]["published"] == "2026-04-09"
