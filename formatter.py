#!/usr/bin/env python3
"""
formatter.py - Markdown formatting layer for the PKM workflow.

This module intentionally stays pure: no network calls and no file I/O.
It takes structured items or curation plans and returns Markdown strings.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

import daily_curation
import digest_copy as digest_copy_builder

TEMPLATE_DIR = Path(__file__).parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), trim_blocks=True, lstrip_blocks=True)

AI_BUCKET_ORDER = ["frontier", "practice", "tooling"]
AI_BUCKET_LABELS = {
    "frontier": "前沿技术",
    "practice": "工程实践",
    "tooling": "工具更新",
}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def slugify(text: str) -> str:
    """Convert a title string to a safe filename slug."""
    text = re.sub(r"[^\w\s\-\u4e00-\u9fff]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = text.strip("-_")[:80]
    return text or "untitled"


def format_daily_digest(
    items_by_source: dict[str, list[dict[str, Any]]],
    raw_only: bool = False,
    *,
    top_picks: int = 8,
    max_items_per_source: int = 3,
    action_items: int = 3,
    max_deferred_items: int = 8,
    include_mindmap: bool = True,
    include_cognitive_lenses: bool = True,
    cognitive_questions: list[str] | None = None,
    quality_gate_enabled: bool = True,
    tldr_min_quality_score: int = 1,
    tldr_max_undisclosed: int = 0,
    tldr_min_items: int = 1,
    tldr_min_hard_signal_ratio: float = 0.4,
    tldr_max_undisclosed_ratio: float = 0.3,
    min_top_nonpaper: int = 2,
    min_top_content_types: int = 2,
    max_paper_in_top: int = 1,
    paper_written: list[dict[str, Any]] | None = None,
    video_written: list[dict[str, Any]] | None = None,
    paper_queue: list[dict[str, Any]] | None = None,
    video_queue: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    curation_plan: daily_curation.DailyDigestPlan | None = None,
    digest_copy: dict[str, Any] | None = None,
    used_urls: set[str] | None = None,
    rotation_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Format the raw digest or the final curated daily digest."""
    del (
        max_items_per_source,
        include_mindmap,
        include_cognitive_lenses,
        cognitive_questions,
        quality_gate_enabled,
        tldr_min_quality_score,
        tldr_max_undisclosed,
        tldr_min_items,
        tldr_min_hard_signal_ratio,
        tldr_max_undisclosed_ratio,
    )

    if raw_only:
        return _render_raw_digest(items_by_source)

    plan = curation_plan or daily_curation.plan_daily_digest(
        dict(items_by_source),
        today=today_str(),
        top_picks=top_picks,
        action_items=action_items,
        max_deferred_items=max_deferred_items,
        min_top_nonpaper=min_top_nonpaper,
        min_top_content_types=min_top_content_types,
        max_paper_in_top=max_paper_in_top,
        paper_written=paper_written or [],
        video_written=video_written or [],
        paper_queue=paper_queue or [],
        video_queue=video_queue or [],
        stats=stats or {},
        used_urls=used_urls,
        rotation_state=rotation_state,
    )

    base_copy = digest_copy_builder.build_digest_copy(plan)
    final_copy = digest_copy_builder.merge_digest_copy(base_copy, digest_copy)
    return _render_curated_daily_digest(plan, final_copy)


def _render_raw_digest(items_by_source: dict[str, list[dict[str, Any]]]) -> tuple[str, str]:
    today = today_str()
    filepath = f"00-Inbox/Raw-Feeds/Raw-Daily-Feeds-{today}.md"

    bucketed: dict[str, list[tuple[str, dict[str, Any]]]] = {key: [] for key in AI_BUCKET_ORDER}
    for source, items in items_by_source.items():
        for item in items:
            bucket = str(item.get("ai_bucket", "practice") or "practice")
            if bucket not in bucketed:
                bucket = "practice"
            bucketed[bucket].append((source, item))

    lines = [
        f"# 原始信息流日报 - {today}",
        "",
        "*由 PKM 工作流自动生成，供 Agent 或人工二次策展。*",
        "",
        "## AI 资讯分桶",
        "",
    ]

    for bucket in AI_BUCKET_ORDER:
        lines.append(f"### {AI_BUCKET_LABELS[bucket]}")
        lines.append("")
        entries = bucketed[bucket]
        if not entries:
            lines.append("- *(今日为空)*")
            lines.append("")
            continue

        for source, item in entries:
            lines.extend(_render_raw_item(source, item))
            lines.append("")

    lines.append("## 按来源展开")
    lines.append("")
    for source, items in items_by_source.items():
        lines.append(f"## {source}")
        lines.append("")
        for item in items:
            lines.extend(_render_raw_item(source, item))
            lines.append("")

    return filepath, "\n".join(lines).rstrip() + "\n"


def _render_raw_item(source: str, item: dict[str, Any]) -> list[str]:
    lines = [
        f"- **标题**：{item.get('title', 'Untitled')}",
        f"  **链接**：{item.get('link', '')}",
        f"  **来源**：{source}",
    ]
    if item.get("score") is not None:
        lines.append(f"  **兴趣分**：{item.get('score')}")
    if item.get("score_reasons"):
        lines.append(f"  **评分信号**：{', '.join(item['score_reasons'])}")
    summary = _clean_text(str(item.get("summary", "")), max_len=500)
    if summary:
        lines.append(f"  **摘要**：{summary}")
    return lines


def _render_curated_daily_digest(
    plan: daily_curation.DailyDigestPlan,
    digest_copy: dict[str, Any],
) -> tuple[str, str]:
    filepath = f"30-Daily/AI-News/AI-Daily-{plan.date}.md"
    lines = [
        "---",
        f'title: "AI & Growth Digest - {plan.date}"',
        f"date: {plan.date}",
        "tags:",
        "  - daily-digest",
        "  - AI-news",
        "  - AI-solopreneur",
        'type: "digest"',
        'status: "inbox"',
        f'aliases: ["Daily Digest {plan.date}"]',
        "---",
        "",
    ]

    for idx, (source, item) in enumerate(plan.top_stories, start=1):
        copy = digest_copy.get("top_stories", [])[idx - 1]
        lines.extend(_render_deep_block(f"🔥 Top {idx}", source, item, copy))

    if plan.venture_story:
        lines.extend(_render_deep_block("💰 创投洞见", *plan.venture_story, digest_copy["venture_story"]))
    if plan.growth_story:
        lines.extend(_render_brief_block("🌱", *plan.growth_story, digest_copy["insight_story"]))
    if plan.video_story:
        lines.extend(_render_video_block(*plan.video_story, digest_copy["video_story"]))
    if plan.solopreneur_story:
        lines.extend(_render_brief_block("🤖", *plan.solopreneur_story, digest_copy["ai_company_story"]))

    return filepath, "\n".join(lines).rstrip() + "\n"


def _render_deep_block(
    label: str,
    source: str,
    item: dict[str, Any],
    copy: dict[str, Any],
) -> list[str]:
    return [
        f"## {label} - {copy['headline_cn']}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{_story_title(item)}]({item.get('link', '')})",
        f"**核心概念**：{' '.join(copy['core_concepts'])}",
        "",
        "### 深度 Takeaways",
        "",
        f"**核心发现**：{copy['core_finding']}",
        "",
        "**关键细节**：",
        *[f"- {point}" for point in copy["key_details"]],
        "",
        f"**行动启示**：{copy['actionable_insight']}",
        "",
        "---",
        "",
    ]


def _render_brief_block(
    icon: str,
    source: str,
    item: dict[str, Any],
    copy: dict[str, Any],
) -> list[str]:
    return [
        f"## {icon} 洞见 - {copy['headline_cn']}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{_story_title(item)}]({item.get('link', '')})",
        f"**核心概念**：{' '.join(copy['core_concepts'])}",
        "",
        f"**一句话**：{copy['one_line_summary']}",
        "",
        "**3 个要点**：",
        *[f"- {point}" for point in copy["key_points"]],
        "",
        f"**行动启示**：{copy['actionable_insight']}",
        "",
        "---",
        "",
    ]


def _render_video_block(
    source: str,
    item: dict[str, Any],
    copy: dict[str, Any],
) -> list[str]:
    return [
        f"## 📺 今日视频 - {copy['headline_cn']}",
        "",
        f"**频道**：{source}",
        f"**链接**：[{_story_title(item)}]({item.get('link', '')})",
        f"**时长**：{_video_duration_text(item)}",
        f"**核心概念**：{' '.join(copy['core_concepts'])}",
        "",
        f"**核心结论**：{copy['core_conclusion']}",
        "",
        "**关键方法论**：",
        *[f"- {point}" for point in copy["method_points"]],
        "",
        f"**行动启示**：{copy['actionable_insight']}",
        "",
        "---",
        "",
    ]


def _story_title(item: dict[str, Any]) -> str:
    return _clean_text(str(item.get("title", "Untitled")), max_len=140)


def _video_duration_text(item: dict[str, Any]) -> str:
    for key in ("duration_text", "duration", "length"):
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return "未披露"


def _clean_text(text: str, *, max_len: int) -> str:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) <= max_len:
        return plain
    return plain[: max_len - 1].rstrip() + "…"


def format_paper_note(paper: dict[str, Any], source_name: str) -> tuple[str, str]:
    """Format an individual paper note."""
    today = today_str()
    title = str(paper.get("title", "Untitled"))
    filepath = f"20-Sources/Papers/{today}-{slugify(title)}.md"

    template = env.get_template("paper_note.md.j2")
    content = template.render(
        title=title,
        today=today,
        now=now_str(),
        source_name=source_name,
        link=paper.get("link", ""),
        summary=_clean_text(str(paper.get("summary", "")), max_len=800),
    )
    return filepath, content


def format_video_note(video: dict[str, Any]) -> tuple[str, str]:
    """Format an individual YouTube video note."""
    channel_name = str(video["channel_name"])
    title = str(video["title"])
    published = str(video["published"])
    folder = str(video["folder"])
    filepath = f"{folder}/{published}-{channel_name.replace(' ', '-')}-{slugify(title)}.md"

    template = env.get_template("video_note.md.j2")
    content = template.render(
        title=title,
        channel_name=channel_name,
        domain=video["domain"],
        published=published,
        link=video["link"],
        summary=_clean_text(str(video.get("summary", "")), max_len=800),
        now=now_str(),
    )
    return filepath, content


def build_note(
    title: str,
    content: str,
    tags: list[str],
    domain: str,
    source: str = "用户输入",
    language: str = "cn",
    note_type: str = "permanent-note",
    related: list[Any] | None = None,
    status: str = "unreviewed",
    core_concept: str = "",
    entity_type: str = "concept",
    source_count: int = 1,
    sources: list[str] | None = None,
) -> str:
    """Build a complete Markdown PKM note string via the generic template."""
    related = related or []
    sources = [s for s in (sources or []) if s]
    if not sources and source:
        sources = [source]
    source_count = max(source_count, len(sources))

    template = env.get_template("generic_note.md.j2")
    return template.render(
        title=title,
        today=today_str(),
        now=now_str(),
        tags=tags,
        domain=domain,
        status=status,
        core_concept=core_concept,
        entity_type=entity_type,
        source=source,
        source_count=source_count,
        sources=sources,
        language=language,
        note_type=note_type,
        related=related,
        content=content,
    )
