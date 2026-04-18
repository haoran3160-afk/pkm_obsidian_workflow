import daily_curation
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
    assert "## AI 资讯分桶" in content
    assert "### 前沿技术" in content
    assert "Context engineering guide" in content
    assert "https://example.com/a" in content
    assert "## LangChain Blog" in content
    assert "Unscored item" in content


def test_format_daily_digest_final_mode_uses_clean_local_template():
    items = {
        "OpenAI News": [
            {
                "title": "Codex for (almost) everything",
                "link": "https://example.com/codex",
                "summary": "Computer use, multimodal integration, and memory updates.",
                "score": 10,
                "score_reasons": ["priority:agent engineering"],
                "ai_bucket": "practice",
                "content_type": "news",
                "domain": "AI-News",
                "published": "2026-04-17",
            }
        ],
        "3Blue1Brown": [
            {
                "title": "Escher's most mind-bending piece",
                "link": "https://youtube.com/watch?v=abc",
                "summary": "Complex analysis and logarithmic mapping.",
                "content_type": "video",
                "published": "2026-04-17",
                "domain": "Math-Modeling",
            }
        ],
    }

    path, content = formatter.format_daily_digest(items, raw_only=False)

    assert path.startswith("30-Daily/AI-News/AI-Daily-")
    assert 'title: "AI & Growth Digest - ' in content
    assert "## 🔥 Top 1 - " in content
    assert "### 深度 Takeaways" in content
    assert "**来源**：" in content
    assert "**行动启示**：" in content
    assert "## 📺 今日视频 - " in content
    assert "**频道**：" in content
    assert "Daily Snapshot" not in content
    assert "Action Queue" not in content


def test_format_daily_digest_merges_llm_override_with_stable_base_copy():
    items = {
        "OpenAI News": [
            {
                "title": "Codex for (almost) everything",
                "link": "https://example.com/codex",
                "summary": "Computer use and in-app browsing.",
                "score": 10,
                "content_type": "news",
                "domain": "AI-News",
                "published": "2026-04-17",
            }
        ],
        "Latent Space": [
            {
                "title": "Harness engineering for coding agents",
                "link": "https://example.com/harness",
                "summary": "Workflow systems and eval loops.",
                "score": 9,
                "content_type": "news",
                "domain": "AI-News",
                "published": "2026-04-17",
            }
        ],
        "Google AI Blog": [
            {
                "title": "Turn your best AI prompts into one-click tools in Chrome",
                "link": "https://example.com/skills",
                "summary": "Reusable workflows and skills.",
                "score": 8,
                "content_type": "news",
                "domain": "AI-Company",
                "published": "2026-04-17",
            }
        ],
    }

    llm_override = {
        "top_stories": [
            {
                "headline_cn": "OpenAI 把桌面 Agent 推到真实工作流入口",
                "core_concepts": ["#concept/Agent-Engineering", "#concept/Browser-Automation"],
                "core_finding": "Codex 的重点不在聊天，而在执行层。",
                "key_details": ["支持 Computer Use", "代理开始处理跨应用操作"],
                "actionable_insight": "优先把跨应用 SOP 交给本地 Agent。",
            }
        ],
        "venture_story": None,
        "insight_story": None,
        "video_story": None,
        "ai_company_story": {
            "headline_cn": "Chrome 开始把高频 Prompt 做成技能",
            "core_concepts": ["#concept/Reusable-Workflows", "#concept/Browser-Automation"],
            "one_line_summary": "重点不是写 Prompt，而是把它产品化。",
            "key_points": ["可以封装技能", "可以一键复用", "可以团队共享"],
            "actionable_insight": "把高频 Prompt 固化成本地技能。",
        },
    }

    curation_plan = daily_curation.DailyDigestPlan(
        date=formatter.today_str(),
        top_stories=[
            ("OpenAI News", items["OpenAI News"][0]),
            ("Latent Space", items["Latent Space"][0]),
        ],
        solopreneur_story=("Google AI Blog", items["Google AI Blog"][0]),
    )

    _, content = formatter.format_daily_digest(
        items,
        raw_only=False,
        curation_plan=curation_plan,
        digest_copy=llm_override,
    )

    assert "OpenAI 把桌面 Agent 推到真实工作流入口" in content
    assert "Chrome 开始把高频 Prompt 做成技能" in content
    assert "## 🤖 洞见 - Chrome 开始把高频 Prompt 做成技能" in content
    assert "## 🔥 Top 2 - " in content


def test_format_note_templates_cover_paper_and_video():
    paper_path, paper_content = formatter.format_paper_note(
        {"title": "Paper: LLM Eval", "link": "https://arxiv.org/abs/1234", "summary": "summary"},
        source_name="arXiv",
    )
    assert paper_path.startswith("20-Sources/Papers/")
    assert "Paper: LLM Eval" in paper_content
    assert "一句话摘要" in paper_content

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
    assert "观看目标" in video_content


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
