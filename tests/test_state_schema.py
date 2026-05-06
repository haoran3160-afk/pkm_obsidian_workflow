from pathlib import Path

import state_schema


def test_load_used_articles_state_accepts_bom(tmp_path: Path):
    path = tmp_path / "used_articles.json"
    path.write_text(
        '\ufeff{"articles":[{"date":"2026-04-20","url":"https://example.com/a"}]}',
        encoding="utf-8",
    )

    state = state_schema.load_used_articles_state(path)

    assert len(state.articles) == 1
    assert state.articles[0].url == "https://example.com/a"


def test_load_state_returns_safe_defaults_for_invalid_payload(tmp_path: Path):
    path = tmp_path / "source_rotation.json"
    path.write_text('{"weekly_summary":{"week_start":"bad-date"}}', encoding="utf-8")

    state = state_schema.load_source_rotation_state(path)

    assert state.sources == {}
    assert state.weekly_summary.week_start == ""


def test_compact_used_articles_state_dedupes_and_prunes(tmp_path: Path, monkeypatch):
    path = tmp_path / "used_articles.json"
    payload = {
        "articles": [
            {"date": "2026-04-01", "url": "https://example.com/old"},
            {"date": "2026-04-20", "url": "https://example.com/a"},
            {"date": "2026-04-21", "url": "https://example.com/a"},
        ]
    }
    state_schema.write_json_file(path, payload)

    class FrozenDatetime(state_schema.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return cls(2026, 4, 21)

    monkeypatch.setattr(state_schema, "datetime", FrozenDatetime)
    compacted = state_schema.compact_used_articles_state(path, retention_days=7)

    assert len(compacted.articles) == 1
    assert compacted.articles[0].date == "2026-04-21"


def test_refresh_source_rotation_week_resets_weekly_flag(tmp_path: Path):
    path = tmp_path / "source_rotation.json"
    state_schema.write_json_file(
        path,
        {
            "sources": {"OpenAI News": {"category": "news", "last_used": "2026-04-10"}},
            "weekly_summary": {
                "week_start": "2026-04-07",
                "3blue1brown_used_this_week": True,
            },
        },
    )

    refreshed = state_schema.refresh_source_rotation_week(path, "2026-04-21")

    assert refreshed.weekly_summary.week_start == "2026-04-20"
    assert refreshed.weekly_summary.threeblue1brown_used_this_week is False


def test_append_source_health_run_keeps_recent_runs(tmp_path: Path):
    path = tmp_path / "source_health.json"

    state_schema.append_source_health_run(
        path,
        run_date="2026-04-20",
        run_at="2026-04-20T07:00:00",
        entries=[
            {
                "timestamp": "2026-04-20T07:00:00",
                "source": "OpenAI News",
                "kind": "rss",
                "status": "ok",
                "item_count": 3,
                "detail": "",
            }
        ],
        keep_runs=2,
    )
    latest = state_schema.append_source_health_run(
        path,
        run_date="2026-04-21",
        run_at="2026-04-21T07:00:00",
        entries=[],
        keep_runs=1,
    )

    assert len(latest.runs) == 1
    assert latest.runs[0].run_date == "2026-04-21"
