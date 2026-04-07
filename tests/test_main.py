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
