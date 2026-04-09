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

DEFAULT_COGNITIVE_QUESTIONS = [
    "能力边界是否实质前移（不仅是榜单数字）？",
    "架构范式是否变化（例如主模型+子代理）？",
    "成本-延迟-质量前沿是否改写？",
    "评测与治理是否可复现、可审计？",
    "能否沉淀为长期杠杆（SOP/模板/基线）？",
]


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
    # Reuse the summary splitter to avoid truncating decimal tokens like "GPT-5.4".
    return _multi_sentence_summary(text, max_len=max_len, max_sentences=1)


def _multi_sentence_summary(text: str, max_len: int = 220, max_sentences: int = 2) -> str:
    plain = _clean_text(text, max_len=1200)
    if not plain:
        return ""

    segments = re.split(r"(?<=[.!?。！？])\s+", plain)
    picked: list[str] = []
    for segment in segments:
        normalized = segment.strip()
        if not normalized:
            continue
        picked.append(normalized)
        if len(picked) >= max_sentences:
            break

    merged = " ".join(picked) if picked else plain
    return _clean_text(merged, max_len=max_len)


def _karpathy_lens(item: dict, source: str) -> str:
    bucket = str(item.get("ai_bucket") or "practice")
    content_type = _infer_content_type(item, source)

    if content_type == "paper" or bucket == "frontier":
        return "能力边界与研究方向"
    if content_type in {"engineering", "tooling"} or bucket == "tooling":
        return "工程杠杆与系统设计"
    if content_type == "tweet":
        return "生态信号与叙事拐点"
    if content_type == "video":
        return "心智模型与学习路径"
    return "产品化节奏与落地窗口"


def _daily_one_thing(item: dict, source: str) -> str:
    content_type = _infer_content_type(item, source)
    bucket = str(item.get("ai_bucket") or "practice")

    if content_type == "paper" or bucket == "frontier":
        return "把其中 1 个新能力做成最小复现实验，记录失败边界与可迁移条件。"
    if content_type in {"engineering", "tooling"} or bucket == "tooling":
        return "把文中方案映射到你现有链路，列出替换成本、收益与回滚条件。"
    if content_type == "tweet":
        return "把该观点与 1 个反例对照，避免只凭社区热度做判断。"
    return "提炼 1 条本周可验证动作，写入任务清单并设定完成标准。"


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
        if re.search(r"[\u4e00-\u9fff]", summary):
            return _first_sentence(summary, max_len=110)
        return "提供一线英文材料，可用于补齐背景、案例与验证依据。"
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

    if not scored:
        return []

    selected: list[tuple[str, dict]] = []
    seen_links: set[str] = set()

    # Diversity pass: take at most one item per content type first.
    for content_type in CONTENT_TYPE_ORDER:
        for source, item in scored:
            if _infer_content_type(item, source) != content_type:
                continue
            link = str(item.get("link", ""))
            if link in seen_links:
                continue
            selected.append((source, item))
            seen_links.add(link)
            break
        if len(selected) >= top_picks:
            return selected

    # Fill by global score order.
    for source, item in scored:
        link = str(item.get("link", ""))
        if link in seen_links:
            continue
        selected.append((source, item))
        seen_links.add(link)
        if len(selected) >= top_picks:
            break

    return selected


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
    if any(
        token in source_lower
        for token in ("hackernews", "hacker news", "github", "engineering", "dev")
    ):
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

    existing_branches: set[str] = set()
    for bucket in AI_BUCKET_ORDER:
        items = bucket_items[bucket]
        if not items:
            continue
        bucket_name = _bucket_label(bucket)
        existing_branches.add(bucket_name)
        lines.append(f'    "{bucket_name}"')
        for item in items[:max_items_per_branch]:
            lines.append(f'      "{_sanitize_mermaid_label(item.get("title", "未命名"))}"')

    for content_type in ("tweet", "engineering", "paper", "video"):
        entries = content_groups.get(content_type, [])
        if not entries:
            continue
        branch_name = CONTENT_TYPE_LABELS[content_type]
        if branch_name in existing_branches:
            continue
        lines.append(f'    "{branch_name}"')
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


def _published_date(item: dict) -> str:
    raw = str(item.get("published", "")).strip()
    if not raw:
        return "日期未知"
    return raw[:10] if len(raw) >= 10 else raw


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _reason_to_cn(reason: str) -> str:
    token = reason.strip().lower()
    if token.startswith("priority:evaluation"):
        return "评测体系"
    if token.startswith("priority:coding agent") or token.startswith("priority:agent engineering"):
        return "编码代理"
    if token.startswith("priority:tool calling"):
        return "工具调用"
    if token.startswith("priority:memory"):
        return "记忆机制"
    if token.startswith("interest:workflow"):
        return "工作流设计"
    if token.startswith("interest:eval") or token.startswith("interest:evaluation"):
        return "评测实践"
    if token.startswith("practical:+"):
        return "可落地实现"
    if token.startswith("base-ai"):
        return "AI 核心议题"
    cleaned = reason.split(":")[-1].strip()
    return cleaned or "行业动态"


def _signal_summary_cn(item: dict, source: str) -> str:
    mapped: list[str] = []
    for reason in item.get("score_reasons", []):
        text = _reason_to_cn(str(reason))
        if text and text not in mapped:
            mapped.append(text)
        if len(mapped) >= 3:
            break
    if mapped:
        return "、".join(mapped)
    content_type = _infer_content_type(item, source)
    return f"{CONTENT_TYPE_LABELS.get(content_type, '其他')}动态"


def _summary_cn(
    item: dict,
    source: str,
    *,
    max_len: int = 220,
    max_sentences: int = 2,
) -> str:
    raw = _multi_sentence_summary(
        item.get("summary", ""), max_len=max_len, max_sentences=max_sentences
    )
    if raw and _has_cjk(raw):
        return raw

    signal = _signal_summary_cn(item, source)
    lens = _karpathy_lens(item, source)
    action = _capture_prompt(item)
    generated = f"英文原文聚焦{signal}，对应「{lens}」维度；建议：{action}"
    return _clean_text(generated, max_len=max_len)


def _build_tldr_candidates(
    picks: list[tuple[str, dict]],
    content_groups: dict[str, list[tuple[str, dict]]],
    limit: int,
) -> list[tuple[str, dict]]:
    selected: list[tuple[str, dict]] = []
    seen_links: set[str] = set()

    def _append(entries: list[tuple[str, dict]]) -> None:
        for source, item in entries:
            link = str(item.get("link", ""))
            if link in seen_links:
                continue
            seen_links.add(link)
            selected.append((source, item))
            if len(selected) >= limit:
                return

    _append(picks)
    if len(selected) >= limit:
        return selected

    for content_type in CONTENT_TYPE_ORDER:
        _append(content_groups.get(content_type, []))
        if len(selected) >= limit:
            break

    return selected


def _build_keyword_tags(
    picks: list[tuple[str, dict]],
    content_groups: dict[str, list[tuple[str, dict]]],
    limit: int = 10,
) -> list[str]:
    tags: list[str] = []

    def _add(tag: str) -> None:
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", tag.strip())
        if not normalized:
            return
        hashtag = normalized if normalized.startswith("#") else f"#{normalized}"
        if hashtag not in tags:
            tags.append(hashtag)

    for content_type in ("news", "engineering", "paper", "video", "tweet", "tooling"):
        if content_groups.get(content_type):
            _add(CONTENT_TYPE_LABELS.get(content_type, "其他"))

    for _, item in picks:
        bucket = _bucket_label(str(item.get("ai_bucket") or "practice"))
        _add(bucket)
        for reason in item.get("score_reasons", [])[:2]:
            token = str(reason).split(":")[-1].strip()
            _add(token)
        if len(tags) >= limit:
            break

    return tags[:limit]


def _collect_source_evidence(
    picks: list[tuple[str, dict]],
    content_groups: dict[str, list[tuple[str, dict]]],
    max_links: int = 8,
) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(source: str, item: dict) -> None:
        url = str(item.get("link", "")).strip()
        if not url or url in seen:
            return
        seen.add(url)
        links.append((source, url))

    for source, item in picks:
        _add(source, item)
        if len(links) >= max_links:
            return links

    for content_type in CONTENT_TYPE_ORDER:
        for source, item in content_groups.get(content_type, []):
            _add(source, item)
            if len(links) >= max_links:
                return links

    return links


def format_daily_digest(
    items_by_source: dict,
    raw_only: bool = False,
    *,
    top_picks: int = 8,
    max_items_per_source: int = 3,
    action_items: int = 3,
    max_deferred_items: int = 8,
    include_mindmap: bool = True,
    include_cognitive_lenses: bool = True,
    cognitive_questions: list[str] | None = None,
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
    question_pool = cognitive_questions or DEFAULT_COGNITIVE_QUESTIONS
    normalized_questions = [q.strip() for q in question_pool if q and q.strip()]
    if not normalized_questions:
        normalized_questions = DEFAULT_COGNITIVE_QUESTIONS

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

    tldr_limit = min(12, max(6, top_picks))
    tldr_candidates = _build_tldr_candidates(picks, content_groups, limit=tldr_limit)
    keyword_tags = _build_keyword_tags(picks, content_groups, limit=10)
    evidence_links = _collect_source_evidence(picks, content_groups, max_links=8)

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
        "> - 阅读结构：TL;DR -> 关键结论 -> 分栏简报 -> 执行清单",
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
            "## 今日 TL;DR（Tier 1）",
            "",
        ]
    )
    if not tldr_candidates:
        lines.append("- *(今日暂无达标条目。)*")
        lines.append("")
    else:
        for source, item in tldr_candidates:
            headline = _clean_text(str(item.get("title", "未命名")), max_len=80)
            summary = _summary_cn(item, source, max_len=120, max_sentences=1)
            if not summary:
                summary = _why_it_matters(item)
            lines.append(f"- {headline}：{summary}（{source}，{_published_date(item)}）")
        lines.append("")

    if include_cognitive_lenses:
        lines.extend(
            [
                "## Karpathy 视角：今日认知增量",
                "",
                "> [!tip] 认知评估框架",
            ]
        )
        for question in normalized_questions[:8]:
            lines.append(f"> - {question}")
        lines.append("")

        if not picks:
            lines.append("- *(今日暂无达标精选，暂不生成认知增量判断。)*")
            lines.append("")
        else:
            for idx, (source, item) in enumerate(picks[:3], start=1):
                lens = _karpathy_lens(item, source)
                content_type = _infer_content_type(item, source)
                content_type_label = CONTENT_TYPE_LABELS.get(content_type, "其他")
                summary = _summary_cn(item, source, max_len=220, max_sentences=2)

                lines.append(f"### 判断 {idx}：{lens}")
                lines.append(
                    f"- 证据：[{item['title']}]({item['link']})（{source} / {content_type_label} / {_published_date(item)}）"
                )
                if summary:
                    lines.append(f"- 发生了什么：{summary}")
                lines.append(f"- 为什么重要：{_why_it_matters(item)}")
                lines.append(f"- 今日动作：{_daily_one_thing(item, source)}")
                lines.append("")

    lines.extend(
        [
            "## 关键结论（Takeaways）",
            "",
            "| 主题 | 关键变化 | 影响判断 | 今日动作 |",
            "|---|---|---|---|",
        ]
    )
    if picks:
        for source, item in picks[:3]:
            topic = _karpathy_lens(item, source)
            change = _clean_text(
                _summary_cn(item, source, max_len=80, max_sentences=1)
                or str(item.get("title", "")),
                max_len=80,
            )
            impact = _clean_text(_why_it_matters(item), max_len=80)
            action = _clean_text(_daily_one_thing(item, source), max_len=80)
            lines.append(f"| {topic} | {change} | {impact} | {action} |")
    else:
        lines.append("| 暂无高价值条目 | - | - | 从分栏简报挑 1 条做复盘 |")
    lines.append("")

    lines.append("## 分栏简报（Tier 2）")
    lines.append("")
    section_limit = max(3, max_items_per_source + 2)
    radar_types = [t for t in CONTENT_TYPE_ORDER if content_groups.get(t)]
    if not radar_types:
        lines.append("- *(今日暂无条目。)*")
        lines.append("")
    else:
        for content_type in radar_types:
            entries = content_groups[content_type]
            shown = entries[:section_limit]
            hidden = max(0, len(entries) - len(shown))
            lines.append(f"### {CONTENT_TYPE_LABELS[content_type]}")

            major_sources = []
            for source, _ in entries:
                if source not in major_sources:
                    major_sources.append(source)
                if len(major_sources) >= 2:
                    break
            source_label = " / ".join(major_sources) if major_sources else "无"
            lines.append(f"> 共 **{len(entries)}** 条；主要来源：{source_label}。")

            for source, item in shown:
                title = _clean_text(str(item.get("title", "未命名")), max_len=110)
                link = str(item.get("link", ""))
                summary = _summary_cn(item, source, max_len=180, max_sentences=2)
                score = item.get("score")
                detail = f"来源：{source}；日期：{_published_date(item)}"
                if score is not None:
                    detail += f"；兴趣分：{score}"

                if summary:
                    lines.append(f"- [{title}]({link}) — {summary}（{detail}）")
                else:
                    lines.append(f"- [{title}]({link})（{detail}）")

            if hidden > 0:
                lines.append(f"- ...另有 {hidden} 条")
            lines.append("")

    lines.append("## 可执行清单（Action Queue）")
    lines.append("")
    if picks:
        for idx, (_, item) in enumerate(picks[:action_items], start=1):
            lines.append(
                f"- [ ] {idx}. [{item.get('title', '未命名')}]({item.get('link', '')}) | {_capture_prompt(item)}"
            )
    else:
        lines.append("- [ ] 今日无精选，请从分栏简报中任选 1 条进行提炼。")
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
                source = str(item.get("source", "论文来源"))
                summary = _summary_cn(item, source, max_len=90, max_sentences=1)
                line = f"- [{item.get('title', '未命名')}]({item.get('link', '')}) | {source}"
                if summary:
                    line += f" | {summary}"
                lines.append(line)
            lines.append("")
        if video_queue:
            lines.append("### 视频")
            for item in video_queue:
                source = str(item.get("source", "视频来源"))
                summary = _summary_cn(item, source, max_len=90, max_sentences=1)
                line = f"- [{item.get('title', '未命名')}]({item.get('link', '')}) | {source}"
                if summary:
                    line += f" | {summary}"
                lines.append(line)
            lines.append("")

    lines.append("## 证据来源（Top Sources）")
    lines.append("")
    if evidence_links:
        refs = [f"[{_clean_text(source, 24)}]({url})" for source, url in evidence_links]
        lines.append(" | ".join(refs))
    else:
        lines.append("- *(暂无可用来源链接)*")
    lines.append("")

    lines.append("## 关键词")
    lines.append("")
    if keyword_tags:
        lines.append(" ".join(keyword_tags))
    else:
        lines.append("#AI资讯 #日报")
    lines.append("")

    lines.append("## 快速统计")
    lines.append("")
    lines.append(f"- 来源总数：**{stats.get('sources_scanned', len(items_by_source))}**")
    lines.append(f"- Top Picks：**{len(picks)}**")
    lines.append(
        f"- 类型覆盖：AI资讯 {len(content_groups.get('news', []))} / 推文 {included_tweets} / 工程 {included_engineering} / 论文 {included_papers} / 视频 {included_videos}"
    )
    non_empty_sources = [(source, items) for source, items in items_by_source.items() if items]
    top_sources = sorted(non_empty_sources, key=lambda kv: len(kv[1]), reverse=True)[:3]
    if top_sources:
        source_stat = "、".join(f"{source}({len(items)})" for source, items in top_sources)
        lines.append(f"- 主要来源分布：{source_stat}")
    lines.append("")

    lines.append("## 按来源快扫（高密度）")
    lines.append("")
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
            summary = _summary_cn(item, source, max_len=120, max_sentences=1)
            parts: list[str] = []
            if item.get("score") is not None:
                parts.append(f"兴趣分 {item.get('score')}")
            if item.get("ai_bucket"):
                parts.append(_bucket_label(str(item.get("ai_bucket"))))
            meta_suffix = f" ({'; '.join(parts)})" if parts else ""
            line = f"- [{title}]({link}){meta_suffix}"
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
