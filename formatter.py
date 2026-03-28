#!/usr/bin/env python3
"""
formatter.py — Markdown Note Formatting Layer (V2)
Uses Jinja2 templates to generate all Markdown strings.
Pure functions: take data in, return strings out — no I/O side effects.
"""

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# ── Setup Jinja2 Environment ──────────────────────────────────────────────────

TEMPLATE_DIR = Path(__file__).parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), trim_blocks=True, lstrip_blocks=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Note Formatters ───────────────────────────────────────────────────────────

def format_daily_digest(items_by_source: dict, raw_only: bool = False) -> tuple[str, str]:
    """Format a daily digest note.

    Returns:
        (filepath, content) tuple.
    """
    today = today_str()

    if raw_only:
        filepath = f"00-Inbox/Raw-Feeds/Raw-Daily-Feeds-{today}.md"
        lines = [
            f"# Raw Daily Feeds - {today}\n\n"
            "*此文件由 PKM Workflow 生成，供 AI Agent 策展过滤高价值资讯。*\n"
        ]
        for source, items in items_by_source.items():
            lines.append(f"\n## {source}\n")
            for item in items:
                lines.append(f"- **Title**: {item['title']}")
                lines.append(f"  **URL**: {item['link']}")
                if item.get("summary"):
                    lines.append(f"  **Summary**: {item['summary'][:500]}")
                lines.append("")
        content = "\n".join(lines)
    else:
        filepath = f"30-Daily/AI-News/AI-Daily-{today}.md"
        template = env.get_template("daily_digest.md.j2")
        content = template.render(
            today=today,
            now=now_str(),
            items_by_source=items_by_source
        )

    return filepath, content


def format_youtube_raw_block(yt_videos: list) -> str:
    """Format a block of YouTube entries to append to Raw-Daily-Feeds.md."""
    if not yt_videos:
        return ""
    template = env.get_template("youtube_raw_block.md.j2")
    return template.render(videos=yt_videos)


def format_paper_note(paper: dict, source_name: str) -> tuple[str, str]:
    """Format an individual paper note.

    Returns:
        (filepath, content) tuple.
    """
    today = today_str()
    title = paper["title"]
    safe_title = slugify(title)
    filepath = f"20-Sources/Papers/{today}-{safe_title}.md"

    template = env.get_template("paper_note.md.j2")
    content = template.render(
        title=title,
        today=today,
        now=now_str(),
        source_name=source_name,
        link=paper['link'],
        summary=paper['summary']
    )
    return filepath, content


def format_video_note(video: dict) -> tuple[str, str]:
    """Format an individual YouTube video note.

    Returns:
        (filepath, content) tuple.
    """
    name      = video["channel_name"]
    title     = video["title"]
    published = video["published"]
    folder    = video["folder"]

    safe_title = slugify(title)
    filepath = f"{folder}/{published}-{name.replace(' ', '-')}-{safe_title}.md"

    template = env.get_template("video_note.md.j2")
    content = template.render(
        title=title,
        channel_name=name,
        domain=video["domain"],
        published=published,
        link=video["link"],
        summary=video.get("summary", ""),
        now=now_str()
    )
    return filepath, content


def format_ielts_reminder() -> tuple[str, str]:
    """Format a daily IELTS study log note.

    Returns:
        (filepath, content) tuple.
    """
    today = today_str()
    filepath = f"10-Notes/IELTS/IELTS-Study-{today}.md"

    template = env.get_template("study_log.md.j2")
    content = template.render(today=today, now=now_str())
    return filepath, content


def build_note(
    title: str, content: str, tags: list, domain: str,
    source: str = "User Content", language: str = "cn",
    note_type: str = "permanent-note", related: list = None,
    status: str = "unreviewed", core_concept: str = "",
) -> str:
    """Build a complete Markdown PKM note string via Generic Template."""
    related = related or []
    
    template = env.get_template("generic_note.md.j2")
    return template.render(
        title=title,
        today=today_str(),
        now=now_str(),
        tags=tags,
        domain=domain,
        status=status,
        core_concept=core_concept,
        source=source,
        language=language,
        note_type=note_type,
        related=related,
        content=content
    )
