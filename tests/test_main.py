from datetime import datetime, timedelta

import main
from config_schema import PKMConfig, RssFeed, YouTubeChannel


def test_dedupe_items_removes_duplicate_guid_and_link():
    items = [
        {"guid": "a", "link": "https://a", "title": "A"},
        {"guid": "a", "link": "https://a-dup", "title": "A2"},
        {"guid": "", "link": "https://b", "title": "B"},
        {"guid": "", "link": "https://b", "title": "B2"},
        {"guid": "", "link": "", "title": "C", "summary": "same"},
        {"guid": "", "link": "", "title": "C", "summary": "same"},
    ]

    deduped = main._dedupe_items(items)

    assert len(deduped) == 3
    assert deduped[0]["title"] == "A"
    assert deduped[1]["title"] == "B"
    assert deduped[2]["title"] == "C"


def test_archive_old_raw_feeds_moves_files_older_than_keep_days(tmp_path):
    vault = tmp_path / "vault"
    raw_dir = vault / "00-Inbox" / "Raw-Feeds"
    raw_dir.mkdir(parents=True, exist_ok=True)

    old_day = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    fresh_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    old_file = raw_dir / f"Raw-Daily-Feeds-{old_day}.md"
    fresh_file = raw_dir / f"Raw-Daily-Feeds-{fresh_day}.md"
    old_file.write_text("old", encoding="utf-8")
    fresh_file.write_text("new", encoding="utf-8")

    archived = main._archive_old_raw_feeds(str(vault), keep_days=7)

    assert archived == 1
    assert not old_file.exists()
    assert fresh_file.exists()
    assert (raw_dir / "archive" / old_file.name).exists()


def test_run_doctor_passes_without_network_when_config_and_vault_are_valid(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)

    config = PKMConfig(
        rss_feeds=[
            RssFeed(name="RSS", url="https://example.com/feed.xml", note_folder="30-Daily/AI-News"),
        ],
        youtube_channels=[
            YouTubeChannel(
                name="YT", channel_id="UC1234567890123456789012", note_folder="20-Sources/Videos"
            ),
        ],
    )

    monkeypatch.setattr(main, "VAULT_PATH", str(vault))
    monkeypatch.setattr(main, "CONFIG", config)

    assert main.run_doctor(check_network=False) is True


def test_run_doctor_fails_when_vault_path_missing(monkeypatch):
    monkeypatch.setattr(main, "VAULT_PATH", "YOUR_VAULT_PATH")
    config = PKMConfig(
        rss_feeds=[RssFeed(name="RSS", url="https://example.com/feed.xml", note_folder="30-Daily")],
        youtube_channels=[],
    )
    monkeypatch.setattr(main, "CONFIG", config)

    assert main.run_doctor(check_network=False) is False


def test_load_feed_cache_accepts_utf8_bom(monkeypatch, tmp_path):
    cache_file = tmp_path / "feed_cache.json"
    cache_file.write_text('\ufeff{"a":"2099-01-01"}', encoding="utf-8")

    monkeypatch.setattr(main, "CACHE_PATH", cache_file)
    monkeypatch.setattr(main, "CACHE_EXPIRY_DAYS", 7)

    assert main.load_feed_cache() == {"a": "2099-01-01"}


def test_run_daily_fetch_daily_only_outputs_single_digest_tmp_path(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)

    config = PKMConfig(
        rss_feeds=[
            RssFeed(
                name="AI News",
                url="https://example.com/news.xml",
                note_folder="30-Daily/AI-News",
                domain="AI-News",
            ),
            RssFeed(
                name="arXiv",
                url="https://arxiv.org/rss/cs.AI",
                note_folder="20-Sources/Papers",
                domain="Research",
            ),
        ],
        youtube_channels=[
            YouTubeChannel(
                name="YT",
                channel_id="UC1234567890123456789012",
                note_folder="20-Sources/Videos",
                domain="AI-Stack",
            ),
        ],
    )

    monkeypatch.setattr(main, "CONFIG", config)
    monkeypatch.setattr(main, "VAULT_PATH", str(vault))
    monkeypatch.setattr(main, "WRITE_MODE", "disk")
    monkeypatch.setattr(main, "DAILY_DIGEST_ONLY_OUTPUT", True)
    monkeypatch.setattr(main, "MAX_PAPERS", 10)
    monkeypatch.setattr(main, "MAX_VIDEOS", 3)
    monkeypatch.setattr(main, "MAX_PAPER_NOTES_PER_DAY", 4)
    monkeypatch.setattr(main, "MAX_VIDEO_NOTES_PER_DAY", 3)
    monkeypatch.setattr(main, "DAILY_DIGEST_TOP_PICKS", 5)
    monkeypatch.setattr(main, "DAILY_DIGEST_MAX_ITEMS_PER_SOURCE", 2)
    monkeypatch.setattr(main, "DAILY_DIGEST_ACTION_ITEMS", 3)
    monkeypatch.setattr(main, "DAILY_DIGEST_MAX_DEFERRED_ITEMS", 6)
    monkeypatch.setattr(main, "DAILY_DIGEST_INCLUDE_MINDMAP", True)
    monkeypatch.setattr(
        main,
        "QUALITY_CONFIG",
        {
            "max_ai_items_per_feed": 8,
            "min_ai_interest_score": 2,
            "ai_interest_topics": ["workflow", "eval"],
            "ai_priority_topics": ["agent engineering"],
            "ai_exclude_keywords": [],
        },
    )

    monkeypatch.setattr(main, "load_feed_cache", lambda: {})
    monkeypatch.setattr(main, "save_feed_cache", lambda _cache: None)
    monkeypatch.setattr(main, "_refresh_source_rotation_week", lambda _path: None)
    monkeypatch.setattr(main, "_compact_used_articles", lambda _path, _retention: None)
    monkeypatch.setattr(main, "_save_source_health", lambda _report: None)
    monkeypatch.setattr(main, "_archive_old_raw_feeds", lambda _vault, keep_days=7: 0)
    monkeypatch.setattr(main.daily_curation, "load_used_urls", lambda _path: set())
    monkeypatch.setattr(
        main.daily_curation, "load_rotation_state", lambda _path: {"sources": {}, "weekly_summary": {}}
    )
    monkeypatch.setattr(
        main.daily_curation,
        "persist_daily_digest_selection",
        lambda *args, **kwargs: None,
    )

    def fake_fetch_rss(feed_config, *_args, **_kwargs):
        if "arxiv" in feed_config["url"]:
            return [
                {
                    "title": "Paper A",
                    "link": "https://arxiv.org/abs/1234.5678",
                    "guid": "paper-guid-1",
                    "summary": "Paper summary",
                    "content_type": "paper",
                }
            ]
        return [
            {
                "title": "Agent eval workflow",
                "link": "https://example.com/agent-eval",
                "guid": "news-guid-1",
                "summary": "Practical guide",
                "score": 10,
                "score_reasons": ["priority:agent engineering"],
                "ai_bucket": "frontier",
                "content_type": "news",
            }
        ]

    monkeypatch.setattr(main.fetcher, "fetch_rss_feed", fake_fetch_rss)
    monkeypatch.setattr(
        main.fetcher,
        "fetch_youtube_channel",
        lambda *_args, **_kwargs: [
            {
                "title": "Video A",
                "link": "https://youtube.com/watch?v=abc",
                "guid": "video-guid-1",
                "published": "2026-04-08",
                "summary": "Video summary",
                "channel_name": "YT",
                "domain": "AI-Stack",
                "folder": "20-Sources/Videos",
                "content_type": "video",
            }
        ],
    )

    written_paths: list[str] = []

    def fake_write(path: str, content: str, dry_run: bool = False) -> bool:
        assert content
        if not dry_run:
            written_paths.append(path)
        return True

    monkeypatch.setattr(main, "_write", fake_write)

    report = main.run_daily_fetch(test_mode=False, raw_only=False, dry_run=False)

    assert report["writes_ok"] == 1
    assert len(written_paths) == 1
    assert written_paths[0].startswith("30-Daily/AI-News/AI-Daily-")
    assert not any(path.startswith("20-Sources/Papers/") for path in written_paths)
    assert not any(path.startswith("20-Sources/Videos/") for path in written_paths)


def test_collect_items_raw_only_merges_rss_and_youtube(monkeypatch):
    config = PKMConfig(
        rss_feeds=[
            RssFeed(name="News", url="https://example.com/feed.xml", note_folder="30-Daily/AI-News"),
            RssFeed(
                name="Papers",
                url="https://arxiv.org/rss/cs.AI",
                note_folder="20-Sources/Papers",
                content_type="paper",
            ),
        ],
        youtube_channels=[
            YouTubeChannel(
                name="3Blue1Brown",
                channel_id="UCYO_jab_esuFRV4b17AJtAw",
                note_folder="20-Sources/Videos",
            )
        ],
    )
    monkeypatch.setattr(main, "CONFIG", config)
    monkeypatch.setattr(main, "MAX_PAPERS", 10)
    monkeypatch.setattr(main, "QUALITY_CONFIG", {})
    monkeypatch.setattr(
        main.fetcher,
        "fetch_rss_feed",
        lambda feed_config, *_args, **_kwargs: (
            [
                {
                    "title": feed_config["name"],
                    "link": f"https://example.com/{feed_config['name']}",
                    "guid": feed_config["name"],
                    "summary": "summary",
                    "content_type": "paper" if "arxiv" in feed_config["url"] else "news",
                }
            ],
            {"status": "ok", "detail": ""},
        ),
    )
    monkeypatch.setattr(
        main.fetcher,
        "fetch_youtube_channel_raw",
        lambda *_args, **_kwargs: [{"title": "Video", "link": "https://youtube.com/watch?v=abc"}],
    )

    report = main._build_run_report(test_mode=False, raw_only=True, dry_run=False)
    collection = main._collect_items(report=report, feed_cache={}, today="2026-04-21", raw_only=True)

    assert set(collection["news_items"]) == {"News", "Papers", "YouTube"}
    assert collection["paper_candidates"] == []
    assert collection["video_candidates"] == []


def test_flush_digest_raw_only_uses_single_render_path(monkeypatch):
    report = main._build_run_report(test_mode=False, raw_only=True, dry_run=False)
    collection = {
        "news_items": {
            "OpenAI News": [
                {"title": "A", "link": "https://example.com/a", "guid": "a", "summary": "summary"}
            ]
        },
        "paper_candidates": [],
        "video_candidates": [],
    }
    written: list[tuple[str, str, bool]] = []

    monkeypatch.setattr(
        main.formatter,
        "format_daily_digest",
        lambda *_args, **_kwargs: ("00-Inbox/Raw-Feeds/Raw-Daily-Feeds-2026-04-21.md", "raw"),
    )
    monkeypatch.setattr(
        main,
        "_write",
        lambda path, content, dry_run=False: written.append((path, content, dry_run)) or True,
    )
    monkeypatch.setattr(main, "_archive_old_raw_feeds", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(main, "VAULT_PATH", "D:/vault")
    monkeypatch.setattr(main, "RAW_FEED_KEEP_DAYS", 7)

    main._flush_digest(
        report=report,
        collection=collection,
        feed_cache={},
        today="2026-04-21",
        raw_only=True,
        test_mode=False,
        dry_run=False,
        used_urls=set(),
        rotation_state={},
    )

    assert written == [("00-Inbox/Raw-Feeds/Raw-Daily-Feeds-2026-04-21.md", "raw", False)]
    assert report["writes_ok"] == 1
    assert report["archived_raw_files"] == 2
