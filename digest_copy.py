#!/usr/bin/env python3
"""
Deterministic copy generation for the curated daily digest.

The open-source workflow should still produce a readable, high-signal digest
without any external LLM. This module converts a curated plan into a stable
Chinese copy structure. An optional LLM layer may refine the wording later, but
the baseline output must remain publishable on its own.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

import daily_curation

_CONCEPT_RULES = [
    (("eval", "evaluation", "benchmark", "grader", "arena", "pass@"), "#concept/Evaluation"),
    (("agent", "coding agent", "computer use", "autonomous"), "#concept/Agent-Engineering"),
    (("workflow", "playbook", "runbook", "harness", "orchestr"), "#concept/Workflow"),
    (("tool calling", "function calling", "tool-use"), "#concept/Tool-Calling"),
    (("memory", "context", "retrieval", "rag"), "#concept/Context-Memory"),
    (("browser", "chrome", "extension"), "#concept/Browser-Automation"),
    (("prompt", "skill", "template"), "#concept/Reusable-Workflows"),
    (("startup", "budget", "pricing", "market", "enterprise", "revenue"), "#concept/Go-To-Market"),
    (("infrastructure", "compute", "cluster", "gpu", "power", "network"), "#concept/AI-Infrastructure"),
    (("paper", "arxiv", "research"), "#concept/Research"),
    (("video", "visual", "geometry", "proof"), "#concept/Visual-Learning"),
]

_METRIC_PATTERNS = (
    r"\bpass@\d+\s*=?\s*\d+(?:\.\d+)?%?",
    r"\b\d+(?:\.\d+)?\s?%",
    r"\bp\d{2}\s*=?\s*\d+(?:\.\d+)?\s?(?:ms|s)\b",
    r"\b\d+(?:\.\d+)?\s?(?:ms|s|mins?|minutes?)\b",
    r"\bcost(?:[_ -]?[a-z]+)?\s*=?\s*\$?\d+(?:\.\d+)?",
)

_TOPIC_LABELS = {
    "computer use": "桌面执行",
    "coding agent": "编码代理",
    "tool calling": "工具调用",
    "function calling": "函数调用",
    "evaluation": "评测体系",
    "eval": "评测体系",
    "benchmark": "基准测试",
    "workflow": "工作流编排",
    "prompt": "可复用 Prompt",
    "memory": "长期记忆",
    "context": "上下文管理",
    "chrome": "浏览器工作台",
    "browser": "浏览器工作台",
    "contract": "企业采购",
    "enterprise": "企业采购",
    "market": "商业化窗口",
    "infrastructure": "基础设施",
    "research": "研究进展",
    "geometry": "可视化解释",
}


def build_digest_copy(plan: daily_curation.DailyDigestPlan) -> dict[str, Any]:
    return {
        "top_stories": [_build_deep_story(source, item, section="top") for source, item in plan.top_stories],
        "venture_story": _maybe(plan.venture_story, lambda s, i: _build_deep_story(s, i, section="venture")),
        "insight_story": _maybe(plan.growth_story, lambda s, i: _build_brief_story(s, i, section="insight")),
        "video_story": _maybe(plan.video_story, _build_video_story),
        "ai_company_story": _maybe(
            plan.solopreneur_story,
            lambda s, i: _build_brief_story(s, i, section="company"),
        ),
    }


def merge_digest_copy(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return base

    merged = dict(base)
    merged["top_stories"] = []
    base_top = list(base.get("top_stories", []))
    override_top = list(override.get("top_stories", []))
    for index in range(max(len(base_top), len(override_top))):
        base_row = base_top[index] if index < len(base_top) else {}
        override_row = override_top[index] if index < len(override_top) else {}
        if base_row or override_row:
            merged["top_stories"].append(_merge_section(base_row, override_row))

    for key in ("venture_story", "insight_story", "video_story", "ai_company_story"):
        base_value = base.get(key)
        override_value = override.get(key)
        if override_value is None:
            merged[key] = base_value
        elif base_value is None:
            merged[key] = override_value
        else:
            merged[key] = _merge_section(base_value, override_value)
    return merged


def _merge_section(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, list):
            merged[key] = value or base.get(key, [])
        elif value not in (None, ""):
            merged[key] = value
    return merged


def _maybe(
    story: tuple[str, dict[str, Any]] | None,
    builder: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    if not story:
        return None
    source, item = story
    return builder(source, item)


def _build_deep_story(source: str, item: dict[str, Any], *, section: str) -> dict[str, Any]:
    evidence = _evidence_text(item)
    return {
        "headline_cn": _headline(source, item, section=section),
        "core_concepts": _concepts(source, item, minimum=2),
        "core_finding": _core_finding(item, section=section),
        "key_details": _key_details(item, evidence=evidence, limit=3),
        "actionable_insight": _actionable_insight(source, item, section=section),
    }


def _build_brief_story(source: str, item: dict[str, Any], *, section: str) -> dict[str, Any]:
    evidence = _evidence_text(item)
    return {
        "headline_cn": _headline(source, item, section=section),
        "core_concepts": _concepts(source, item, minimum=2),
        "one_line_summary": _one_line_summary(item, section=section),
        "key_points": _key_details(item, evidence=evidence, limit=3),
        "actionable_insight": _actionable_insight(source, item, section=section),
    }


def _build_video_story(source: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline_cn": _headline(source, item, section="video"),
        "core_concepts": _concepts(source, item, minimum=2),
        "core_conclusion": _core_finding(item, section="video"),
        "method_points": _key_details(item, evidence=_evidence_text(item), limit=2),
        "actionable_insight": _actionable_insight(source, item, section="video"),
    }


def _headline(source: str, item: dict[str, Any], *, section: str) -> str:
    text = _evidence_text(item).lower()
    subject = _subject_label(source, item)
    topic = _topic_phrase(text) or "这条更新"

    if section == "venture":
        return f"{subject} 暴露了 AI 商业化推进中的真实约束"
    if section == "video":
        return f"{subject} 用可视化方式拆解 {topic}"
    if "computer use" in text or "desktop" in text or "browser" in text:
        return f"{subject} 正把代理从对话层推进到执行层"
    if "tool calling" in text or "function calling" in text:
        return f"{subject} 开始把工具调用纳入生产编排"
    if "eval" in text or "evaluation" in text or "benchmark" in text:
        return f"{subject} 正在把评测做成可审计流程"
    if "workflow" in text or "harness" in text or "playbook" in text:
        return f"{subject} 沉淀出更可复用的工作流范式"
    if "prompt" in text or "skill" in text or "chrome" in text:
        return f"{subject} 正在把高频 Prompt 产品化"
    if "memory" in text or "context" in text:
        return f"{subject} 试图解决长上下文和记忆约束"
    return f"{subject} 这条更新值得关注：{topic}"


def _subject_label(source: str, item: dict[str, Any]) -> str:
    title = str(item.get("title", "")).strip()
    lowered = f"{source} {title}".lower()
    for token, label in (
        ("openai", "OpenAI"),
        ("codex", "Codex"),
        ("langchain", "LangChain"),
        ("google", "Google"),
        ("hugging face", "Hugging Face"),
        ("lilian weng", "Lilian Weng"),
        ("latent space", "Latent Space"),
        ("simon willison", "Simon Willison"),
        ("3blue1brown", "3Blue1Brown"),
        ("guardian", "The Guardian"),
        ("sequoia", "Sequoia"),
    ):
        if token in lowered:
            return label
    return source


def _core_finding(item: dict[str, Any], *, section: str) -> str:
    text = _evidence_text(item).lower()
    metrics = _extract_metrics(text)

    if section == "venture":
        finding = "重点不在单条新闻本身，而在它暴露了 AI 商业化、基础设施或企业采购的真实约束。"
    elif section == "video":
        finding = "这条视频的价值在于把抽象概念压缩成可迁移的心智模型，而不是只给结论。"
    elif "eval" in text or "evaluation" in text or "benchmark" in text:
        finding = "核心变化不是又多了一个宣传指标，而是评测口径开始具备落地意义。"
    elif "tool calling" in text or "function calling" in text:
        finding = "真正的增量不在工具数量，而在工具调用的成功率、时延和容错开始被系统化处理。"
    elif "computer use" in text or "desktop" in text or "browser" in text:
        finding = "关键变化不是聊天能力，而是代理开始进入跨应用执行层。"
    elif "workflow" in text or "playbook" in text or "harness" in text:
        finding = "重点不是观点，而是方法开始沉淀为可复用工作流。"
    elif "prompt" in text or "skill" in text or "chrome" in text:
        finding = "关键变化是把一次性 Prompt 固化成可复用工具，而不是继续堆手工指令。"
    else:
        finding = _first_sentence(_evidence_text(item), max_len=96) or "这条更新值得关注，但仍需结合原文判断真实增量。"

    if metrics:
        return f"{finding} 文中至少给出了 {metrics[0]} 这类硬信号。"
    return finding


def _one_line_summary(item: dict[str, Any], *, section: str) -> str:
    text = _evidence_text(item).lower()
    metrics = _extract_metrics(text)
    if section == "company" and ("prompt" in text or "skill" in text or "chrome" in text):
        return "重点不是写更长 Prompt，而是把高频操作封装成可复用工具。"
    if section == "insight" and ("paper" in text or "arxiv" in text):
        return "这条 insight 更像研究信号，适合跟踪方法与边界，不适合直接当成结论。"
    if metrics:
        return f"这条更新至少给出了 {metrics[0]} 级别的硬信号，而不只是口号。"
    return _first_sentence(_evidence_text(item), max_len=64) or "这条更新值得跟踪，但仍需结合原文判断含金量。"


def _key_details(item: dict[str, Any], *, evidence: str, limit: int) -> list[str]:
    points: list[str] = []
    metrics = _extract_metrics(evidence)
    benchmark = _extract_benchmark(evidence)
    topic = _topic_phrase(evidence.lower())

    if metrics:
        points.append(f"硬信号：文中至少出现了 {metrics[0]}。")
    if benchmark:
        points.append(f"评测或观察口径：重点落在 {benchmark}。")
    if topic:
        points.append(f"主题焦点：真正值得看的不是热闹，而是 {topic}。")

    for sentence in _sentences(evidence):
        cleaned = _clean_sentence(sentence)
        if cleaned and cleaned not in points:
            points.append(cleaned)
        if len(points) >= limit:
            break

    while len(points) < limit:
        if len(points) == 0:
            points.append("值得继续回看原文，确认这条信息对应的边界条件与适用场景。")
        elif len(points) == 1:
            points.append("如果要落地，先抽取可复用方法，而不是只记住结论。")
        else:
            points.append("后续应关注这条信息是否带来更稳定的工作流、成本或评测改进。")

    return points[:limit]


def _actionable_insight(source: str, item: dict[str, Any], *, section: str) -> str:
    text = _evidence_text(item).lower()
    source_label = _subject_label(source, item)

    if section == "venture":
        return "把它当成市场约束信号来读：优先判断预算、采购链条和交付复杂度，而不是先被概念吸引。"
    if section == "video":
        return "把视频里的一个解释框架迁移到自己的项目里，验证它是否能降低理解和沟通成本。"
    if "eval" in text or "evaluation" in text or "benchmark" in text:
        return "优先补齐自己的评测口径，至少让关键任务具备可复跑、可比较和可审计的基线。"
    if "tool calling" in text or "function calling" in text:
        return "先把高频工具调用链路做成稳定 SOP，再考虑扩大代理权限。"
    if "computer use" in text or "desktop" in text or "browser" in text:
        return "优先挑一个跨应用、重复度高的流程交给本地代理执行，再用日志验证是否真的省时。"
    if "prompt" in text or "skill" in text or "chrome" in text:
        return "把高频 Prompt 固化成模板、技能或浏览器工具，减少重复手工输入。"
    if "memory" in text or "context" in text:
        return "先收缩上下文窗口，只保留任务必需状态，再判断是否真的需要更复杂的记忆层。"
    return f"把 {source_label} 这条信息转成一个可执行实验，而不是停留在“知道了”。"


def _concepts(source: str, item: dict[str, Any], *, minimum: int) -> list[str]:
    text = f"{source} {_evidence_text(item)}".lower()
    concepts: list[str] = []
    for tokens, label in _CONCEPT_RULES:
        if any(token in text for token in tokens):
            concepts.append(label)
    if not concepts:
        concepts.extend(["#concept/Workflow", "#concept/Research"])
    while len(concepts) < minimum:
        fallback = "#concept/AI-Infrastructure" if "#concept/Workflow" in concepts else "#concept/Workflow"
        if fallback not in concepts:
            concepts.append(fallback)
        else:
            concepts.append("#concept/Research")
    return concepts[:3]


def _topic_phrase(text: str) -> str:
    for token, label in _TOPIC_LABELS.items():
        if token in text:
            return label
    return ""


def _extract_metrics(text: str) -> list[str]:
    matches: list[str] = []
    for pattern in _METRIC_PATTERNS:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            if match not in matches:
                matches.append(match)
    return matches[:3]


def _extract_benchmark(text: str) -> str:
    lowered = text.lower()
    for token in ("pass@1", "arena", "benchmark", "grader", "latency", "error budget", "throughput"):
        if token in lowered:
            return token
    return ""


def _evidence_text(item: dict[str, Any]) -> str:
    title = str(item.get("title", "")).strip()
    summary = str(item.get("summary", "")).strip()
    return f"{title}. {summary}".strip()


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", normalized)
    return [part.strip(" -") for part in parts if part.strip(" -")]


def _clean_sentence(sentence: str) -> str:
    cleaned = _clean_text(sentence, max_len=88)
    if not cleaned:
        return ""
    if cleaned.endswith((".", "。", "!", "！", "?", "？")):
        cleaned = cleaned[:-1]
    return cleaned + "。"


def _first_sentence(text: str, *, max_len: int) -> str:
    for sentence in _sentences(text):
        cleaned = _clean_text(sentence, max_len=max_len)
        if cleaned:
            return cleaned
    return ""


def _clean_text(text: str, *, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    cleaned = cleaned.replace("�", "")
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"
