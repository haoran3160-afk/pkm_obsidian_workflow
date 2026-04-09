#!/usr/bin/env python3
"""
formatter.py - Markdown Note Formatting Layer
Uses Jinja2 templates to generate markdown content.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), trim_blocks=True, lstrip_blocks=True)

AI_BUCKET_ORDER = ["frontier", "practice", "tooling"]
AI_BUCKET_LABELS = {
    "frontier": "前沿技巧",
    "practice": "工程实践",
    "tooling": "工具更新",
}

CONTENT_TYPE_ORDER = [
    "news",
    "tweet",
    "engineering",
    "paper",
    "video",
    "tooling",
    "community",
    "other",
]
CONTENT_TYPE_LABELS = {
    "news": "AI 资讯",
    "tweet": "推文速览",
    "engineering": "工程实践",
    "paper": "论文雷达",
    "video": "视频速览",
    "tooling": "工具更新",
    "community": "社区讨论",
    "other": "其他",
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


def _clean_text(text: str, max_len: int) -> str:
    plain = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip())
    if len(plain) <= max_len:
        return plain
    return plain[: max_len - 3].rstrip() + "..."


def _first_sentence(text: str, max_len: int = 140) -> str:
    plain = _clean_text(text, max_len=500)
    if not plain:
        return ""
    match = re.search(r"([.!?。！？])", plain)
    if match:
        plain = plain[: match.end()]
    return _clean_text(plain, max_len=max_len)


def _why_it_matters(item: dict) -> str:
    reasons = " ".join(item.get("score_reasons", []))
    summary = item.get("summary", "")

    if "priority:evaluation" in reasons or "interest:eval" in reasons:
        return "有助于建立评测闭环和质量闸口。"
    if "priority:coding agent" in reasons or "priority:agent engineering" in reasons:
        return "可直接用于搭建或加固 Coding Agent 工作流。"
    if "priority:memory" in reasons:
        return "和长上下文记忆与检索架构决策直接相关。"
    if "practical:+" in reasons:
        return "包含可快速落地的实现细节。"
    if summary:
        return _first_sentence(summary, max_len=110)
    return "存在潜在价值，建议先快速人工扫读。"


def _build_ai_bucket_view(
    items_by_source: dict[str, list[dict]],
) -> dict[str, list[tuple[str, dict]]]:
    buckets: dict[str, list[tuple[str, dict]]] = {k: [] for k in AI_BUCKET_ORDER}

    for source, items in items_by_source.items():
        for item in items:
            if item.get("score") is None:
                continue
            bucket = str(item.get("ai_bucket") or "practice")
            if bucket not in buckets:
                bucket = "practice"
            buckets[bucket].append((source, item))

    for bucket_name in buckets:
        buckets[bucket_name].sort(
            key=lambda pair: (pair[1].get("score", 0), pair[1].get("title", "")),
            reverse=True,
        )
    return buckets


def _build_top_picks(
    items_by_source: dict[str, list[dict]],
    top_picks: int,
) -> list[tuple[str, dict]]:
    scored: list[tuple[str, dict]] = []
    for source, items in items_by_source.items():
        for item in items:
            if item.get("score") is None:
                continue
            scored.append((source, item))
    scored.sort(key=lambda pair: (pair[1].get("score", 0), pair[1].get("title", "")), reverse=True)
    return scored[:top_picks]


def _normalize_content_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v in CONTENT_TYPE_LABELS:
        return v
    return "other"


def _infer_content_type(item: dict, source: str) -> str:
    explicit = item.get("content_type")
    if explicit:
        return _normalize_content_type(str(explicit))

    link = str(item.get("link", "")).lower()
    source_lower = source.lower()
    summary = str(item.get("summary", "")).lower()

    if "arxiv.org" in link or "paperswithcode" in link:
        return "paper"
    if "youtube.com" in link or "youtu.be" in link:
        return "video"
    if any(token in link for token in ("twitter.com", "x.com", "nitter.net", "rsshub.app/twitter")):
        return "tweet"
    if any(token in source_lower for token in ("hackernews", "hacker news", "github", "engineering", "dev")):
        return "engineering"
    if any(token in summary for token in ("implementation", "playbook", "benchmark", "deployment")):
        return "engineering"
    return "news"


def _build_content_type_view(
    items_by_source: dict[str, list[dict]],
) -> dict[str, list[tuple[str, dict]]]:
    groups: dict[str, list[tuple[str, dict]]] = {k: [] for k in CONTENT_TYPE_ORDER}
    for source, items in items_by_source.items():
        for item in items:
            content_type = _infer_content_type(item, source)
            groups[content_type].append((source, item))

    for key in groups:
        groups[key].sort(
            key=lambda pair: (
                pair[1].get("score", -1),
                pair[1].get("published", ""),
                pair[1].get("title", ""),
            ),
            reverse=True,
        )
    return groups


def _obsidian_note_ref(path_str: str) -> str:
    path = Path(path_str)
    return f"[[{path.stem}]]"


def _bucket_label(bucket: str) -> str:
    if bucket in AI_BUCKET_LABELS:
        return AI_BUCKET_LABELS[bucket]
    return AI_BUCKET_LABELS["practice"]


def _sanitize_mermaid_label(text: str, max_len: int = 36) -> str:
    plain = _clean_text(text, max_len=max_len + 20)
    plain = re.sub(r'["`{}\[\]()<>\|]', "", plain).strip()
    if len(plain) > max_len:
        plain = plain[: max_len - 3].rstrip() + "..."
    return plain or "未命名"


def _build_mindmap_block(
    today: str,
    picks: list[tuple[str, dict]],
    content_groups: dict[str, list[tuple[str, dict]]],
    max_items_per_branch: int = 3,
) -> str:
    lines = [
        "```mermaid",
        "mindmap",
        f'  root(("AI 简报 {today}"))',
    ]

    bucket_items: dict[str, list[dict]] = {k: [] for k in AI_BUCKET_ORDER}
    for _, item in picks:
        bucket = str(item.get("ai_bucket") or "practice")
        if bucket not in bucket_items:
            bucket = "practice"
        bucket_items[bucket].append(item)

    for bucket in AI_BUCKET_ORDER:
        items = bucket_items[bucket]
        if not items:
            continue
        lines.append(f'    "{_bucket_label(bucket)}"')
        for item in items[:max_items_per_branch]:
            lines.append(f'      "{_sanitize_mermaid_label(item.get("title", "未命名"))}"')

    for content_type in ("tweet", "engineering", "paper", "video"):
        entries = content_groups.get(content_type, [])
        if not entries:
            continue
        lines.append(f'    "{CONTENT_TYPE_LABELS[content_type]}"')
        for _, item in entries[:max_items_per_branch]:
            lines.append(f'      "{_sanitize_mermaid_label(item.get("title", "未命名"))}"')

    lines.append("```")
    return "\n".join(lines)


def _capture_prompt(item: dict) -> str:
    bucket = str(item.get("ai_bucket") or "practice")
    if bucket == "frontier":
        return "提炼 1 个新技巧 + 1 个关键权衡。"
    if bucket == "tooling":
        return "提炼迁移影响与工具选型标准。"
    return "提炼 1 个本周可验证的流程步骤。"


def format_daily_digest(
    items_by_source: dict,
    raw_only: bool = False,
    *,
    top_picks: int = 8,
    max_items_per_source: int = 3,
    action_items: int = 3,
    max_deferred_items: int = 8,
    include_mindmap: bool = True,
    paper_written: list[dict] | None = None,
    video_written: list[dict] | None = None,
    paper_queue: list[dict] | None = None,
    video_queue: list[dict] | None = None,
    stats: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Format a daily digest note and return (filepath, content)."""
    today = today_str()
    ai_buckets = _build_ai_bucket_view(items_by_source)

    paper_written = paper_written or []
    video_written = video_written or []
    paper_queue = (paper_queue or [])[:max_deferred_items]
    video_queue = (video_queue or [])[:max_deferred_items]
    stats = stats or {}

    if raw_only:
        filepath = f"00-Inbox/Raw-Feeds/Raw-Daily-Feeds-{today}.md"
        lines = [
            f"# 原始信息流日报 - {today}",
            "",
            "*由 PKM 工作流自动生成，供 AI 与人工二次策展。*",
            "",
            "## AI 资讯分桶（快速分拣）",
            "",
            "*固定分区：前沿技巧 / 工程实践 / 工具更新*",
            "",
        ]

        for bucket in AI_BUCKET_ORDER:
            lines.append(f"### {AI_BUCKET_LABELS[bucket]}")
            lines.append("")
            entries = ai_buckets[bucket]
            if not entries:
                lines.append("- *(今日为空)*")
                lines.append("")
                continue

            for source, item in entries:
                lines.append(f"- **标题**: {item['title']}")
                lines.append(f"  **链接**: {item['link']}")
                lines.append(f"  **来源**: {source}")
                lines.append(f"  **兴趣分**: {item.get('score', 0)}")
                if item.get("score_reasons"):
                    lines.append(f"  **评分信号**: {', '.join(item['score_reasons'])}")
                if item.get("summary"):
                    lines.append(f"  **摘要**: {_clean_text(item['summary'], max_len=500)}")
                lines.append("")

        lines.append("## 按来源展开（完整原始项）")
        lines.append("")
        for source, items in items_by_source.items():
            lines.append(f"## {source}")
            lines.append("")
            for item in items:
                lines.append(f"- **标题**: {item['title']}")
                lines.append(f"  **链接**: {item['link']}")
                if item.get("score") is not None:
                    lines.append(f"  **兴趣分**: {item['score']}")
                    if item.get("score_reasons"):
                        lines.append(f"  **评分信号**: {', '.join(item['score_reasons'])}")
                if item.get("summary"):
                    lines.append(f"  **摘要**: {_clean_text(item['summary'], max_len=500)}")
                lines.append("")

        content = "\n".join(lines)
        return filepath, content

    filepath = f"30-Daily/AI-News/AI-Daily-{today}.md"
    picks = _build_top_picks(items_by_source, top_picks=top_picks)
    content_groups = _build_content_type_view(items_by_source)
    daily_only_output = bool(stats.get("daily_only_output", False))
    included_papers = stats.get("papers_written", len(paper_written))
    included_videos = stats.get("videos_written", len(video_written))
    included_tweets = len(content_groups.get("tweet", []))
    included_engineering = len(content_groups.get("engineering", []))

    lines = [
        "---",
        f'title: "AI 每日简报 - {today}"',
        f"date: {today}",
        'tags: ["AI资讯", "日报", "简报"]',
        "type: daily-digest",
        "status: unreviewed",
        f"top_picks: {len(picks)}",
        f"created: {now_str()}",
        "---",
        "",
        f"# AI 每日简报 - {today}",
        "",
        "> [!summary] 60 秒快读",
        f"> - 今日精选：**{len(picks)}** 条",
        f"> - 扫描来源：**{stats.get('sources_scanned', len(items_by_source))}** 个",
        f"> - 覆盖类型：推文 **{included_tweets}**、工程实践 **{included_engineering}**、论文 **{included_papers}**、视频 **{included_videos}**",
        "> - 输出形态：**单一核心 AI Daily**",
        "> - 建议阅读顺序：速读摘要 -> 今日精选 -> 统一雷达 -> 提炼任务",
        "",
        "## 阅读路径",
        "- `60 秒`：速读摘要 + 前 3 条今日精选",
        "- `10 分钟`：+ 统一雷达 + 提炼任务",
        "- `30 分钟`：+ 知识图谱 + 按来源快扫",
        "",
    ]
    if daily_only_output:
        lines.extend(
            [
                "> [!info] 单日报模式",
                "> 本次仅写入一份核心日报，不再额外生成论文/视频独立笔记。",
                "",
            ]
        )

    lines.extend(
        [
            "## 今日精选",
            "",
        ]
    )

    if not picks:
        lines.append("- *(今日暂无达标精选，请查看文末按来源快扫。)*")
        lines.append("")
    else:
        for idx, (source, item) in enumerate(picks, start=1):
            summary = _first_sentence(item.get("summary", ""), max_len=150)
            bucket_label = _bucket_label(str(item.get("ai_bucket") or "practice"))
            lines.append(f"### {idx}. [{item['title']}]({item['link']})")
            lines.append(
                f"`来源: {source}` | `分桶: {bucket_label}` | `兴趣分: {item.get('score', 0)}`"
            )
            lines.append(f"- 为什么值得看：{_why_it_matters(item)}")
            if summary:
                lines.append(f"- 摘要：{summary}")
            score_reasons = item.get("score_reasons", [])
            if score_reasons:
                lines.append(f"- 信号：{', '.join(score_reasons[:3])}")
            lines.append(f"- 提炼提示：{_capture_prompt(item)}")
            lines.append("")

    lines.append("## 统一雷达")
    lines.append("")
    radar_limit = max(2, max_items_per_source + 1)
    radar_types = [t for t in CONTENT_TYPE_ORDER if content_groups.get(t)]
    if not radar_types:
        lines.append("- *(今日暂无条目。)*")
        lines.append("")
    else:
        for content_type in radar_types:
            entries = content_groups[content_type]
            shown = entries[:radar_limit]
            hidden = max(0, len(entries) - len(shown))
            lines.append(f"### {CONTENT_TYPE_LABELS[content_type]}")
            for source, item in shown:
                title = item.get("title", "未命名")
                link = item.get("link", "")
                summary = _first_sentence(item.get("summary", ""), max_len=130)
                meta_parts = [f"来源: {source}"]
                if item.get("score") is not None:
                    meta_parts.append(f"兴趣分: {item.get('score')}")
                line = f"- [{title}]({link}) | " + " | ".join(meta_parts)
                if summary:
                    line += f" | {summary}"
                lines.append(line)
            if hidden > 0:
                lines.append(f"- ...另有 {hidden} 条")
            lines.append("")

    lines.append("## 提炼任务（可执行）")
    lines.append("")
    if picks:
        for idx, (_, item) in enumerate(picks[:action_items], start=1):
            lines.append(
                f"- [ ] {idx}. [{item.get('title', '未命名')}]({item.get('link', '')}) | {_capture_prompt(item)}"
            )
    else:
        lines.append("- [ ] 今日无精选，请从统一雷达中任选 1 条进行提炼。")
    lines.append("")

    if include_mindmap and picks:
        lines.append("## 知识图谱")
        lines.append("")
        lines.append(_build_mindmap_block(today, picks, content_groups))
        lines.append("")

    if paper_queue or video_queue:
        lines.append(f"## 延后队列（每类最多展示 {max_deferred_items} 条）")
        lines.append("")
        if paper_queue:
            lines.append("### 论文")
            for item in paper_queue:
                summary = _first_sentence(item.get("summary", ""), max_len=90)
                line = (
                    f"- [{item.get('title', '未命名')}]({item.get('link', '')})"
                    f" | {item.get('source', '论文来源')}"
                )
                if summary:
                    line += f" | {summary}"
                lines.append(line)
            lines.append("")
        if video_queue:
            lines.append("### 视频")
            for item in video_queue:
                summary = _first_sentence(item.get("summary", ""), max_len=90)
                line = (
                    f"- [{item.get('title', '未命名')}]({item.get('link', '')})"
                    f" | {item.get('source', '视频来源')}"
                )
                if summary:
                    line += f" | {summary}"
                lines.append(line)
            lines.append("")

    lines.append("## 按来源快扫")
    lines.append("")
    non_empty_sources = [(source, items) for source, items in items_by_source.items() if items]
    sorted_sources = sorted(non_empty_sources, key=lambda kv: len(kv[1]), reverse=True)
    for source, items in sorted_sources:
        shown = items[:max_items_per_source]
        hidden = max(0, len(items) - len(shown))
        lines.append("<details>")
        lines.append(
            f"<summary><strong>{source}</strong>（共 {len(items)} 条，展示 {len(shown)} 条）</summary>"
        )
        lines.append("")
        for item in shown:
            title = item.get("title", "未命名")
            link = item.get("link", "")
            summary = _first_sentence(item.get("summary", ""), max_len=120)
            parts: list[str] = []
            if item.get("score") is not None:
                parts.append(f"兴趣分 {item.get('score')}")
            if item.get("ai_bucket"):
                parts.append(_bucket_label(str(item.get("ai_bucket"))))
            meta = f" ({'; '.join(parts)})" if parts else ""
            line = f"- [{title}]({link}){meta}"
            if summary:
                line += f" - {summary}"
            lines.append(line)
        if hidden > 0:
            lines.append(f"- ...另有 {hidden} 条")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    content = "\n".join(lines)
    return filepath, content


def format_youtube_raw_block(yt_videos: list) -> str:
    """Format a block of YouTube entries to append to Raw-Daily-Feeds.md."""
    if not yt_videos:
        return ""
    template = env.get_template("youtube_raw_block.md.j2")
    return template.render(videos=yt_videos)


def format_paper_note(paper: dict, source_name: str) -> tuple[str, str]:
    """Format an individual paper note."""
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
        link=paper["link"],
        summary=paper["summary"],
    )
    return filepath, content


def format_video_note(video: dict) -> tuple[str, str]:
    """Format an individual YouTube video note."""
    name = video["channel_name"]
    title = video["title"]
    published = video["published"]
    folder = video["folder"]

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
        now=now_str(),
    )
    return filepath, content


def build_note(
    title: str,
    content: str,
    tags: list,
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
    """Build a complete Markdown PKM note string via Generic Template."""
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
