from __future__ import annotations

import pytest

import daily_curation
from workflow_contracts import (
    DuplicateKind,
    IntentKind,
    SemanticClusterProposal,
)


def _story(
    title: str,
    *,
    score: int,
    content_type: str,
    domain: str,
) -> dict:
    return {
        "title": title,
        "link": f"https://example.test/{title}",
        "summary": f"{title} evidence 42",
        "score": score,
        "content_type": content_type,
        "domain": domain,
        "published": "2026-07-26",
    }


def _portfolio() -> dict[str, list[dict]]:
    return {
        "Paper A": [_story("paper-a", score=100, content_type="paper", domain="research")],
        "Paper B": [_story("paper-b", score=90, content_type="paper", domain="research")],
        "News A": [_story("news-a", score=80, content_type="news", domain="ai-news")],
        "News B": [_story("news-b", score=70, content_type="news", domain="ai-news")],
        "Engineering": [
            _story(
                "engineering",
                score=60,
                content_type="engineering",
                domain="ai-news",
            )
        ],
    }


def _titles(plan: daily_curation.DailyDigestPlan) -> list[str]:
    return [item["title"] for _, item in plan.top_stories]


def test_top_picks_changes_the_number_of_selected_stories() -> None:
    two = daily_curation.plan_daily_digest(
        _portfolio(),
        today="2026-07-26",
        top_picks=2,
        min_top_nonpaper=1,
        min_top_content_types=1,
        max_paper_in_top=1,
    )
    four = daily_curation.plan_daily_digest(
        _portfolio(),
        today="2026-07-26",
        top_picks=4,
        min_top_nonpaper=2,
        min_top_content_types=2,
        max_paper_in_top=2,
    )

    assert len(two.top_stories) == 2
    assert len(four.top_stories) == 4


def test_paper_limit_and_nonpaper_minimum_change_the_slate() -> None:
    paper_allowed = daily_curation.plan_daily_digest(
        _portfolio(),
        today="2026-07-26",
        top_picks=3,
        min_top_nonpaper=1,
        min_top_content_types=1,
        max_paper_in_top=2,
    )
    nonpaper_only = daily_curation.plan_daily_digest(
        _portfolio(),
        today="2026-07-26",
        top_picks=3,
        min_top_nonpaper=3,
        min_top_content_types=1,
        max_paper_in_top=2,
    )

    assert sum(item["content_type"] == "paper" for _, item in paper_allowed.top_stories) == 2
    assert all(item["content_type"] != "paper" for _, item in nonpaper_only.top_stories)


def test_content_type_minimum_forces_a_diverse_story() -> None:
    one_type = daily_curation.plan_daily_digest(
        _portfolio(),
        today="2026-07-26",
        top_picks=2,
        min_top_nonpaper=2,
        min_top_content_types=1,
        max_paper_in_top=0,
    )
    two_types = daily_curation.plan_daily_digest(
        _portfolio(),
        today="2026-07-26",
        top_picks=2,
        min_top_nonpaper=2,
        min_top_content_types=2,
        max_paper_in_top=0,
    )

    assert _titles(one_type) == ["news-a", "news-b"]
    assert "engineering" in _titles(two_types)


def test_impossible_policy_fails_explicitly() -> None:
    with pytest.raises(daily_curation.CurationPolicyError, match="content types"):
        daily_curation.plan_daily_digest(
            _portfolio(),
            today="2026-07-26",
            top_picks=3,
            min_top_nonpaper=3,
            min_top_content_types=4,
            max_paper_in_top=0,
        )


def test_intent_portfolio_is_stable_and_allows_multi_pool_membership() -> None:
    items = {
        "OpenAI Engineering": [
            {
                "title": "Agents API engineering guide",
                "link": "https://example.test/openai-agents",
                "summary": "Official API workflow and agent evaluation.",
                "content_type": "engineering",
                "domain": "ai-news",
                "published": "2026-07-26",
                "raw_ref": "raw/openai/1",
            }
        ],
        "IELTS Liz": [
            {
                "title": "IELTS speaking practice",
                "link": "https://example.test/ielts-speaking",
                "summary": "English speaking exercise.",
                "content_type": "article",
                "domain": "ielts",
                "published": "2026-07-26",
                "raw_ref": "raw/ielts/1",
            }
        ],
    }

    first = daily_curation.build_intent_portfolio(items)
    second = daily_curation.build_intent_portfolio(items)
    openai_id = first.pools[IntentKind.OFFICIAL_AI.value][0].item_id

    assert first == second
    assert openai_id in {row.item_id for row in first.pools[IntentKind.ENGINEERING.value]}
    assert first.pools[IntentKind.IELTS.value][0].source_name == "IELTS Liz"
    assert set(first.all_item_ids) == {row.item_id for pool in first.pools.values() for row in pool}


def test_explicit_source_intents_only_add_memberships_and_never_drop_items() -> None:
    items = {
        "Hybrid Source": [
            {
                "title": "Research market workflow",
                "link": "https://example.test/hybrid",
                "summary": "A paper about agent market adoption.",
                "content_type": "paper",
                "domain": "research",
                "published": "2026-07-26",
            }
        ]
    }

    baseline = daily_curation.build_intent_portfolio(items)
    expanded = daily_curation.build_intent_portfolio(
        items,
        source_intents={
            "Hybrid Source": [
                IntentKind.OFFICIAL_AI,
                IntentKind.MARKET_GROWTH,
            ]
        },
    )

    assert baseline.all_item_ids == expanded.all_item_ids
    assert len(expanded.pools[IntentKind.OFFICIAL_AI.value]) == 1
    assert len(expanded.pools[IntentKind.MARKET_GROWTH.value]) == 1


def test_ielts_pool_does_not_compete_for_daily_top_slots() -> None:
    items = _portfolio()
    items["IELTS"] = [
        _story(
            "ielts-high-score",
            score=10_000,
            content_type="article",
            domain="ielts",
        )
    ]

    plan = daily_curation.plan_daily_digest(
        items,
        today="2026-07-26",
        top_picks=3,
        min_top_nonpaper=3,
        min_top_content_types=1,
        max_paper_in_top=0,
    )

    assert "ielts-high-score" not in _titles(plan)


def test_python_records_exact_and_content_duplicates_without_dropping_raw_items() -> None:
    items = {
        "Source A": [
            {
                "title": "Same event A",
                "link": "https://example.test/event",
                "summary": "shared evidence",
                "content_type": "news",
                "domain": "ai-news",
                "raw_ref": "raw/a",
            }
        ],
        "Source B": [
            {
                "title": "Same event B",
                "link": "https://example.test/event",
                "summary": "shared evidence",
                "content_type": "news",
                "domain": "ai-news",
                "raw_ref": "raw/b",
            }
        ],
        "Source C": [
            {
                "title": "Same event A",
                "link": "https://example.test/another-url",
                "summary": "shared evidence",
                "content_type": "news",
                "domain": "ai-news",
                "raw_ref": "raw/c",
            }
        ],
    }
    portfolio = daily_curation.build_intent_portfolio(items)

    records = daily_curation.record_python_duplicates(portfolio)

    assert len(portfolio.all_item_ids) == 3
    assert any(
        record.duplicate_kind is DuplicateKind.EXACT
        and set(record.evidence_refs) == {"raw/a", "raw/b"}
        for record in records
    )
    assert any(
        record.duplicate_kind is DuplicateKind.CONTENT and "raw/c" in record.evidence_refs
        for record in records
    )


def test_semantic_cluster_uses_one_slot_and_preserves_all_source_evidence() -> None:
    items = {
        "Source A": [
            {
                "title": "Model launch",
                "link": "https://a.test/model",
                "summary": "The model launched today.",
                "content_type": "news",
                "domain": "ai-news",
                "raw_ref": "raw/a",
            }
        ],
        "Source B": [
            {
                "title": "New model availability",
                "link": "https://b.test/launch",
                "summary": "Availability of the same model.",
                "content_type": "news",
                "domain": "ai-news",
                "raw_ref": "raw/b",
            }
        ],
    }
    portfolio = daily_curation.build_intent_portfolio(items)
    ids = portfolio.all_item_ids
    proposal = SemanticClusterProposal(
        cluster_id="semantic-model-launch",
        member_item_ids=ids,
        representative_item_id=ids[0],
        evidence_refs=["raw/a", "raw/b"],
        rationale="Both reports describe the same launch event.",
        prompt_version="cluster-v1",
        model="codex",
    )

    clusters = daily_curation.validate_semantic_clusters(
        portfolio,
        [proposal],
    )

    assert len(clusters) == 1
    assert clusters[0].representative_item_id == ids[0]
    assert set(clusters[0].evidence_refs) == {"raw/a", "raw/b"}


def test_semantic_cluster_cannot_hide_member_evidence_or_invent_items() -> None:
    portfolio = daily_curation.build_intent_portfolio(
        {
            "Source A": [
                {
                    "title": "A",
                    "link": "https://a.test/a",
                    "summary": "A",
                    "content_type": "news",
                    "domain": "ai-news",
                    "raw_ref": "raw/a",
                }
            ],
            "Source B": [
                {
                    "title": "B",
                    "link": "https://b.test/b",
                    "summary": "B",
                    "content_type": "news",
                    "domain": "ai-news",
                    "raw_ref": "raw/b",
                }
            ],
        }
    )
    ids = portfolio.all_item_ids

    with pytest.raises(daily_curation.CurationPolicyError, match="evidence"):
        daily_curation.validate_semantic_clusters(
            portfolio,
            [
                SemanticClusterProposal(
                    cluster_id="semantic-hidden-evidence",
                    member_item_ids=ids,
                    representative_item_id=ids[0],
                    evidence_refs=["raw/a"],
                    rationale="same event",
                    prompt_version="cluster-v1",
                    model="codex",
                )
            ],
        )
