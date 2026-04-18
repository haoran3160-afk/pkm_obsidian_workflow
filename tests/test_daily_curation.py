from pathlib import Path

import daily_curation


def test_plan_daily_digest_respects_recently_used_urls_and_weekly_video_rule():
    items = {
        "OpenAI News": [
            {
                "title": "Fresh eval post",
                "link": "https://example.com/fresh",
                "summary": "offline and online eval with pass@1 41.2%",
                "score": 10,
                "content_type": "news",
                "domain": "AI-News",
                "published": "2026-04-17",
            },
            {
                "title": "Duplicate post",
                "link": "https://example.com/dup",
                "summary": "old update",
                "score": 12,
                "content_type": "news",
                "domain": "AI-News",
                "published": "2026-04-17",
            },
        ],
        "3Blue1Brown": [
            {
                "title": "Visual math for models",
                "link": "https://youtube.com/watch?v=3b1b",
                "summary": "complex analysis for infinite recursion",
                "score": 8,
                "content_type": "video",
                "domain": "Math-Modeling",
                "published": "2026-04-17",
            }
        ],
    }

    plan = daily_curation.plan_daily_digest(
        items,
        today="2026-04-17",
        used_urls={"https://example.com/dup"},
        rotation_state={"sources": {}, "weekly_summary": {"3blue1brown_used_this_week": False}},
    )

    assert any(item["title"] == "Fresh eval post" for _, item in plan.top_stories)
    assert all(item["title"] != "Duplicate post" for _, item in plan.top_stories)
    assert plan.video_story is not None
    assert plan.video_story[0] == "3Blue1Brown"
    assert plan.used_three_blue_one_brown is True


def test_plan_daily_digest_uses_paper_as_insight_fallback():
    items = {
        "arXiv cs.AI": [
            {
                "title": "The Non-Optimality of Scientific Knowledge",
                "link": "https://arxiv.org/abs/2604.11828",
                "summary": "Path dependence and lock-in in science.",
                "score": 7,
                "content_type": "paper",
                "domain": "Research",
                "published": "2026-04-17",
            }
        ]
    }

    plan = daily_curation.plan_daily_digest(items, today="2026-04-17")

    assert plan.growth_story is not None
    assert plan.growth_story[0] == "arXiv cs.AI"


def test_persist_daily_digest_selection_updates_used_articles_and_rotation(tmp_path: Path):
    used_articles = tmp_path / "used_articles.json"
    source_rotation = tmp_path / "source_rotation.json"

    plan = daily_curation.DailyDigestPlan(
        date="2026-04-17",
        selected_links=["https://example.com/a", "https://youtube.com/watch?v=3b1b"],
        selected_sources=["OpenAI News", "3Blue1Brown"],
        used_three_blue_one_brown=True,
    )

    daily_curation.persist_daily_digest_selection(
        plan,
        used_articles_path=used_articles,
        source_rotation_path=source_rotation,
        retention_days=30,
    )

    assert used_articles.exists()
    assert source_rotation.exists()
    assert "https://example.com/a" in used_articles.read_text(encoding="utf-8")
    rotation_text = source_rotation.read_text(encoding="utf-8")
    assert "OpenAI News" in rotation_text
    assert '"3blue1brown_used_this_week": true' in rotation_text.lower()
