from pathlib import Path

import pytest

import daily_curation
import state_schema


def test_plan_daily_digest_rejects_underfilled_top_stories_after_deduplication():
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

    with pytest.raises(daily_curation.CurationPolicyError, match="requires 3 stories"):
        daily_curation.plan_daily_digest(
            items,
            today="2026-04-17",
            used_urls={"https://example.com/dup"},
            rotation_state={"sources": {}, "weekly_summary": {"3blue1brown_used_this_week": False}},
        )


def test_plan_daily_digest_does_not_publish_a_single_paper_as_a_complete_digest():
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

    with pytest.raises(daily_curation.CurationPolicyError, match="requires 3 stories"):
        daily_curation.plan_daily_digest(items, today="2026-04-17")


def test_persist_daily_digest_selection_cannot_mutate_legacy_state_files(tmp_path: Path):
    used_articles = tmp_path / "used_articles.json"
    source_rotation = tmp_path / "source_rotation.json"

    plan = daily_curation.DailyDigestPlan(
        date="2026-04-17",
        selected_links=["https://example.com/a", "https://youtube.com/watch?v=3b1b"],
        selected_sources=["OpenAI News", "3Blue1Brown"],
        used_three_blue_one_brown=True,
    )

    with pytest.raises(state_schema.LegacyStateImportError, match="read-only"):
        daily_curation.persist_daily_digest_selection(
            plan,
            used_articles_path=used_articles,
            source_rotation_path=source_rotation,
            retention_days=30,
        )

    assert not used_articles.exists()
    assert not source_rotation.exists()
