import formatter


def test_format_daily_digest_raw_includes_bucket_sections_and_scores():
    items = {
        "LangChain Blog": [
            {
                "title": "Context engineering guide",
                "link": "https://example.com/a",
                "summary": "Deep implementation notes.",
                "score": 10,
                "score_reasons": ["priority:context engineering"],
                "ai_bucket": "\u524d\u6cbf\u6280\u5de7",
            }
        ],
        "General Feed": [
            {"title": "Unscored item", "link": "https://example.com/b", "summary": "note"}
        ],
    }

    path, content = formatter.format_daily_digest(items, raw_only=True)

    assert path.startswith("00-Inbox/Raw-Feeds/Raw-Daily-Feeds-")
    assert "## AI-News Curated Buckets" in content
    assert "### \u524d\u6cbf\u6280\u5de7" in content
    assert "**InterestScore**: 10" in content
    assert "## Source Buckets (Full Raw by Feed)" in content


def test_format_daily_digest_final_mode_includes_frontmatter_and_links():
    items = {
        "OpenAI News": [
            {
                "title": "How we monitor internal coding agents",
                "link": "https://example.com/c",
                "summary": "A practical post.",
                "score": 8,
                "ai_bucket": "\u5de5\u7a0b\u5b9e\u8df5",
            }
        ]
    }

    path, content = formatter.format_daily_digest(items, raw_only=False)

    assert path.startswith("30-Daily/AI-News/AI-Daily-")
    assert "type: daily-digest" in content
    assert "## AI-News Curated Buckets" in content
    assert "- **[How we monitor internal coding agents](https://example.com/c)**" in content
    assert "## Source Buckets (All)" in content


def test_format_note_templates_cover_paper_video_and_ielts():
    paper_path, paper_content = formatter.format_paper_note(
        {"title": "Paper: LLM Eval", "link": "https://arxiv.org/abs/1234", "summary": "summary"},
        source_name="arXiv",
    )
    assert paper_path.startswith("20-Sources/Papers/")
    assert "Paper: LLM Eval" in paper_content

    video_path, video_content = formatter.format_video_note(
        {
            "channel_name": "Andrej Karpathy",
            "title": "How I use LLMs",
            "published": "2026-04-07",
            "folder": "20-Sources/Videos",
            "domain": "AI-Stack",
            "link": "https://youtube.com/watch?v=abc",
            "summary": "video summary",
        }
    )
    assert video_path.startswith("20-Sources/Videos/2026-04-07-Andrej-Karpathy-")
    assert "How I use LLMs" in video_content

    ielts_path, ielts_content = formatter.format_ielts_reminder()
    assert ielts_path.startswith("10-Notes/IELTS/IELTS-Study-")
    assert "IELTS" in ielts_content


def test_build_note_includes_entity_type_and_sources():
    note = formatter.build_note(
        title="Agent Notes",
        content="Implementation details.",
        tags=["ai", "workflow"],
        domain="AI-Stack",
        source="manual",
        related=["Existing Note"],
        entity_type="tool",
        source_count=1,
        sources=["https://example.com/ref1", "https://example.com/ref2"],
    )

    assert "entity_type: tool" in note
    assert "source_count: 2" in note
    assert '- "https://example.com/ref1"' in note
    assert '- "https://example.com/ref2"' in note
    assert 'related: ["[[Existing Note]]"]' in note
