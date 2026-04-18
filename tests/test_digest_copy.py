import digest_copy
from daily_curation import DailyDigestPlan


def test_build_digest_copy_produces_stable_sections_without_llm():
    plan = DailyDigestPlan(
        date="2026-04-18",
        top_stories=[
            (
                "OpenAI News",
                {
                    "title": "Codex for (almost) everything",
                    "summary": "Computer use and evaluation workflow with pass@1=43.2%.",
                    "link": "https://example.com/codex",
                    "content_type": "news",
                    "domain": "AI-News",
                },
            )
        ],
        growth_story=(
            "Latent Space",
            {
                "title": "Harness engineering for coding agents",
                "summary": "Workflow playbook for agent evaluation loops.",
                "link": "https://example.com/harness",
                "content_type": "news",
                "domain": "AI-News",
            },
        ),
    )

    payload = digest_copy.build_digest_copy(plan)

    assert payload["top_stories"][0]["headline_cn"]
    assert payload["top_stories"][0]["core_concepts"]
    assert "43.2%" in " ".join(payload["top_stories"][0]["key_details"])
    assert payload["insight_story"]["one_line_summary"]


def test_merge_digest_copy_keeps_base_when_override_is_partial():
    base = {
        "top_stories": [
            {
                "headline_cn": "Base headline",
                "core_concepts": ["#concept/A", "#concept/B"],
                "core_finding": "Base finding",
                "key_details": ["Base detail 1", "Base detail 2"],
                "actionable_insight": "Base action",
            }
        ],
        "venture_story": None,
        "insight_story": None,
        "video_story": None,
        "ai_company_story": None,
    }
    override = {
        "top_stories": [
            {
                "headline_cn": "Override headline",
                "core_concepts": [],
                "core_finding": "",
                "key_details": ["Override detail"],
                "actionable_insight": "",
            }
        ]
    }

    merged = digest_copy.merge_digest_copy(base, override)

    assert merged["top_stories"][0]["headline_cn"] == "Override headline"
    assert merged["top_stories"][0]["core_finding"] == "Base finding"
    assert merged["top_stories"][0]["key_details"] == ["Override detail"]
