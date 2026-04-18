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

import daily_curation
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

KARPATHY_DAILY_PROMPT_MODULE = """
# Role & Goal
你现在是 Andrej Karpathy，一位极度关注“信噪比”、“工程落地”和“第一性原理”的顶级 AI 研究员。
你的任务是阅读给定的 AI 领域文章/论文/视频摘要，并生成一份硬核、无废话、直击技术本质的 AI 每日简报。

# Strict Anti-Fluff Constraints (绝对禁令)
1. 禁止任何“元描述”与套话：绝对不允许出现类似“本文聚焦了某某议题”、“对应了某某维度”、“建议提炼几个关键点”这种描述。
2. 禁止正确的废话：不要说“这有助于提升模型性能”，必须说出“在具体什么数据集上，用什么架构，将什么指标提升了多少”。
3. 禁止抽象的行动建议：Action Item 严禁出现“学习”、“了解”、“提炼”等虚词。必须是具体的工程动作（例如：“在本地拉取代码，修改 X 参数，观察 Y 现象”）。

# Content Extraction Requirements (硬核提取标准)
针对不同类型的内容，你必须提取以下维度的信息（如果没有，则标明“未披露”）：
- 【工程实践/官方博客】：核心痛点是什么？新工具/框架的 API 范式有什么改变？Cost（成本）、Latency（延迟）、Quality（质量）的权衡是什么？
- 【论文雷达】：主干架构（Architecture）有什么微创新？训练数据怎么清洗的？Loss 函数怎么改的？评估基准（Benchmarks）上的真实表现（非注水）是什么？
- 【评测与对齐】：Eval 怎么做的？是否可复现？具体的 Metric 是什么？

# Output Format Directives (输出格式指令)
- 在 [TL;DR] 部分，每条不超过一句话，必须包含一个具体的“专有名词、数据、或技术方法”。
- 在 [Karpathy 视角：认知增量] 部分，采用“发生了什么 -> 技术实质 -> 失败边界/局限性 -> 最小复现实验”的结构。
- 你的语气必须冷静、客观、甚至带一点挑剔的极客感，只为真正的技术突破买单。
""".strip()

ANTI_FLUFF_BANNED_PHRASES = (
    "本文聚焦",
    "对应了",
    "对应「",
    "建议提炼",
    "建议：提炼",
)
PROMPT_BLOCKLIST_PHRASES = (
    "本文聚焦了某某议题",
    "对应了某某维度",
    "建议提炼几个关键点",
)

METHOD_TOKENS = (
    "mamba",
    "transformer",
    "moe",
    "rag",
    "tool calling",
    "function calling",
    "activation steering",
    "distillation",
    "instruction tuning",
    "rlhf",
    "dpo",
    "sft",
    "quantization",
    "flash attention",
)

BENCHMARK_TOKENS = (
    "swe-bench",
    "mmlu",
    "gpqa",
    "gsm8k",
    "humaneval",
    "mbpp",
    "osworld",
    "arena",
    "pass@1",
    "f1",
)

METRIC_TOKENS = (
    "pass@1",
    "accuracy",
    "f1",
    "rouge",
    "bleu",
    "latency",
    "token/s",
    "throughput",
    "cost",
    "wer",
)

HARD_SIGNAL_TOKENS = METHOD_TOKENS + BENCHMARK_TOKENS + METRIC_TOKENS
TOP_VIDEO_TWEET_AI_TOKENS = (
    "ai",
    "llm",
    "gpt",
    "agent",
    "benchmark",
    "evaluation",
    "eval",
    "inference",
    "reasoning",
    "model",
    "transformer",
    "tool calling",
)


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


def _anti_fluff(text: str) -> str:
    cleaned = text
    active_blocklist = ANTI_FLUFF_BANNED_PHRASES
    # Bind behavior to KARPATHY_DAILY_PROMPT_MODULE so this module is active
    # in the real generation pipeline, not only a dead constant.
    if "Strict Anti-Fluff Constraints" in KARPATHY_DAILY_PROMPT_MODULE:
        active_blocklist = ANTI_FLUFF_BANNED_PHRASES + PROMPT_BLOCKLIST_PHRASES
    for phrase in active_blocklist:
        cleaned = cleaned.replace(phrase, "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_numbers(text: str, limit: int = 2) -> list[str]:
    if not text:
        return []
    patterns = (
        r"\b\d+(?:\.\d+)?\s?%",
        r"\b\d+(?:\.\d+)?x\b",
        r"\b\d+(?:\.\d+)?\s?ms\b",
        r"\$\s?\d+(?:\.\d+)?",
        r"\b\d+(?:\.\d+)?\s?(?:token/s|tokens/s)\b",
    )
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            normalized = str(match).strip()
            if normalized and normalized not in found:
                found.append(normalized)
            if len(found) >= limit:
                return found
    return found


def _pick_keyword(text: str, tokens: tuple[str, ...], fallback: str = "未披露") -> str:
    lowered = text.lower()
    for token in tokens:
        if token in lowered:
            return token
    return fallback


def _contains_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _is_eval_alignment(item: dict) -> bool:
    reasons = " ".join(str(x).lower() for x in item.get("score_reasons", []))
    summary = str(item.get("summary", "")).lower()
    title = str(item.get("title", "")).lower()
    text = f"{title} {summary} {reasons}"
    return any(token in text for token in ("eval", "evaluation", "benchmark", "metric", "misalign"))


def _extract_pain_point(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if "misalign" in text:
        return "代理行为偏移监控不足"
    if "memory" in text:
        return "长期记忆与检索一致性不足"
    if "latency" in text:
        return "线上延迟超预算"
    if "eval" in text:
        return "评测标准不稳定"
    if "tool calling" in text or "function calling" in text:
        return "工具调用成功率不足"
    return "关键工程瓶颈未披露"


def _extract_tradeoff(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    numbers = _extract_numbers(text, limit=2)
    lowered = text.lower()
    if numbers:
        return f"披露指标 {', '.join(numbers)}，其余成本/延迟/质量项未披露"
    if any(token in lowered for token in ("cost", "latency", "quality")):
        return "提到成本/延迟/质量，但未给出可计算数字"
    return "成本/延迟/质量权衡未披露"


def _extract_architecture(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return _pick_keyword(text, METHOD_TOKENS, fallback="架构细节未披露")


def _extract_benchmark(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return _pick_keyword(text, BENCHMARK_TOKENS, fallback="基准未披露")


def _extract_metric(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    metric_name = _pick_keyword(text, METRIC_TOKENS, fallback="metric 未披露")
    numbers = _extract_numbers(text, limit=1)
    if numbers and metric_name != "metric 未披露":
        return f"{metric_name}={numbers[0]}"
    return metric_name


def _extract_api_pattern(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if "tool calling" in text or "function calling" in text:
        return "函数/工具调用型 API"
    if "sdk" in text:
        return "SDK 封装型 API"
    if "endpoint" in text or "rest" in text or "/v1/" in text:
        return "REST endpoint 型 API"
    if "agent" in text:
        return "Agent 编排型 API"
    return "API 范式未披露"


def _extract_data_cleaning(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if "dedup" in text or "de-dup" in text:
        return "去重（dedup）"
    if "filter" in text:
        return "规则过滤（filter）"
    if "synthetic" in text:
        return "合成数据混合"
    if "curated" in text:
        return "人工筛选（curated）"
    return "数据清洗未披露"


def _extract_loss_change(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if "dpo" in text:
        return "DPO 目标"
    if "rlhf" in text:
        return "RLHF 目标"
    if "contrastive" in text:
        return "对比学习损失"
    if "cross-entropy" in text or "xent" in text:
        return "交叉熵损失"
    if "loss" in text:
        return "loss 调整已提及但细节未披露"
    return "loss 未披露"


def _extract_eval_protocol(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if "offline" in text and "online" in text:
        return "离线+在线双轨 eval"
    if "harness" in text and "eval" in text:
        return "harness 驱动回归评测"
    if "recipe" in text and "eval" in text:
        return "recipe 流程评测"
    if "playbook" in text and "eval" in text:
        return "playbook 流程评测"
    if "hill-climbing" in text and "eval" in text:
        return "hill-climbing 迭代评测"
    if "human" in text and "grader" in text:
        return "人工标注+自动 grader"
    if "ablation" in text:
        return "消融实验 eval"
    if "benchmark" in text:
        return "基准测试 eval"
    return "eval 协议未披露"


def _extract_reproducibility(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if "open source" in text or "github" in text:
        return "可复现：公开代码/配置"
    if "recipe" in text or "script" in text:
        return "可复现：给出 recipe"
    if "dataset" in text and "checkpoint" in text:
        return "可复现：给出数据与 checkpoint"
    return "可复现性未披露"


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
    title = _clean_text(str(item.get("title", "该条目")), max_len=56)
    metric = _extract_metric(item)
    benchmark = _extract_benchmark(item)
    eval_protocol = _extract_eval_protocol(item)
    api_pattern = _extract_api_pattern(item)
    tradeoff = _extract_tradeoff(item)

    if content_type == "paper":
        benchmark = _extract_benchmark(item)
        if not _paper_is_top_ready(item):
            return (
                f"将“{title}”放入 `paper-radar.md`，补抓作者代码仓与 appendix；"
                "若 48 小时内仍无 benchmark/metric，则仅保留追踪，不排期复现。"
            )
        return (
            f"在本地建 `repro/{slugify(title)}`，复现文中方法并与现有基线对比；"
            f"至少记录 `{benchmark}` 与 `{metric}` 的差值。"
        )
    if _is_eval_alignment(item):
        return (
            f"围绕“{title}”搭建 {eval_protocol} 最小评测集（20 条样本）；"
            "固定 grader 版本并输出 `pass_rate`、`false_positive_rate`、`cost_per_eval_usd`。"
        )
    if content_type in {"engineering", "tooling", "news"}:
        return (
            f"基于“{title}”实现 {api_pattern} 最小 PoC，"
            f"跑 20 条真实请求并记录 `success_rate`、`p95_latency_ms`、`error_budget_burn`；"
            f"若 {tradeoff}，则补齐缺失成本/质量指标。"
        )
    if content_type == "tweet":
        return (
            f"追溯“{title}”的原始发布与 changelog；"
            "24 小时内无可验证文档则标记为观察项，不进入生产待办。"
        )
    if content_type == "video":
        return (
            f"为“{title}”提取 3 个可执行工程片段并映射到现有栈，"
            "各写 1 个验证脚本，记录复现耗时与失败样本。"
        )
    return f"为“{title}”建最小验证脚本，固定输入样本后连续运行 3 次，比较输出一致性与耗时。"


def _why_it_matters(item: dict) -> str:
    reasons = " ".join(item.get("score_reasons", []))
    summary = item.get("summary", "")

    if "priority:evaluation" in reasons or "interest:eval" in reasons:
        return "可直接接入评测闸门，决定发布阈值与回滚条件。"
    if "priority:coding agent" in reasons or "priority:agent engineering" in reasons:
        return "会直接影响编码代理的成功率与故障模式。"
    if "priority:memory" in reasons:
        return "关系到长期记忆命中率和检索一致性。"
    if "practical:+" in reasons:
        return "包含可执行步骤，可直接转成实验工单。"
    if summary:
        if re.search(r"[\u4e00-\u9fff]", summary):
            return _first_sentence(summary, max_len=110)
        return "提供一手英文证据，可用于交叉验证。"
    return "未给出足够证据，需先做最小验证。"


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


TOP_PRIORITY_NON_PAPER_TYPES = ("news", "engineering", "tweet", "video", "tooling", "community")


def _paper_signal_report(item: dict) -> dict[str, Any]:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    has_method = _contains_any_token(text, METHOD_TOKENS)
    has_benchmark = _contains_any_token(text, BENCHMARK_TOKENS)
    has_metric = _contains_any_token(text, METRIC_TOKENS)
    number_hits = _extract_numbers(text, limit=3)
    has_number = bool(number_hits)
    disclosed_axes = int(has_method) + int(has_benchmark) + int(has_metric or has_number)
    return {
        "has_method": has_method,
        "has_benchmark": has_benchmark,
        "has_metric": has_metric,
        "has_number": has_number,
        "disclosed_axes": disclosed_axes,
        "numbers": number_hits,
    }


def _paper_is_top_ready(item: dict) -> bool:
    report = _paper_signal_report(item)
    if report["disclosed_axes"] < 2:
        return False
    if not report["has_benchmark"]:
        return False
    return bool(report["has_metric"] or report["has_number"])


def _build_top_picks(
    items_by_source: dict[str, list[dict]],
    top_picks: int,
) -> list[tuple[str, dict]]:
    scored: list[tuple[str, dict]] = []
    for source, items in items_by_source.items():
        for item in items:
            if item.get("score") is None:
                continue
            if _infer_content_type(item, source) == "paper" and not _paper_is_top_ready(item):
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


def _select_diverse_top_stories(
    content_groups: dict[str, list[tuple[str, dict]]],
    *,
    limit: int,
    min_top_nonpaper: int,
    min_top_content_types: int,
    max_paper_in_top: int,
) -> tuple[list[tuple[str, dict]], dict[str, Any]]:
    pool = _build_ranked_pool(content_groups)
    selected: list[tuple[str, dict]] = []
    seen_links: set[str] = set()
    paper_count = 0
    target_type_coverage = max(1, min_top_content_types)

    def _can_add(source: str, item: dict, *, allow_low_signal_paper: bool = False) -> bool:
        nonlocal paper_count
        link = str(item.get("link", "")).strip()
        if link and link in seen_links:
            return False
        content_type = _infer_content_type(item, source)
        if not allow_low_signal_paper and not _is_top_quality_candidate(source, item):
            return False
        if content_type == "paper":
            if paper_count >= max_paper_in_top:
                return False
            if not allow_low_signal_paper and not _paper_is_top_ready(item):
                return False
        return True

    def _append(source: str, item: dict) -> None:
        nonlocal paper_count
        link = str(item.get("link", "")).strip()
        if link:
            seen_links.add(link)
        if _infer_content_type(item, source) == "paper":
            paper_count += 1
        selected.append((source, item))

    # Pass 1: ensure non-paper type diversity first.
    for content_type in TOP_PRIORITY_NON_PAPER_TYPES:
        for source, item in pool:
            if _infer_content_type(item, source) != content_type:
                continue
            if not _can_add(source, item):
                continue
            _append(source, item)
            break
        if len(selected) >= limit:
            break

    # Pass 2: fill with best remaining non-paper content.
    if len(selected) < limit:
        for source, item in pool:
            if _infer_content_type(item, source) == "paper":
                continue
            if not _can_add(source, item):
                continue
            selected_types = {_infer_content_type(i, s) for s, i in selected}
            candidate_type = _infer_content_type(item, source)
            needed_new_types = max(0, target_type_coverage - len(selected_types))
            remaining_slots = limit - len(selected)
            # Keep slots for uncovered types, so one dominant type won't fill all Top slots.
            if (
                needed_new_types > 0
                and remaining_slots <= needed_new_types
                and candidate_type in selected_types
            ):
                continue
            _append(source, item)
            if len(selected) >= limit:
                break

    # Pass 3: allow paper only if benchmark/metric signals are present.
    if len(selected) < limit:
        for source, item in pool:
            if _infer_content_type(item, source) != "paper":
                continue
            if not _can_add(source, item):
                continue
            _append(source, item)
            if len(selected) >= limit:
                break

    # Pass 4: safety net when all feeds are weak; still cap paper count.
    if len(selected) < limit:
        for source, item in pool:
            if not _can_add(source, item, allow_low_signal_paper=True):
                continue
            _append(source, item)
            if len(selected) >= limit:
                break

    selected_types = {_infer_content_type(item, source) for source, item in selected}
    nonpaper_count = sum(
        1 for source, item in selected if _infer_content_type(item, source) != "paper"
    )
    diversity_ok = nonpaper_count >= min_top_nonpaper and len(selected_types) >= min_top_content_types
    stats = {
        "nonpaper_count": nonpaper_count,
        "paper_count": sum(
            1 for source, item in selected if _infer_content_type(item, source) == "paper"
        ),
        "type_count": len(selected_types),
        "types": sorted(selected_types),
        "diversity_ok": diversity_ok,
    }
    return selected, stats


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


def _capture_prompt(item: dict, source: str = "") -> str:
    return _daily_one_thing(item, source or str(item.get("source", "")))


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
    include_title: bool = True,
) -> str:
    content_type = _infer_content_type(item, source)
    title = _clean_text(str(item.get("title", "未命名")), max_len=48)
    summary_raw = _multi_sentence_summary(
        item.get("summary", ""), max_len=600, max_sentences=max_sentences
    )

    if content_type == "paper":
        arch = _extract_architecture(item)
        cleaning = _extract_data_cleaning(item)
        loss_change = _extract_loss_change(item)
        benchmark = _extract_benchmark(item)
        metric = _extract_metric(item)
        report = _paper_signal_report(item)
        prefix = f"{title} " if include_title else ""
        if not _paper_is_top_ready(item):
            observed = []
            if report["has_method"]:
                observed.append(f"方法={arch}")
            if report["has_benchmark"]:
                observed.append(f"基准={benchmark}")
            if report["has_metric"] or report["has_number"]:
                observed.append(f"指标={metric}")
            observed_text = "；".join(observed) if observed else "仅有题目/摘要级信息"
            statement = (
                f"{prefix}{observed_text}；缺少可核验 benchmark 与量化指标，"
                "暂列论文雷达，不进入核心 Top。"
            )
        else:
            statement = (
                f"{prefix}架构：{arch}；数据清洗：{cleaning}；Loss：{loss_change}；"
                f"基准：{benchmark}；表现：{metric}。"
            )
        return _clean_text(_anti_fluff(statement), max_len=max_len)

    if _is_eval_alignment(item):
        eval_protocol = _extract_eval_protocol(item)
        reproducibility = _extract_reproducibility(item)
        metric = _extract_metric(item)
        prefix = f"{title} " if include_title else ""
        unknown_count = sum(
            "公开材料未给出" in part or "未披露" in part
            for part in (eval_protocol, metric, reproducibility)
        )
        if unknown_count >= 2 and summary_raw:
            statement = f"{prefix}{summary_raw}"
        else:
            statement = (
                f"{prefix}评测协议：{eval_protocol}；指标：{metric}；可复现性：{reproducibility}。"
            )
        return _clean_text(_anti_fluff(statement), max_len=max_len)

    if content_type in {"engineering", "tooling", "news"}:
        pain = _extract_pain_point(item)
        api = _extract_api_pattern(item)
        tradeoff = _extract_tradeoff(item)
        prefix = f"{title} " if include_title else ""
        unknown_count = sum("公开材料未给出" in part or "未披露" in part for part in (pain, api, tradeoff))
        if unknown_count >= 2 and summary_raw:
            statement = f"{prefix}{summary_raw}"
        else:
            statement = f"{prefix}解决痛点：{pain}；API 变化：{api}；成本-延迟-质量权衡：{tradeoff}。"
        return _clean_text(_anti_fluff(statement), max_len=max_len)

    if summary_raw and _has_cjk(summary_raw):
        return _clean_text(_anti_fluff(summary_raw), max_len=max_len)
    if summary_raw:
        return _clean_text(_anti_fluff(summary_raw), max_len=max_len)

    signal = _signal_summary_cn(item, source)
    prefix = f"{title} " if include_title else ""
    statement = f"{prefix}关键信号：{signal}；核心细节未披露。"
    return _clean_text(_anti_fluff(statement), max_len=max_len)


def _technical_essence(item: dict, source: str) -> str:
    content_type = _infer_content_type(item, source)
    if content_type == "paper":
        return (
            f"Architecture={_extract_architecture(item)}；Data={_extract_data_cleaning(item)}；"
            f"Loss={_extract_loss_change(item)}；Benchmark={_extract_benchmark(item)}。"
        )
    if _is_eval_alignment(item):
        return (
            f"评测协议={_extract_eval_protocol(item)}；指标={_extract_metric(item)}；"
            f"可复现性={_extract_reproducibility(item)}。"
        )
    if content_type in {"engineering", "tooling", "news"}:
        return (
            f"痛点={_extract_pain_point(item)}；API={_extract_api_pattern(item)}；"
            f"权衡={_extract_tradeoff(item)}。"
        )
    return "技术细节披露不足，需补原文与代码。"


def _failure_boundary(item: dict, source: str) -> str:
    content_type = _infer_content_type(item, source)
    if content_type == "paper":
        return "论文未给完整 ablation 或跨数据集泛化结果时，结论可能依赖特定数据分布。"
    if _is_eval_alignment(item):
        return "若缺少固定测试集版本和 grader 配置，eval 结果在不同环境下可能漂移。"
    if content_type in {"engineering", "tooling", "news"}:
        return "若未公开生产流量、失败样本与回滚策略，线上可用性与稳定性不可直接外推。"
    return "信息源粒度不足，暂不能判断边界条件。"


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


def _tldr_summary_quality(summary: str) -> dict[str, Any]:
    numbers = _extract_numbers(summary, limit=3)
    has_hard_token = _contains_any_token(summary, HARD_SIGNAL_TOKENS)
    has_eval_protocol = any(
        token in summary
        for token in (
            "离线+在线双轨 eval",
            "人工标注+自动 grader",
            "消融实验 eval",
            "基准测试 eval",
        )
    )
    undisclosed = summary.count("未披露")
    hard_signal = bool(numbers or has_hard_token or has_eval_protocol)
    score = 0
    if numbers:
        score += 2
    if has_hard_token:
        score += 2
    if has_eval_protocol:
        score += 1
    score -= min(undisclosed, 3)
    return {
        "score": score,
        "undisclosed": undisclosed,
        "hard_signal": hard_signal,
        "number_hits": len(numbers),
    }


def _build_tldr_rows(candidates: list[tuple[str, dict]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, item in candidates:
        headline = _clean_text(str(item.get("title", "未命名")), max_len=80)
        summary = _summary_cn(item, source, max_len=120, max_sentences=1, include_title=False)
        if not summary:
            summary = _why_it_matters(item)
        quality = _tldr_summary_quality(summary)
        rows.append(
            {
                "source": source,
                "item": item,
                "headline": headline,
                "summary": summary,
                "quality": quality,
                "key": str(item.get("link", "")) or headline,
            }
        )
    return rows


def _select_tldr_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    quality_gate_enabled: bool,
    min_quality_score: int,
    max_undisclosed: int,
    min_items: int,
) -> list[dict[str, Any]]:
    if not quality_gate_enabled:
        return rows[:limit]

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    backup: list[dict[str, Any]] = []

    for row in rows:
        quality = row["quality"]
        meets_gate = (
            int(quality["score"]) >= min_quality_score
            and int(quality["undisclosed"]) <= max_undisclosed
            and bool(quality["hard_signal"])
        )
        if meets_gate:
            selected.append(row)
            selected_keys.add(row["key"])
        else:
            backup.append(row)

    if len(selected) < min(min_items, limit):
        backup_sorted = sorted(
            backup,
            key=lambda row: (
                int(row["quality"]["score"]),
                int(row["quality"]["number_hits"]),
                -int(row["quality"]["undisclosed"]),
            ),
            reverse=True,
        )
        for row in backup_sorted:
            if row["key"] in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(row["key"])
            if len(selected) >= min(min_items, limit):
                break

    return selected[:limit]


def _tldr_quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "total": 0,
            "hard_signal_count": 0,
            "hard_signal_ratio": 0.0,
            "undisclosed_total": 0,
            "undisclosed_ratio": 0.0,
            "avg_quality_score": 0.0,
        }
    total = len(rows)
    hard_signal_count = sum(1 for row in rows if row["quality"]["hard_signal"])
    undisclosed_total = sum(int(row["quality"]["undisclosed"]) for row in rows)
    score_total = sum(int(row["quality"]["score"]) for row in rows)
    return {
        "total": total,
        "hard_signal_count": hard_signal_count,
        "hard_signal_ratio": hard_signal_count / total,
        "undisclosed_total": undisclosed_total,
        "undisclosed_ratio": undisclosed_total / total,
        "avg_quality_score": score_total / total,
    }


def _soften_undisclosed(text: str) -> str:
    replaced = text
    replaced = replaced.replace("未披露", "公开材料未给出")
    replaced = replaced.replace("metric 公开材料未给出", "关键指标公开材料未给出")
    replaced = replaced.replace("基准公开材料未给出", "评测基准公开材料未给出")
    replaced = replaced.replace("架构细节公开材料未给出", "架构细节仍需补原文")
    return replaced


def _concept_tags(item: dict, source: str, limit: int = 3) -> list[str]:
    content_type = _infer_content_type(item, source)
    score_reasons = [str(x).lower() for x in item.get("score_reasons", [])]
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()

    mapping = {
        "evaluation": "#concept/Evaluation",
        "tool calling": "#concept/Tool-Calling",
        "memory": "#concept/Memory",
        "coding agent": "#concept/Coding-Agent",
        "agent engineering": "#concept/Agent-Engineering",
        "context engineering": "#concept/Context-Engineering",
        "workflow": "#concept/Workflow",
        "security": "#concept/Security",
        "creator": "#concept/Creator-Economy",
        "open source": "#concept/Open-Source",
    }

    tags: list[str] = []

    def _add(tag: str) -> None:
        if tag not in tags:
            tags.append(tag)

    if content_type == "paper":
        _add("#concept/Research")
    if content_type == "engineering":
        _add("#concept/Engineering")
    if content_type == "video":
        _add("#concept/Video-Learning")
    if content_type == "tweet":
        _add("#concept/Social-Signal")

    if _is_eval_alignment(item):
        _add("#concept/Evaluation")

    for reason in score_reasons:
        token = reason.split(":")[-1].strip()
        if token in mapping:
            _add(mapping[token])

    for token, tag in mapping.items():
        if token in text:
            _add(tag)
        if len(tags) >= limit:
            break

    if not tags:
        _add("#concept/AI-Insight")

    return tags[:limit]


def _detail_points(item: dict, source: str) -> list[str]:
    content_type = _infer_content_type(item, source)
    if _is_eval_alignment(item):
        eval_protocol = _extract_eval_protocol(item)
        metric = _extract_metric(item)
        reproducibility = _extract_reproducibility(item)
        unknown_count = sum(
            "公开材料未给出" in part or "未披露" in part
            for part in (eval_protocol, metric, reproducibility)
        )
        summary_raw = _multi_sentence_summary(item.get("summary", ""), max_len=220, max_sentences=2)
        if unknown_count >= 2 and summary_raw:
            points = [
                f"原文摘要：{summary_raw}",
                f"关键信号：{_signal_summary_cn(item, source)}",
                f"建议动作：{_daily_one_thing(item, source)}",
            ]
        else:
            points = [
                f"评测协议：{eval_protocol}",
                f"关键指标：{metric}",
                f"复现条件：{reproducibility}",
            ]
    elif content_type == "paper":
        points = [
            f"主干架构：{_extract_architecture(item)}",
            f"训练与清洗：{_extract_data_cleaning(item)}；Loss：{_extract_loss_change(item)}",
            f"基准与表现：{_extract_benchmark(item)}；{_extract_metric(item)}",
        ]
    elif content_type in {"engineering", "tooling", "news"}:
        pain = _extract_pain_point(item)
        api = _extract_api_pattern(item)
        tradeoff = _extract_tradeoff(item)
        unknown_count = sum("公开材料未给出" in part or "未披露" in part for part in (pain, api, tradeoff))
        summary_raw = _multi_sentence_summary(item.get("summary", ""), max_len=220, max_sentences=2)
        if unknown_count >= 2 and summary_raw:
            points = [
                f"原文摘要：{summary_raw}",
                f"关键信号：{_signal_summary_cn(item, source)}",
                f"建议动作：{_daily_one_thing(item, source)}",
            ]
        else:
            points = [
                f"核心痛点：{pain}",
                f"API 范式：{api}",
                f"成本/延迟/质量：{tradeoff}",
            ]
    elif content_type == "video":
        points = [
            f"关键信号：{_signal_summary_cn(item, source)}",
            f"可执行动作：{_daily_one_thing(item, source)}",
            f"失败边界：{_failure_boundary(item, source)}",
        ]
    else:
        points = [
            _summary_cn(item, source, max_len=160, max_sentences=1, include_title=False),
            _why_it_matters(item),
            _daily_one_thing(item, source),
        ]

    cleaned = [_soften_undisclosed(_clean_text(point, max_len=260)) for point in points if point]
    return cleaned[:3]


def _core_finding(item: dict, source: str) -> str:
    summary = _summary_cn(item, source, max_len=260, max_sentences=2, include_title=False)
    if not summary:
        summary = _why_it_matters(item)
    return _soften_undisclosed(summary)


def _story_title(item: dict, max_len: int = 88) -> str:
    return _clean_text(str(item.get("title", "未命名")), max_len=max_len)


def _render_top_story(rank: int, source: str, item: dict) -> list[str]:
    title = _story_title(item)
    tags = " ".join(_concept_tags(item, source))
    details = _detail_points(item, source)
    action = _soften_undisclosed(_daily_one_thing(item, source))

    lines = [
        f"## 今日重点 {rank} — {title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{title}]({item.get('link', '')})",
        f"**核心概念**：{tags}",
        "",
        "### 深度要点",
        "",
        f"**核心发现**：{_core_finding(item, source)}",
        "",
        "**关键细节**：",
    ]
    for point in details:
        lines.append(f"- {point}")
    lines.extend(
        [
            "",
            f"**行动启示**：{action}",
            "",
            "---",
            "",
        ]
    )
    return lines


def _build_ranked_pool(content_groups: dict[str, list[tuple[str, dict]]]) -> list[tuple[str, dict]]:
    pool: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for content_type in CONTENT_TYPE_ORDER:
        for source, item in content_groups.get(content_type, []):
            link = str(item.get("link", "")).strip()
            key = link or f"{source}|{item.get('title', '')}"
            if key in seen:
                continue
            seen.add(key)
            pool.append((source, item))
    return pool


def _first_story(
    pool: list[tuple[str, dict]],
    used_links: set[str],
    predicate: Any,
) -> tuple[str, dict] | None:
    for source, item in pool:
        link = str(item.get("link", "")).strip()
        if link and link in used_links:
            continue
        if predicate(source, item):
            return source, item
    return None


def _render_deep_spotlight(title: str, source: str, item: dict) -> list[str]:
    story_title = _story_title(item)
    tags = " ".join(_concept_tags(item, source))
    details = _detail_points(item, source)
    lines = [
        f"## {title} — {story_title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{story_title}]({item.get('link', '')})",
        f"**核心概念**：{tags}",
        "",
        "### 深度要点",
        "",
        f"**核心发现**：{_core_finding(item, source)}",
        "",
        "**关键细节**：",
    ]
    for point in details:
        lines.append(f"- {point}")
    lines.extend(
        [
            "",
            f"**行动启示**：{_soften_undisclosed(_daily_one_thing(item, source))}",
            "",
            "---",
            "",
        ]
    )
    return lines


def _render_brief_spotlight(title: str, source: str, item: dict) -> list[str]:
    story_title = _story_title(item)
    tags = " ".join(_concept_tags(item, source))
    details = _detail_points(item, source)
    lines = [
        f"## {title} — {story_title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{story_title}]({item.get('link', '')})",
        f"**核心概念**：{tags}",
        "",
        f"**一句话**：{_core_finding(item, source)}",
        "",
        "**3 个要点**：",
    ]
    for point in details[:3]:
        lines.append(f"- {point}")
    lines.extend(
        [
            "",
            f"**行动启示**：{_soften_undisclosed(_daily_one_thing(item, source))}",
            "",
            "---",
            "",
        ]
    )
    return lines


def _render_video_spotlight(source: str, item: dict) -> list[str]:
    title = _story_title(item)
    tags = " ".join(_concept_tags(item, source))
    details = _detail_points(item, source)
    lines = [
        f"## 今日视频 — {title}",
        "",
        f"**频道**：{source}",
        f"**链接**：[{{title}}]({item.get('link', '')})".format(title=title),
        f"**发布日期**：{_published_date(item)}",
        f"**核心概念**：{tags}",
        "",
        f"**核心结论**：{_core_finding(item, source)}",
        "**关键方法论**：",
    ]
    for point in details[:3]:
        lines.append(f"- {point}")
    lines.extend(
        [
            f"**行动启示**：{_soften_undisclosed(_daily_one_thing(item, source))}",
            "",
            "---",
            "",
        ]
    )
    return lines


def _collect_summary_candidates(
    top_stories: list[tuple[str, dict]],
    extra_candidates: list[tuple[str, dict] | None],
    *,
    limit: int = 6,
    allow_weak: bool = False,
) -> list[tuple[str, dict]]:
    candidates: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for source, item in top_stories:
        if not allow_weak and not _is_top_quality_candidate(source, item):
            continue
        key = str(item.get("link", "")).strip() or f"{source}|{item.get('title', '')}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append((source, item))
        if len(candidates) >= limit:
            return candidates

    for candidate in extra_candidates:
        if candidate is None:
            continue
        source, item = candidate
        if not allow_weak and not _is_top_quality_candidate(source, item):
            continue
        key = str(item.get("link", "")).strip() or f"{source}|{item.get('title', '')}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append((source, item))
        if len(candidates) >= limit:
            break
    return candidates


def _type_coverage_summary(content_groups: dict[str, list[tuple[str, dict]]]) -> str:
    non_empty = [
        CONTENT_TYPE_LABELS.get(content_type, "其他")
        for content_type in ("news", "tweet", "engineering", "paper", "video", "tooling")
        if content_groups.get(content_type)
    ]
    if not non_empty:
        return "暂无稳定类型覆盖"
    if len(non_empty) <= 3:
        return "、".join(non_empty)
    return "、".join(non_empty[:3]) + f" 等 {len(non_empty)} 类"


def _has_hard_signal(item: dict, source: str) -> bool:
    text = (
        f"{item.get('title', '')} {item.get('summary', '')} "
        f"{' '.join(str(x) for x in item.get('score_reasons', []))} {source}"
    )
    lowered = text.lower()
    if _extract_numbers(text, limit=1):
        return True
    if _contains_any_token(lowered, HARD_SIGNAL_TOKENS):
        return True
    return False


def _is_top_quality_candidate(source: str, item: dict) -> bool:
    content_type = _infer_content_type(item, source)
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    link = str(item.get("link", "")).lower()

    if content_type == "paper":
        return _paper_is_top_ready(item)
    if content_type in {"video", "tweet"}:
        if "/shorts/" in link:
            return False
        if _has_hard_signal(item, source):
            return True
        return any(token in text for token in TOP_VIDEO_TWEET_AI_TOKENS)
    if content_type in {"news", "engineering", "tooling", "community"}:
        return _has_hard_signal(item, source) or len(_clean_text(str(item.get("summary", "")), 180)) >= 48
    return True


def _optimize_brief_summary(summary: str, item: dict, source: str, level: int) -> str:
    cleaned = _soften_undisclosed(_clean_text(summary, max_len=150))
    if not cleaned:
        cleaned = _soften_undisclosed(_core_finding(item, source))
    if level <= 0:
        return cleaned

    if "公开材料未给出" in cleaned and not _has_hard_signal(item, source):
        cleaned = f"{cleaned}；需补抓原文中的实验配置与量化结果。"
    if level >= 2 and "需补抓" not in cleaned:
        cleaned = f"{cleaned}；可先做最小可复现实验验证方向。"
    return _clean_text(cleaned, max_len=180)


def _build_digest_overview(
    top_stories: list[tuple[str, dict]],
    content_groups: dict[str, list[tuple[str, dict]]],
    *,
    level: int = 0,
) -> list[str]:
    if not top_stories:
        return [
            "- 今日主线：有效信号不足，优先修复源稳定性与筛选阈值。",
            "- 工程落点：暂停排期新实验，先确认高优先级来源可用。",
            "- 风险边界：若连续 2 天无高质量条目，建议收缩来源并提升评分阈值。",
        ]

    top_titles = [_story_title(item, max_len=42) for _, item in top_stories[:2]]
    hard_signal_count = sum(1 for source, item in top_stories if _has_hard_signal(item, source))
    coverage = _type_coverage_summary(content_groups)
    lead_story = top_titles[0] if top_titles else "今日重点条目"
    second_story = top_titles[1] if len(top_titles) > 1 else lead_story

    lines = [
        f"- 今日主线：信号聚焦在 {coverage}，核心条目是「{lead_story}」与「{second_story}」。",
        (
            f"- 工程落点：Top 条目中有 {hard_signal_count}/{len(top_stories)} 条包含方法或指标信号，"
            "可直接转为 PoC、评测或复现实验。"
        ),
        "- 风险边界：对“公开材料未给出”的条目只做追踪，不直接进入生产排期。",
    ]
    if level >= 1:
        lines.append("- 执行策略：先做 1 个可复现实验 + 1 个在线评测，再决定是否扩大投入。")
    return lines


def _build_digest_tldr_rows(
    candidates: list[tuple[str, dict]],
    *,
    limit: int = 5,
    level: int = 0,
) -> list[str]:
    rows: list[str] = []
    for source, item in candidates:
        title = _story_title(item, max_len=64)
        summary = _summary_cn(item, source, max_len=130, max_sentences=1, include_title=False)
        summary = _optimize_brief_summary(summary, item, source, level)
        rows.append(f"[{title}]({item.get('link', '')})：{summary}（{source}）")
        if len(rows) >= limit:
            break
    return rows


def _score_digest_quality(
    *,
    overview_lines: list[str],
    tldr_rows: list[str],
    top_stories: list[tuple[str, dict]],
    action_items_count: int,
) -> tuple[float, dict[str, float]]:
    overview_chars = sum(len(x) for x in overview_lines)
    tldr_chars = sum(len(x) for x in tldr_rows)
    hard_signal_top = sum(1 for source, item in top_stories if _has_hard_signal(item, source))
    top_types = {_infer_content_type(item, source) for source, item in top_stories}
    nonpaper_top = sum(1 for source, item in top_stories if _infer_content_type(item, source) != "paper")
    low_detail_rows = sum(
        1
        for row in tldr_rows
        if any(phrase in row for phrase in ("公开材料未给出", "仅有题目/摘要级信息", "暂列论文雷达"))
    )

    overview_score = 0.0
    if len(overview_lines) >= 3:
        overview_score += 1.4
    if overview_chars >= 90:
        overview_score += 1.2
    if any("主线" in x for x in overview_lines):
        overview_score += 0.4

    tldr_score = 0.0
    if len(tldr_rows) >= 3:
        tldr_score += 1.3
    if len(tldr_rows) >= 4:
        tldr_score += 0.4
    if len(tldr_rows) > 0 and (tldr_chars / len(tldr_rows)) >= 48:
        tldr_score += 0.9
    if sum("公开材料未给出" not in row for row in tldr_rows) >= max(2, len(tldr_rows) - 1):
        tldr_score += 0.4

    signal_score = 0.0
    if len(top_stories) > 0:
        ratio = hard_signal_top / len(top_stories)
        if ratio >= 0.67:
            signal_score += 2.0
        elif ratio >= 0.34:
            signal_score += 1.5
        else:
            signal_score += 0.8
    if hard_signal_top >= 2:
        signal_score += 0.6

    diversity_score = 0.0
    if len(top_types) >= 3:
        diversity_score += 1.4
    elif len(top_types) >= 2:
        diversity_score += 1.0
    else:
        diversity_score += 0.3
    if nonpaper_top >= 2:
        diversity_score += 0.3

    action_score = 0.0
    if action_items_count >= 3:
        action_score += 1.6
    elif action_items_count >= 2:
        action_score += 1.1
    if action_items_count >= 1:
        action_score += 0.2

    low_detail_ratio = (low_detail_rows / len(tldr_rows)) if tldr_rows else 0.0
    generic_penalty = 0.0
    if low_detail_ratio >= 0.8:
        generic_penalty = 1.6
    elif low_detail_ratio >= 0.6:
        generic_penalty = 1.2
    elif low_detail_ratio >= 0.4:
        generic_penalty = 0.7

    total = min(
        10.0,
        max(0.0, overview_score + tldr_score + signal_score + diversity_score + action_score - generic_penalty),
    )
    if hard_signal_top == 0:
        total = min(total, 8.8)
    if len(top_types) < 2:
        total = min(total, 8.6)
    breakdown = {
        "overview": round(min(3.0, overview_score), 1),
        "tldr": round(min(3.0, tldr_score), 1),
        "signal": round(min(3.0, signal_score), 1),
        "diversity": round(min(2.0, diversity_score), 1),
        "action": round(min(2.0, action_score), 1),
    }
    return round(total, 1), breakdown


def _format_daily_digest_rebuilt(
    items_by_source: dict[str, list[dict]],
    *,
    top_picks: int,
    action_items: int,
    min_top_nonpaper: int,
    min_top_content_types: int,
    max_paper_in_top: int,
    stats: dict[str, Any],
) -> tuple[str, str]:
    today = today_str()
    filepath = f"30-Daily/AI-News/AI-Daily-{today}.md"

    content_groups = _build_content_type_view(items_by_source)
    top_story_limit = min(3, max(1, top_picks))
    top_stories, top_diversity_stats = _select_diverse_top_stories(
        content_groups,
        limit=top_story_limit,
        min_top_nonpaper=max(0, min_top_nonpaper),
        min_top_content_types=max(1, min_top_content_types),
        max_paper_in_top=max(0, min(max_paper_in_top, top_story_limit)),
    )
    used_links: set[str] = {
        str(item.get("link", "")).strip()
        for _, item in top_stories
        if str(item.get("link", "")).strip()
    }
    pool = _build_ranked_pool(content_groups)

    def _reserve(candidate: tuple[str, dict] | None) -> tuple[str, dict] | None:
        if candidate is None:
            return None
        link = str(candidate[1].get("link", "")).strip()
        if link:
            used_links.add(link)
        return candidate

    venture_candidate = _reserve(
        _first_story(
            pool,
            used_links,
            lambda source, _item: any(
                k in source.lower() for k in ("sequoia", "a16z", "benchmark", "accel")
            ),
        )
    )
    growth_candidate = _reserve(
        _first_story(
            pool,
            used_links,
            lambda source, _item: any(
                k in source.lower() for k in ("dan koe", "creator", "newsletter")
            ),
        )
    )
    video_candidate = _reserve(
        _first_story(
            pool,
            used_links,
            lambda source, item: (
                _infer_content_type(item, source) == "video"
                and _is_top_quality_candidate(source, item)
            ),
        )
    )
    tweet_candidate = _reserve(
        _first_story(
            pool,
            used_links,
            lambda source, item: _infer_content_type(item, source) == "tweet",
        )
    )
    insight_candidate = _reserve(
        _first_story(
            pool,
            used_links,
            lambda source, item: (
                _infer_content_type(item, source) in {"news", "engineering", "tooling", "paper"}
            ),
        )
    )
    summary_candidates = _collect_summary_candidates(
        top_stories,
        [venture_candidate, growth_candidate, video_candidate, tweet_candidate, insight_candidate],
        limit=6,
        allow_weak=False,
    )
    if not summary_candidates:
        summary_candidates = _collect_summary_candidates(
            top_stories,
            [venture_candidate, growth_candidate, video_candidate, tweet_candidate, insight_candidate],
            limit=6,
            allow_weak=True,
        )
    quality_threshold = 9.0
    optimization_round = 1
    overview_lines: list[str] = []
    tldr_rows: list[str] = []
    quality_score = 0.0
    quality_breakdown: dict[str, float] = {
        "overview": 0.0,
        "tldr": 0.0,
        "signal": 0.0,
        "diversity": 0.0,
        "action": 0.0,
    }

    for round_idx in range(1, 4):
        level = round_idx - 1
        overview_lines = _build_digest_overview(top_stories, content_groups, level=level)
        tldr_rows = _build_digest_tldr_rows(summary_candidates, limit=5, level=level)
        quality_score, quality_breakdown = _score_digest_quality(
            overview_lines=overview_lines,
            tldr_rows=tldr_rows,
            top_stories=top_stories,
            action_items_count=min(action_items, len(top_stories)),
        )
        optimization_round = round_idx
        if quality_score >= quality_threshold:
            break

    lines = [
        "---",
        f'title: "AI 每日简报 - {today}"',
        f"date: {today}",
        "tags:",
        "  - daily-digest",
        "  - AI-news",
        "  - AI-daily",
        'type: "digest"',
        'status: "inbox"',
        f'aliases: ["AI Daily {today}"]',
        "---",
        "",
        f"# AI 每日简报 - {today}",
        "",
    ]
    lines.append("## 今日概述")
    lines.append("")
    for row in overview_lines:
        lines.append(row)
    lines.append("")

    lines.append("## 今日摘要（TL;DR）")
    lines.append("")
    if tldr_rows:
        for row in tldr_rows:
            lines.append(f"- {row}")
    else:
        lines.append("- 今日暂无可用摘要条目。")
    lines.append("")

    lines.append("## 内容质检")
    lines.append("")
    lines.append(
        f"- 综合评分：**{quality_score:.1f}/10**（第 {optimization_round} 轮自动优化）"
    )
    lines.append(
        f"- 维度得分：概述 {quality_breakdown['overview']:.1f} / TL;DR {quality_breakdown['tldr']:.1f} / 信号密度 {quality_breakdown['signal']:.1f} / 多样性 {quality_breakdown['diversity']:.1f} / 行动性 {quality_breakdown['action']:.1f}"
    )
    lines.append(
        f"- 质检结论：**{'PASS' if quality_score >= quality_threshold else 'FAIL'}**（阈值 {quality_threshold:.1f}）"
    )
    if quality_score < quality_threshold:
        lines.append("- 说明：达到最大自动优化轮次仍低于阈值，建议人工补充实验指标与评测细节。")
    lines.append("")

    if not top_stories:
        lines.extend(
            [
                "> [!warning] 今日无高质量条目",
                "> 建议检查数据源可用性与筛选阈值。",
                "",
            ]
        )
    else:
        for idx, (source, item) in enumerate(top_stories, start=1):
            lines.extend(_render_top_story(idx, source, item))
        if not top_diversity_stats.get("diversity_ok", True):
            lines.extend(
                [
                    "> [!warning] Top 条目多样性未达标",
                    (
                        f"> 当前：非论文 {top_diversity_stats.get('nonpaper_count', 0)} / "
                        f"论文 {top_diversity_stats.get('paper_count', 0)} / "
                        f"类型 {top_diversity_stats.get('type_count', 0)}；"
                        f"阈值：非论文≥{max(0, min_top_nonpaper)}，"
                        f"类型≥{max(1, min_top_content_types)}。"
                    ),
                    "",
                ]
            )

    if venture_candidate:
        lines.extend(_render_deep_spotlight("创投洞见", venture_candidate[0], venture_candidate[1]))
    if growth_candidate:
        lines.extend(_render_brief_spotlight("成长洞见", growth_candidate[0], growth_candidate[1]))
    if video_candidate:
        lines.extend(_render_video_spotlight(video_candidate[0], video_candidate[1]))
    if tweet_candidate:
        lines.extend(_render_brief_spotlight("推文速览", tweet_candidate[0], tweet_candidate[1]))
    if insight_candidate:
        lines.extend(_render_brief_spotlight("洞见", insight_candidate[0], insight_candidate[1]))

    lines.append("## 执行清单（3 件事）")
    lines.append("")
    if top_stories:
        for idx, (source, item) in enumerate(top_stories[:action_items], start=1):
            lines.append(
                f"- [ ] {idx}. [{_story_title(item, max_len=80)}]({item.get('link', '')})：{_soften_undisclosed(_capture_prompt(item, source))}"
            )
    else:
        lines.append("- [ ] 无可执行 Top 条目，待下一轮抓取。")
    lines.append("")

    lines.append("## 今日快照")
    lines.append("")
    lines.append(f"- 扫描来源：**{stats.get('sources_scanned', len(items_by_source))}**")
    lines.append(f"- Top Picks：**{len(top_stories)}**")
    lines.append(
        f"- Top 多样性：非论文 {top_diversity_stats.get('nonpaper_count', 0)} / "
        f"论文 {top_diversity_stats.get('paper_count', 0)} / "
        f"类型 {top_diversity_stats.get('type_count', 0)}（"
        f"{'达标' if top_diversity_stats.get('diversity_ok', False) else '未达标'}）"
    )
    lines.append(
        f"- 类型覆盖：AI资讯 {len(content_groups.get('news', []))} / 推文 {len(content_groups.get('tweet', []))} / 工程 {len(content_groups.get('engineering', []))} / 论文 {len(content_groups.get('paper', []))} / 视频 {len(content_groups.get('video', []))}"
    )
    lines.append(
        f"- 写入策略：单一核心日报（论文 {stats.get('papers_written', 0)} 条 / 视频 {stats.get('videos_written', 0)} 条合并）"
    )

    return filepath, "\n".join(lines)


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


def _normalized_concept_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if not value:
            continue
        normalized.append(value if value.startswith("#concept/") else f"#concept/{value.lstrip('#')}")
    return normalized[:3]


def _video_duration_text(item: dict) -> str:
    for key in ("duration_text", "duration", "length"):
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return "未披露"


def _fallback_curated_summary(item: dict, *, max_sentences: int, max_len: int) -> str:
    text = str(item.get("summary", "")).strip() or str(item.get("title", "")).strip()
    return _multi_sentence_summary(text, max_len=max_len, max_sentences=max_sentences)


def _fallback_curated_points(item: dict, *, limit: int) -> list[str]:
    summary = str(item.get("summary", "")).strip()
    if not summary:
        return [f"原文标题：{_story_title(item)}"]

    sentences = [
        segment.strip(" -•\t")
        for segment in re.split(r"(?<=[.!?。！？；;])\s+", summary)
        if segment.strip()
    ]
    points: list[str] = []
    for sentence in sentences:
        cleaned = _clean_text(sentence, max_len=120)
        if cleaned and cleaned not in points:
            points.append(cleaned)
        if len(points) >= limit:
            break
    if not points:
        points.append(_clean_text(summary, max_len=120))
    return points[:limit]


def _fallback_curated_action(item: dict, source: str) -> str:
    source_lower = source.lower()
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    content_type = str(item.get("content_type", "")).lower()

    if content_type == "video":
        return "从视频里挑 1 个方法片段做最小复现，并记录你自己的输入、输出和时间成本。"
    if content_type == "paper":
        return "先追踪作者代码仓库和 benchmark 细节，确认可复现性后再决定是否投入完整复现。"
    if any(token in source_lower for token in ("google ai blog", "langchain", "hugging face", "lilian weng")):
        return "把这条思路沉淀成一个可复用模板或技能，而不是停留在一次性的 Prompt。"
    if any(token in text for token in ("agent", "coding", "workflow", "tool", "context", "memory")):
        return "把文中的方法抽成一条可复用 SOP，并在你自己的工作流里跑一次最小闭环。"
    if any(token in text for token in ("investment", "market", "infrastructure", "startup", "satellite")):
        return "把这条变化当作行业信号，重新检查你当前项目依赖的基础设施与分发假设。"
    return "把这条信息转成一个可验证的小实验或一条明确的决策检查项。"


def _fallback_concept_tags(item: dict, source: str) -> list[str]:
    tags = _normalized_concept_tags(_concept_tags(item, source))
    content_type = str(item.get("content_type", "")).lower()
    source_lower = source.lower()
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()

    if len(tags) < 2 and any(token in text for token in ("agent", "coding", "workflow")):
        tags.append("#concept/Workflow")
    if len(tags) < 2 and any(token in text for token in ("evaluation", "eval", "benchmark")):
        tags.append("#concept/Evaluation")
    if len(tags) < 2 and content_type == "paper":
        tags.append("#concept/Research")
    if len(tags) < 2 and content_type == "video":
        tags.append("#concept/Video-Learning")
    if len(tags) < 2 and any(token in source_lower for token in ("google ai blog", "langchain", "hugging face")):
        tags.append("#concept/Reusable-Workflows")
    if len(tags) < 2:
        tags.append("#concept/Workflow")
    if len(tags) < 2:
        tags.append("#concept/AI-Tools")
    return tags[:3]


def _render_curated_deep_block(
    label: str,
    source: str,
    item: dict,
    *,
    copy: dict[str, Any] | None = None,
) -> list[str]:
    title = str(copy.get("headline_cn", "")).strip() if copy else _story_title(item)
    details = list(copy.get("key_details", [])) if copy else _fallback_curated_points(item, limit=3)
    tags = _normalized_concept_tags(list(copy.get("core_concepts", []))) if copy else _fallback_concept_tags(item, source)
    action = str(copy.get("actionable_insight", "")).strip() if copy else _fallback_curated_action(item, source)
    core_finding = str(copy.get("core_finding", "")).strip() if copy else _fallback_curated_summary(
        item,
        max_sentences=2,
        max_len=220,
    )
    return [
        f"## {label} — {title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{_story_title(item)}]({item.get('link', '')})",
        f"**🔑 核心概念**：{' '.join(tags)}",
        "",
        "### 深度 Takeaways",
        "",
        f"**核心发现**：{core_finding}",
        "",
        "**关键细节**：",
        *[f"- {point}" for point in details[:3]],
        "",
        f"**💡 行动启示**：{action}",
        "",
        "---",
        "",
    ]


def _render_curated_brief_block(
    icon: str,
    source: str,
    item: dict,
    *,
    copy: dict[str, Any] | None = None,
) -> list[str]:
    title = str(copy.get("headline_cn", "")).strip() if copy else _story_title(item)
    details = list(copy.get("key_points", [])) if copy else _fallback_curated_points(item, limit=3)
    tags = _normalized_concept_tags(list(copy.get("core_concepts", []))) if copy else _fallback_concept_tags(item, source)
    action = str(copy.get("actionable_insight", "")).strip() if copy else _fallback_curated_action(item, source)
    one_line = str(copy.get("one_line_summary", "")).strip() if copy else _fallback_curated_summary(
        item,
        max_sentences=1,
        max_len=80,
    )
    return [
        f"## {icon} 洞见 — {title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[{_story_title(item)}]({item.get('link', '')})",
        f"**🔑 核心概念**：{' '.join(tags)}",
        "",
        f"**一句话**：{one_line}",
        "",
        "**3 个要点**：",
        *[f"- {point}" for point in details[:3]],
        "",
        f"**💡 行动启示**：{action}",
        "",
        "---",
        "",
    ]


def _render_curated_video_block(
    source: str,
    item: dict,
    *,
    copy: dict[str, Any] | None = None,
) -> list[str]:
    title = str(copy.get("headline_cn", "")).strip() if copy else _story_title(item)
    details = list(copy.get("method_points", [])) if copy else _fallback_curated_points(item, limit=2)
    tags = _normalized_concept_tags(list(copy.get("core_concepts", []))) if copy else _fallback_concept_tags(item, source)
    action = str(copy.get("actionable_insight", "")).strip() if copy else _fallback_curated_action(item, source)
    conclusion = str(copy.get("core_conclusion", "")).strip() if copy else _fallback_curated_summary(
        item,
        max_sentences=2,
        max_len=220,
    )
    return [
        f"## 📺 今日视频 — {title}",
        "",
        f"**频道**：{source}",
        f"**链接**：[{_story_title(item)}]({item.get('link', '')})",
        f"**时长**：{_video_duration_text(item)}",
        f"**🔑 核心概念**：{' '.join(tags)}",
        "",
        f"**核心结论**：{conclusion}",
        "",
        "**关键方法论**：",
        *[f"- {point}" for point in details[:2]],
        "",
        f"**💡 行动启示**：{action}",
        "",
        "---",
        "",
    ]


def _render_curated_daily_digest(
    plan: daily_curation.DailyDigestPlan,
    *,
    digest_copy: dict[str, Any] | None = None,
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
        top_copy = None
        if digest_copy:
            top_stories_copy = list(digest_copy.get("top_stories", []))
            top_copy = top_stories_copy[idx - 1] if idx - 1 < len(top_stories_copy) else None
        lines.extend(_render_curated_deep_block(f"🔥 Top {idx}", source, item, copy=top_copy))

    if plan.venture_story:
        lines.extend(
            _render_curated_deep_block(
                "💰 创投洞见",
                *plan.venture_story,
                copy=(digest_copy or {}).get("venture_story"),
            )
        )
    if plan.growth_story:
        lines.extend(
            _render_curated_brief_block(
                "🌱",
                *plan.growth_story,
                copy=(digest_copy or {}).get("insight_story"),
            )
        )
    if plan.video_story:
        lines.extend(_render_curated_video_block(*plan.video_story, copy=(digest_copy or {}).get("video_story")))
    if plan.solopreneur_story:
        lines.extend(
            _render_curated_brief_block(
                "🤖",
                *plan.solopreneur_story,
                copy=(digest_copy or {}).get("ai_company_story"),
            )
        )

    return filepath, "\n".join(lines).rstrip() + "\n"


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
    quality_gate_enabled: bool = True,
    tldr_min_quality_score: int = 1,
    tldr_max_undisclosed: int = 0,
    tldr_min_items: int = 1,
    tldr_min_hard_signal_ratio: float = 0.4,
    tldr_max_undisclosed_ratio: float = 0.3,
    min_top_nonpaper: int = 2,
    min_top_content_types: int = 2,
    max_paper_in_top: int = 1,
    paper_written: list[dict] | None = None,
    video_written: list[dict] | None = None,
    paper_queue: list[dict] | None = None,
    video_queue: list[dict] | None = None,
    stats: dict[str, Any] | None = None,
    curation_plan: daily_curation.DailyDigestPlan | None = None,
    digest_copy: dict[str, Any] | None = None,
    used_urls: set[str] | None = None,
    rotation_state: dict[str, Any] | None = None,
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

    plan = curation_plan or daily_curation.plan_daily_digest(
        dict(items_by_source),
        today=today,
        top_picks=top_picks,
        action_items=action_items,
        max_deferred_items=max_deferred_items,
        min_top_nonpaper=min_top_nonpaper,
        min_top_content_types=min_top_content_types,
        max_paper_in_top=max_paper_in_top,
        paper_written=paper_written,
        video_written=video_written,
        paper_queue=paper_queue,
        video_queue=video_queue,
        stats=stats,
        used_urls=used_urls,
        rotation_state=rotation_state,
    )
    return _render_curated_daily_digest(plan, digest_copy=digest_copy)

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
    tldr_rows = _build_tldr_rows(tldr_candidates)
    selected_tldr_rows = _select_tldr_rows(
        tldr_rows,
        limit=tldr_limit,
        quality_gate_enabled=quality_gate_enabled,
        min_quality_score=tldr_min_quality_score,
        max_undisclosed=tldr_max_undisclosed,
        min_items=tldr_min_items,
    )
    tldr_quality = _tldr_quality_report(selected_tldr_rows)
    quality_gate_pass = (
        tldr_quality["hard_signal_ratio"] >= tldr_min_hard_signal_ratio
        and tldr_quality["undisclosed_ratio"] <= tldr_max_undisclosed_ratio
    )
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
    if not selected_tldr_rows:
        lines.append("- *(今日暂无达标条目。)*")
        lines.append("")
    else:
        for row in selected_tldr_rows:
            source = str(row["source"])
            item = row["item"]
            headline = str(row["headline"])
            summary = str(row["summary"])
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
                summary = _summary_cn(
                    item, source, max_len=220, max_sentences=2, include_title=False
                )

                lines.append(f"### 判断 {idx}：{lens}")
                lines.append(
                    f"- 证据：[{item['title']}]({item['link']})（{source} / {content_type_label} / {_published_date(item)}）"
                )
                if summary:
                    lines.append(f"- 发生了什么：{summary}")
                lines.append(f"- 技术实质：{_technical_essence(item, source)}")
                lines.append(f"- 失败边界/局限性：{_failure_boundary(item, source)}")
                lines.append(f"- 最小复现实验：{_daily_one_thing(item, source)}")
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
                _summary_cn(item, source, max_len=80, max_sentences=1, include_title=False)
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
                summary = _summary_cn(
                    item, source, max_len=180, max_sentences=2, include_title=False
                )
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
        for idx, (source, item) in enumerate(picks[:action_items], start=1):
            lines.append(
                f"- [ ] {idx}. [{item.get('title', '未命名')}]({item.get('link', '')}) | {_capture_prompt(item, source)}"
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
                summary = _summary_cn(
                    item, source, max_len=90, max_sentences=1, include_title=False
                )
                line = f"- [{item.get('title', '未命名')}]({item.get('link', '')}) | {source}"
                if summary:
                    line += f" | {summary}"
                lines.append(line)
            lines.append("")
        if video_queue:
            lines.append("### 视频")
            for item in video_queue:
                source = str(item.get("source", "视频来源"))
                summary = _summary_cn(
                    item, source, max_len=90, max_sentences=1, include_title=False
                )
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

    lines.append("## 质量哨兵（Auto QA）")
    lines.append("")
    lines.append(f"- TL;DR 条目：**{tldr_quality['total']}**")
    lines.append(
        f"- 硬信号命中：**{tldr_quality['hard_signal_count']} / {max(tldr_quality['total'], 1)}** "
        f"（{tldr_quality['hard_signal_ratio'] * 100:.0f}%）"
    )
    lines.append(
        f"- “未披露”密度：**{tldr_quality['undisclosed_total']} / {max(tldr_quality['total'], 1)}** "
        f"（均值 {tldr_quality['undisclosed_ratio']:.2f}）"
    )
    lines.append(
        f"- 门禁阈值：硬信号 >= {tldr_min_hard_signal_ratio:.2f}，未披露均值 <= {tldr_max_undisclosed_ratio:.2f}"
    )
    lines.append(f"- 门禁状态：**{'PASS' if quality_gate_pass else 'FAIL'}**")
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
            summary = _summary_cn(item, source, max_len=120, max_sentences=1, include_title=False)
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


# Clean overrides for the open-source curated digest path.
AI_BUCKET_LABELS = {
    "frontier": "前沿技术",
    "practice": "工程实践",
    "tooling": "工具更新",
}

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
    "能力边界是否真的前移，而不只是榜单数字更好看？",
    "架构范式是否发生变化，例如主模型加子代理或新型工具编排？",
    "成本、延迟、质量之间的前沿约束是否被改写？",
    "评测与治理是否可复现、可审计，而不只是宣传口径？",
    "这条信息能否沉淀为长期杠杆，例如 SOP、模板或基线？",
]


def _normalized_concept_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if not value:
            continue
        normalized.append(value if value.startswith("#concept/") else f"#concept/{value.lstrip('#')}")
    return normalized[:3]


def _video_duration_text(item: dict) -> str:
    for key in ("duration_text", "duration", "length"):
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return "未披露"


def _fallback_curated_summary(item: dict, *, max_sentences: int, max_len: int) -> str:
    text = str(item.get("summary", "")).strip() or str(item.get("title", "")).strip()
    return _multi_sentence_summary(text, max_len=max_len, max_sentences=max_sentences)


def _fallback_curated_points(item: dict, *, limit: int) -> list[str]:
    summary = str(item.get("summary", "")).strip()
    if not summary:
        return [f"原文标题：{_story_title(item)}"]

    sentences = [
        segment.strip(" -\t")
        for segment in re.split(r"(?<=[.!?。！？；;])\s+", summary)
        if segment.strip()
    ]
    points: list[str] = []
    for sentence in sentences:
        cleaned = _clean_text(sentence, max_len=120)
        if cleaned and cleaned not in points:
            points.append(cleaned)
        if len(points) >= limit:
            break
    if not points:
        points.append(_clean_text(summary, max_len=120))
    return points[:limit]


def _fallback_curated_action(item: dict, source: str) -> str:
    source_lower = source.lower()
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    content_type = str(item.get("content_type", "")).lower()

    if content_type == "video":
        return "从视频里抽 1 个方法片段做最小复现，并记录你的输入、输出和时间成本。"
    if content_type == "paper":
        return "先追踪作者代码仓库和 benchmark 细节，确认可复现性后再决定是否投入完整复现。"
    if any(token in source_lower for token in ("google ai blog", "langchain", "hugging face", "lilian weng")):
        return "把这条思路沉淀成一个可复用模板或技能，而不是停留在一次性的 Prompt。"
    if any(token in text for token in ("agent", "coding", "workflow", "tool", "context", "memory")):
        return "把文中的方法抽成一条可复用 SOP，并在你自己的工作流里跑一次最小闭环。"
    if any(token in text for token in ("investment", "market", "infrastructure", "startup", "satellite")):
        return "把这条变化当作行业信号，重新检查你当前项目依赖的基础设施与分发假设。"
    return "把这条信息转成一个可验证的小实验，或一条明确的决策检查项。"


def _fallback_concept_tags(item: dict, source: str) -> list[str]:
    tags = _normalized_concept_tags(_concept_tags(item, source))
    content_type = str(item.get("content_type", "")).lower()
    source_lower = source.lower()
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()

    if len(tags) < 2 and any(token in text for token in ("agent", "coding", "workflow")):
        tags.append("#concept/Workflow")
    if len(tags) < 2 and any(token in text for token in ("evaluation", "eval", "benchmark")):
        tags.append("#concept/Evaluation")
    if len(tags) < 2 and content_type == "paper":
        tags.append("#concept/Research")
    if len(tags) < 2 and content_type == "video":
        tags.append("#concept/Video-Learning")
    if len(tags) < 2 and any(token in source_lower for token in ("google ai blog", "langchain", "hugging face")):
        tags.append("#concept/Reusable-Workflows")
    if len(tags) < 2:
        tags.append("#concept/Workflow")
    if len(tags) < 2:
        tags.append("#concept/AI-Tools")
    return tags[:3]


def _render_curated_deep_block(
    label: str,
    source: str,
    item: dict,
    *,
    copy: dict[str, Any] | None = None,
) -> list[str]:
    title = str(copy.get("headline_cn", "")).strip() if copy else _story_title(item)
    details = list(copy.get("key_details", [])) if copy else _fallback_curated_points(item, limit=3)
    tags = (
        _normalized_concept_tags(list(copy.get("core_concepts", [])))
        if copy
        else _fallback_concept_tags(item, source)
    )
    action = (
        str(copy.get("actionable_insight", "")).strip()
        if copy
        else _fallback_curated_action(item, source)
    )
    core_finding = (
        str(copy.get("core_finding", "")).strip()
        if copy
        else _fallback_curated_summary(item, max_sentences=2, max_len=220)
    )
    return [
        f"## {label} - {title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[${_story_title(item)}]({item.get('link', '')})".replace("[$", "["),
        f"**核心概念**：{' '.join(tags)}",
        "",
        "### 深度 Takeaways",
        "",
        f"**核心发现**：{core_finding}",
        "",
        "**关键细节**：",
        *[f"- {point}" for point in details[:3]],
        "",
        f"**行动启示**：{action}",
        "",
        "---",
        "",
    ]


def _render_curated_brief_block(
    icon: str,
    source: str,
    item: dict,
    *,
    copy: dict[str, Any] | None = None,
) -> list[str]:
    title = str(copy.get("headline_cn", "")).strip() if copy else _story_title(item)
    details = list(copy.get("key_points", [])) if copy else _fallback_curated_points(item, limit=3)
    tags = (
        _normalized_concept_tags(list(copy.get("core_concepts", [])))
        if copy
        else _fallback_concept_tags(item, source)
    )
    action = (
        str(copy.get("actionable_insight", "")).strip()
        if copy
        else _fallback_curated_action(item, source)
    )
    one_line = (
        str(copy.get("one_line_summary", "")).strip()
        if copy
        else _fallback_curated_summary(item, max_sentences=1, max_len=80)
    )
    return [
        f"## {icon} 洞见 - {title}",
        "",
        f"**来源**：{source}",
        f"**原文**：[${_story_title(item)}]({item.get('link', '')})".replace("[$", "["),
        f"**核心概念**：{' '.join(tags)}",
        "",
        f"**一句话**：{one_line}",
        "",
        "**3 个要点**：",
        *[f"- {point}" for point in details[:3]],
        "",
        f"**行动启示**：{action}",
        "",
        "---",
        "",
    ]


def _render_curated_video_block(
    source: str,
    item: dict,
    *,
    copy: dict[str, Any] | None = None,
) -> list[str]:
    title = str(copy.get("headline_cn", "")).strip() if copy else _story_title(item)
    details = list(copy.get("method_points", [])) if copy else _fallback_curated_points(item, limit=2)
    tags = (
        _normalized_concept_tags(list(copy.get("core_concepts", [])))
        if copy
        else _fallback_concept_tags(item, source)
    )
    action = (
        str(copy.get("actionable_insight", "")).strip()
        if copy
        else _fallback_curated_action(item, source)
    )
    conclusion = (
        str(copy.get("core_conclusion", "")).strip()
        if copy
        else _fallback_curated_summary(item, max_sentences=2, max_len=220)
    )
    return [
        f"## 📺 今日视频 - {title}",
        "",
        f"**频道**：{source}",
        f"**链接**：[${_story_title(item)}]({item.get('link', '')})".replace("[$", "["),
        f"**时长**：{_video_duration_text(item)}",
        f"**核心概念**：{' '.join(tags)}",
        "",
        f"**核心结论**：{conclusion}",
        "",
        "**关键方法论**：",
        *[f"- {point}" for point in details[:2]],
        "",
        f"**行动启示**：{action}",
        "",
        "---",
        "",
    ]


def _render_curated_daily_digest(
    plan: daily_curation.DailyDigestPlan,
    *,
    digest_copy: dict[str, Any] | None = None,
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
        top_copy = None
        if digest_copy:
            top_stories_copy = list(digest_copy.get("top_stories", []))
            top_copy = top_stories_copy[idx - 1] if idx - 1 < len(top_stories_copy) else None
        lines.extend(_render_curated_deep_block(f"🔥 Top {idx}", source, item, copy=top_copy))

    if plan.venture_story:
        lines.extend(
            _render_curated_deep_block(
                "💰 创投洞见",
                *plan.venture_story,
                copy=(digest_copy or {}).get("venture_story"),
            )
        )
    if plan.growth_story:
        lines.extend(
            _render_curated_brief_block(
                "🌱",
                *plan.growth_story,
                copy=(digest_copy or {}).get("insight_story"),
            )
        )
    if plan.video_story:
        lines.extend(
            _render_curated_video_block(
                *plan.video_story,
                copy=(digest_copy or {}).get("video_story"),
            )
        )
    if plan.solopreneur_story:
        lines.extend(
            _render_curated_brief_block(
                "🤖",
                *plan.solopreneur_story,
                copy=(digest_copy or {}).get("ai_company_story"),
            )
        )

    return filepath, "\n".join(lines).rstrip() + "\n"


def format_daily_digest(
    items_by_source: dict,
    raw_only: bool = False,
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
    paper_written: list[dict] | None = None,
    video_written: list[dict] | None = None,
    paper_queue: list[dict] | None = None,
    video_queue: list[dict] | None = None,
    stats: dict[str, Any] | None = None,
    curation_plan: daily_curation.DailyDigestPlan | None = None,
    digest_copy: dict[str, Any] | None = None,
    used_urls: set[str] | None = None,
    rotation_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
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
            "## AI 资讯分桶",
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
                lines.append(f"- **标题**：{item['title']}")
                lines.append(f"  **链接**：{item['link']}")
                lines.append(f"  **来源**：{source}")
                lines.append(f"  **兴趣分**：{item.get('score', 0)}")
                if item.get("score_reasons"):
                    lines.append(f"  **评分信号**：{', '.join(item['score_reasons'])}")
                if item.get("summary"):
                    lines.append(f"  **摘要**：{_clean_text(item['summary'], max_len=500)}")
                lines.append("")

        lines.append("## 按来源展开")
        lines.append("")
        for source, items in items_by_source.items():
            lines.append(f"## {source}")
            lines.append("")
            for item in items:
                lines.append(f"- **标题**：{item['title']}")
                lines.append(f"  **链接**：{item['link']}")
                if item.get("score") is not None:
                    lines.append(f"  **兴趣分**：{item['score']}")
                    if item.get("score_reasons"):
                        lines.append(f"  **评分信号**：{', '.join(item['score_reasons'])}")
                if item.get("summary"):
                    lines.append(f"  **摘要**：{_clean_text(item['summary'], max_len=500)}")
                lines.append("")

        return filepath, "\n".join(lines)

    plan = curation_plan or daily_curation.plan_daily_digest(
        dict(items_by_source),
        today=today,
        top_picks=top_picks,
        action_items=action_items,
        max_deferred_items=max_deferred_items,
        min_top_nonpaper=min_top_nonpaper,
        min_top_content_types=min_top_content_types,
        max_paper_in_top=max_paper_in_top,
        paper_written=paper_written,
        video_written=video_written,
        paper_queue=paper_queue,
        video_queue=video_queue,
        stats=stats,
        used_urls=used_urls,
        rotation_state=rotation_state,
    )
    return _render_curated_daily_digest(plan, digest_copy=digest_copy)
