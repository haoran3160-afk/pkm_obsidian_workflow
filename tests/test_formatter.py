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
                "ai_bucket": "frontier",
            }
        ],
        "General Feed": [
            {"title": "Unscored item", "link": "https://example.com/b", "summary": "note"}
        ],
    }

    path, content = formatter.format_daily_digest(items, raw_only=True)

    assert path.startswith("00-Inbox/Raw-Feeds/Raw-Daily-Feeds-")
    assert "## AI 资讯分桶（快速分拣）" in content
    assert "### 前沿技巧" in content
    assert "**兴趣分**: 10" in content
    assert "## 按来源展开（完整原始项）" in content


def test_format_daily_digest_final_mode_includes_frontmatter_and_links():
    items = {
        "OpenAI News": [
            {
                "title": "How we monitor internal coding agents",
                "link": "https://example.com/c",
                "summary": "A practical post.",
                "score": 8,
                "ai_bucket": "practice",
            }
        ]
    }

    path, content = formatter.format_daily_digest(items, raw_only=False)

    assert path.startswith("30-Daily/AI-News/AI-Daily-")
    assert "type: daily-digest" in content
    assert "> [!summary] 60 秒快读" in content
    assert "## 今日 TL;DR（Tier 1）" in content
    assert "## Karpathy 视角：今日认知增量" in content
    assert "> [!tip] 认知评估框架" in content
    assert "## 关键结论（Takeaways）" in content
    assert "## 分栏简报（Tier 2）" in content
    assert "## 可执行清单（Action Queue）" in content
    assert "## 知识图谱" in content
    assert "## 证据来源（Top Sources）" in content
    assert "## 关键词" in content
    assert "## 快速统计" in content
    assert "```mermaid" in content
    assert "## 按来源快扫（高密度）" in content
    assert "How we monitor internal coding agents" in content


def test_format_daily_digest_unified_radar_supports_tweets_and_engineering():
    items = {
        "OpenAI X": [
            {
                "title": "Agent eval thread",
                "link": "https://x.com/openai/status/1",
                "summary": "Tweet summary.",
                "content_type": "tweet",
            }
        ],
        "Engineering Blog": [
            {
                "title": "Production harness playbook",
                "link": "https://example.com/engineering",
                "summary": "How to deploy and evaluate.",
                "content_type": "engineering",
                "score": 9,
                "ai_bucket": "practice",
            }
        ],
    }

    _, content = formatter.format_daily_digest(items, raw_only=False)

    assert "### 推文速览" in content
    assert "Agent eval thread" in content
    assert "### 工程实践" in content
    assert "Production harness playbook" in content


def test_format_daily_digest_supports_disabling_cognitive_lenses():
    items = {
        "OpenAI News": [
            {
                "title": "Agent runtime notes",
                "link": "https://example.com/agent-runtime",
                "summary": "System notes.",
                "score": 8,
                "ai_bucket": "practice",
            }
        ]
    }

    _, content = formatter.format_daily_digest(
        items,
        raw_only=False,
        include_cognitive_lenses=False,
    )

    assert "## Karpathy 视角：今日认知增量" not in content


def test_format_note_templates_cover_paper_and_video():
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
    assert "## 概念图" in video_content


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
