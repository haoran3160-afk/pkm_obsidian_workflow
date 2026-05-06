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
    "frontier": "前沿突破",
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
    template = env.get_template("raw_daily_feeds.md.j2")
    content = template.render(
        today=today,
        buckets=_build_raw_bucket_context(items_by_source),
        sources=_build_raw_source_context(items_by_source),
    )
    return filepath, content.rstrip() + "\n"


def _render_curated_daily_digest(
    plan: daily_curation.DailyDigestPlan,
    digest_copy: dict[str, Any],
) -> tuple[str, str]:
    filepath = f"30-Daily/AI-News/AI-Daily-{plan.date}.md"
    template = env.get_template("curated_daily_digest.md.j2")
    content = template.render(
        plan=plan,
        top_sections=_build_top_sections(plan, digest_copy),
        venture_section=_build_deep_section("创投洞见", plan.venture_story, digest_copy.get("venture_story")),
        insight_section=_build_brief_section("洞见", plan.growth_story, digest_copy.get("insight_story")),
        video_section=_build_video_section(plan.video_story, digest_copy.get("video_story")),
        company_section=_build_brief_section("洞见", plan.solopreneur_story, digest_copy.get("ai_company_story")),
    )
    return filepath, content.rstrip() + "\n"


def _build_raw_bucket_context(
    items_by_source: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    bucketed: dict[str, list[dict[str, Any]]] = {key: [] for key in AI_BUCKET_ORDER}
    for source, items in items_by_source.items():
        for item in items:
            bucket = str(item.get("ai_bucket", "practice") or "practice")
            if bucket not in bucketed:
                bucket = "practice"
            bucketed[bucket].append(_normalize_raw_item(source, item))

    return [
        {
            "key": bucket,
            "label": AI_BUCKET_LABELS[bucket],
            "entries": bucketed[bucket],
        }
        for bucket in AI_BUCKET_ORDER
    ]


def _build_raw_source_context(
    items_by_source: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    return [
        {
            "name": source,
            "entries": [_normalize_raw_item(source, item) for item in items],
        }
        for source, items in items_by_source.items()
    ]


def _normalize_raw_item(source: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "title": _story_title(item),
        "link": str(item.get("link", "")).strip(),
        "score": item.get("score"),
        "score_reasons": list(item.get("score_reasons", []) or []),
        "summary": _clean_text(str(item.get("summary", "")), max_len=500),
    }


def _build_top_sections(
    plan: daily_curation.DailyDigestPlan,
    digest_copy: dict[str, Any],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    top_copy = list(digest_copy.get("top_stories", []))
    for idx, story in enumerate(plan.top_stories):
        copy = top_copy[idx] if idx < len(top_copy) else {}
        section = _build_deep_section(f"Top {idx + 1}", story, copy)
        if section:
            sections.append(section)
    return sections


def _build_deep_section(
    label: str,
    story: tuple[str, dict[str, Any]] | None,
    copy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not story or not copy:
        return None
    source, item = story
    return {
        "label": label,
        "headline_cn": str(copy.get("headline_cn", "")).strip() or _story_title(item),
        "source": source,
        "link": str(item.get("link", "")).strip(),
        "story_title": _story_title(item),
        "core_concepts": _normalize_list(copy.get("core_concepts"), fallback=["#concept/AI-News"]),
        "core_finding": str(copy.get("core_finding", "")).strip() or _clean_text(
            str(item.get("summary", "")), max_len=120
        ),
        "key_details": _normalize_list(copy.get("key_details"), fallback=[_clean_text(str(item.get("summary", "")), max_len=140)]),
        "actionable_insight": str(copy.get("actionable_insight", "")).strip() or "建议回看原文并提炼为可执行 SOP。",
    }


def _build_brief_section(
    label: str,
    story: tuple[str, dict[str, Any]] | None,
    copy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not story or not copy:
        return None
    source, item = story
    return {
        "label": label,
        "headline_cn": str(copy.get("headline_cn", "")).strip() or _story_title(item),
        "source": source,
        "link": str(item.get("link", "")).strip(),
        "story_title": _story_title(item),
        "core_concepts": _normalize_list(copy.get("core_concepts"), fallback=["#concept/AI-News"]),
        "one_line_summary": str(copy.get("one_line_summary", "")).strip() or _clean_text(
            str(item.get("summary", "")), max_len=80
        ),
        "key_points": _normalize_list(copy.get("key_points"), fallback=[_clean_text(str(item.get("summary", "")), max_len=140)]),
        "actionable_insight": str(copy.get("actionable_insight", "")).strip() or "标记到后续选题池，结合原文再判断优先级。",
    }


def _build_video_section(
    story: tuple[str, dict[str, Any]] | None,
    copy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not story or not copy:
        return None
    source, item = story
    channel_name = str(item.get("channel_name") or source).strip() or source
    return {
        "label": "今日视频",
        "headline_cn": str(copy.get("headline_cn", "")).strip() or _story_title(item),
        "channel_name": channel_name,
        "link": str(item.get("link", "")).strip(),
        "story_title": _story_title(item),
        "core_concepts": _normalize_list(copy.get("core_concepts"), fallback=["#concept/Visual-Learning"]),
        "core_conclusion": str(copy.get("core_conclusion", "")).strip() or _clean_text(
            str(item.get("summary", "")), max_len=120
        ),
        "method_points": _normalize_list(copy.get("method_points"), fallback=[_clean_text(str(item.get("summary", "")), max_len=140)]),
        "actionable_insight": str(copy.get("actionable_insight", "")).strip() or "优先提炼可迁移的方法框架，而不是只记结论。",
    }


def _normalize_list(value: Any, *, fallback: list[str]) -> list[str]:
    values = [str(item).strip() for item in (value or []) if str(item).strip()]
    if values:
        return values
    return fallback


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
