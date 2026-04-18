#!/usr/bin/env python3
"""
daily_curation.py - Local-parity selection and state management for AI digest output.

The local workflow quality comes from strict section quotas, duplicate filtering,
source rotation, and a curated final slate. This module mirrors that behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

THREE_BLUE_ONE_BROWN = "3blue1brown"
VENTURE_SOURCE_TOKENS = ("sequoia", "guardian")
INSIGHT_SOURCE_TOKENS = ("dan koe", "arxiv", "ethan mollick", "latent space")
AI_COMPANY_SOURCE_TOKENS = (
    "google ai blog",
    "lilian weng",
    "hugging face",
    "langchain",
    "openai news",
)
VENTURE_TEXT_TOKENS = (
    "acquisition",
    "investment",
    "startup",
    "market",
    "infrastructure",
    "satellite",
    "network",
    "enterprise",
    "business",
)
TOP_NEWS_DOMAINS = {"ai-news"}


@dataclass(slots=True)
class CandidateStory:
    source: str
    item: dict[str, Any]
    key: str
    link: str
    content_type: str
    domain: str
    priority: float
    source_age_days: int
    recent_duplicate: bool


@dataclass(slots=True)
class DailyDigestPlan:
    date: str
    top_stories: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    venture_story: tuple[str, dict[str, Any]] | None = None
    growth_story: tuple[str, dict[str, Any]] | None = None
    solopreneur_story: tuple[str, dict[str, Any]] | None = None
    video_story: tuple[str, dict[str, Any]] | None = None
    social_story: tuple[str, dict[str, Any]] | None = None
    paper_radar: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    engineering_watch: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    action_queue: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    paper_written: list[dict[str, Any]] = field(default_factory=list)
    video_written: list[dict[str, Any]] = field(default_factory=list)
    paper_queue: list[dict[str, Any]] = field(default_factory=list)
    video_queue: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    selected_links: list[str] = field(default_factory=list)
    selected_sources: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    selection_notes: list[str] = field(default_factory=list)
    used_three_blue_one_brown: bool = False


def load_used_urls(path: Path) -> set[str]:
    payload = _load_json(path, {"articles": []})
    return {
        _normalize_url(str(item.get("url", "")))
        for item in payload.get("articles", [])
        if str(item.get("url", "")).strip()
    }


def load_rotation_state(path: Path) -> dict[str, Any]:
    return _load_json(path, {"sources": {}, "weekly_summary": {}})


def plan_daily_digest(
    items_by_source: dict[str, list[dict[str, Any]]],
    *,
    today: str,
    top_picks: int = 3,
    action_items: int = 3,
    max_deferred_items: int = 8,
    min_top_nonpaper: int = 3,
    min_top_content_types: int = 1,
    max_paper_in_top: int = 0,
    paper_written: list[dict[str, Any]] | None = None,
    video_written: list[dict[str, Any]] | None = None,
    paper_queue: list[dict[str, Any]] | None = None,
    video_queue: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    used_urls: set[str] | None = None,
    rotation_state: dict[str, Any] | None = None,
) -> DailyDigestPlan:
    del top_picks, min_top_nonpaper, min_top_content_types, max_paper_in_top

    used_urls = {_normalize_url(url) for url in (used_urls or set()) if url}
    rotation_state = rotation_state or {"sources": {}, "weekly_summary": {}}

    candidates = _build_candidates(items_by_source, today=today, used_urls=used_urls, rotation_state=rotation_state)
    selected_keys: set[str] = set()
    selection_notes: list[str] = []

    def reserve(candidate: CandidateStory | None) -> tuple[str, dict[str, Any]] | None:
        if candidate is None or candidate.key in selected_keys:
            return None
        selected_keys.add(candidate.key)
        return candidate.source, candidate.item

    top_stories = _select_group(
        candidates,
        selected_keys=selected_keys,
        predicate=_is_top_story,
        limit=3,
        prefer_unique_sources=True,
    )
    if len(top_stories) < 3:
        top_stories.extend(
            _select_group(
                candidates,
                selected_keys=selected_keys,
                predicate=_is_top_story_fallback,
                limit=3 - len(top_stories),
                prefer_unique_sources=True,
            )
        )

    venture_story = reserve(_pick_first(candidates, selected_keys, _is_venture_story))
    insight_story = reserve(_pick_first(candidates, selected_keys, _is_insight_story))

    force_three_blue = not bool(
        rotation_state.get("weekly_summary", {}).get("3blue1brown_used_this_week", False)
    )
    video_story = reserve(_pick_video_story(candidates, selected_keys, force_three_blue))
    if video_story and THREE_BLUE_ONE_BROWN in video_story[0].lower():
        selection_notes.append("3Blue1Brown was forced into the weekly video slot.")

    ai_company_story = reserve(_pick_first(candidates, selected_keys, _is_ai_company_story))

    action_queue = _build_action_queue(
        top_stories=top_stories,
        venture_story=venture_story,
        insight_story=insight_story,
        video_story=video_story,
        ai_company_story=ai_company_story,
        limit=action_items,
    )

    selected_pairs = [
        *top_stories,
        *(story for story in (venture_story, insight_story, video_story, ai_company_story) if story),
    ]
    selected_links = _unique_nonempty([_normalize_url(str(item.get("link", ""))) for _, item in selected_pairs])
    selected_sources = _unique_nonempty([source for source, _ in selected_pairs])

    snapshot = {
        "candidate_count": len(candidates),
        "filtered_duplicates": sum(1 for candidate in candidates if candidate.recent_duplicate),
        "selected_count": len(selected_pairs),
        "top_story_count": len(top_stories),
        "rotation_hits": sum(1 for source in selected_sources if _source_age_days(source, today, rotation_state) >= 4),
        "content_type_counts": _count_content_types(candidates),
    }

    return DailyDigestPlan(
        date=today,
        top_stories=top_stories,
        venture_story=venture_story,
        growth_story=insight_story,
        solopreneur_story=ai_company_story,
        video_story=video_story,
        action_queue=action_queue,
        paper_written=(paper_written or [])[:max_deferred_items],
        video_written=(video_written or [])[:max_deferred_items],
        paper_queue=(paper_queue or [])[:max_deferred_items],
        video_queue=(video_queue or [])[:max_deferred_items],
        stats=stats or {},
        selected_links=selected_links,
        selected_sources=selected_sources,
        snapshot=snapshot,
        selection_notes=selection_notes,
        used_three_blue_one_brown=bool(video_story and THREE_BLUE_ONE_BROWN in video_story[0].lower()),
    )


def persist_daily_digest_selection(
    plan: DailyDigestPlan,
    *,
    used_articles_path: Path,
    source_rotation_path: Path,
    retention_days: int = 30,
) -> None:
    if not plan.selected_links:
        return

    used_payload = _load_json(
        used_articles_path,
        {
            "description": "Tracks URLs used in past daily digests to prevent duplication.",
            "articles": [],
        },
    )
    articles = list(used_payload.get("articles", []))
    for link in plan.selected_links:
        articles.append({"date": plan.date, "url": link})

    cutoff = datetime.strptime(plan.date, "%Y-%m-%d").date() - timedelta(days=retention_days)
    normalized: dict[str, dict[str, str]] = {}
    for item in articles:
        url = _normalize_url(str(item.get("url", "")))
        date_str = str(item.get("date", "")).strip()
        if not url or not date_str:
            continue
        try:
            current = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if current < cutoff:
            continue
        previous = normalized.get(url)
        if previous is None or previous["date"] < date_str:
            normalized[url] = {"date": date_str, "url": url}

    used_payload["articles"] = sorted(normalized.values(), key=lambda row: (row["date"], row["url"]))
    used_articles_path.write_text(json.dumps(used_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    rotation_payload = load_rotation_state(source_rotation_path)
    sources = rotation_payload.setdefault("sources", {})
    for source in plan.selected_sources:
        entry = sources.setdefault(source, {"category": _guess_rotation_category(source), "last_used": plan.date})
        entry["last_used"] = plan.date

    weekly = rotation_payload.setdefault("weekly_summary", {})
    weekly.setdefault("week_start", _week_start(plan.date))
    if plan.used_three_blue_one_brown:
        weekly["3blue1brown_used_this_week"] = True

    source_rotation_path.write_text(
        json.dumps(rotation_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _build_candidates(
    items_by_source: dict[str, list[dict[str, Any]]],
    *,
    today: str,
    used_urls: set[str],
    rotation_state: dict[str, Any],
) -> list[CandidateStory]:
    deduped: dict[str, CandidateStory] = {}
    for source, items in items_by_source.items():
        for raw_item in items:
            item = dict(raw_item)
            link = _normalize_url(str(item.get("link", "")))
            title = str(item.get("title", "")).strip()
            key = link or f"{source}|{title}|{str(item.get('published', '')).strip()}"
            domain = str(item.get("domain", "")).strip().lower()
            content_type = str(item.get("content_type") or "news").strip().lower()
            candidate = CandidateStory(
                source=source,
                item=item,
                key=key,
                link=link,
                content_type=content_type or "news",
                domain=domain,
                priority=_candidate_priority(source, item, today=today, rotation_state=rotation_state),
                source_age_days=_source_age_days(source, today, rotation_state),
                recent_duplicate=bool(link and link in used_urls),
            )
            previous = deduped.get(key)
            if previous is None or previous.priority < candidate.priority:
                deduped[key] = candidate

    return sorted(
        deduped.values(),
        key=lambda candidate: (
            candidate.recent_duplicate,
            -candidate.priority,
            candidate.source.lower(),
            str(candidate.item.get("title", "")).lower(),
        ),
    )


def _select_group(
    candidates: list[CandidateStory],
    *,
    selected_keys: set[str],
    predicate: Callable[[CandidateStory], bool],
    limit: int,
    prefer_unique_sources: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    picked: list[tuple[str, dict[str, Any]]] = []
    used_sources: set[str] = set()
    for candidate in candidates:
        if len(picked) >= limit:
            break
        if candidate.key in selected_keys or candidate.recent_duplicate or not predicate(candidate):
            continue
        if prefer_unique_sources and candidate.source in used_sources:
            continue
        selected_keys.add(candidate.key)
        used_sources.add(candidate.source)
        picked.append((candidate.source, candidate.item))

    if len(picked) >= limit or not prefer_unique_sources:
        return picked

    for candidate in candidates:
        if len(picked) >= limit:
            break
        if candidate.key in selected_keys or candidate.recent_duplicate or not predicate(candidate):
            continue
        selected_keys.add(candidate.key)
        picked.append((candidate.source, candidate.item))
    return picked


def _pick_first(
    candidates: list[CandidateStory],
    selected_keys: set[str],
    predicate: Callable[[CandidateStory], bool],
) -> CandidateStory | None:
    for candidate in candidates:
        if candidate.key in selected_keys or candidate.recent_duplicate:
            continue
        if predicate(candidate):
            return candidate
    return None


def _pick_video_story(
    candidates: list[CandidateStory],
    selected_keys: set[str],
    force_three_blue: bool,
) -> CandidateStory | None:
    if force_three_blue:
        forced = _pick_first(
            candidates,
            selected_keys,
            lambda candidate: candidate.content_type == "video"
            and THREE_BLUE_ONE_BROWN in candidate.source.lower(),
        )
        if forced is not None:
            return forced
    return _pick_first(candidates, selected_keys, lambda candidate: candidate.content_type == "video")


def _build_action_queue(
    *,
    top_stories: list[tuple[str, dict[str, Any]]],
    venture_story: tuple[str, dict[str, Any]] | None,
    insight_story: tuple[str, dict[str, Any]] | None,
    video_story: tuple[str, dict[str, Any]] | None,
    ai_company_story: tuple[str, dict[str, Any]] | None,
    limit: int,
) -> list[tuple[str, dict[str, Any]]]:
    ordered = [
        *top_stories,
        *(story for story in (venture_story, insight_story, video_story, ai_company_story) if story),
    ]
    result: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for source, item in ordered:
        key = _normalize_url(str(item.get("link", ""))) or f"{source}|{item.get('title', '')}"
        if key in seen:
            continue
        seen.add(key)
        result.append((source, item))
        if len(result) >= limit:
            break
    return result


def _candidate_priority(
    source: str,
    item: dict[str, Any],
    *,
    today: str,
    rotation_state: dict[str, Any],
) -> float:
    score = float(item.get("score", 0) or 0)
    freshness = _freshness_bonus(str(item.get("published", "")).strip(), today)
    rotation = min(_source_age_days(source, today, rotation_state), 10) * 0.25
    source_bonus = 0.0
    text = f"{source} {item.get('title', '')} {item.get('summary', '')}".lower()
    domain = str(item.get("domain", "")).strip().lower()
    content_type = str(item.get("content_type", "")).strip().lower()

    if domain in TOP_NEWS_DOMAINS:
        source_bonus += 1.5
    if any(token in source.lower() for token in VENTURE_SOURCE_TOKENS):
        source_bonus += 0.8
    if any(token in source.lower() for token in AI_COMPANY_SOURCE_TOKENS):
        source_bonus += 0.7
    if any(token in source.lower() for token in INSIGHT_SOURCE_TOKENS):
        source_bonus += 0.5
    if content_type == "video":
        source_bonus += 0.4
    if content_type == "paper":
        source_bonus += 0.3
    if any(token in text for token in ("agent", "coding", "workflow", "evaluation", "tool", "prompt", "model")):
        source_bonus += 0.8
    if any(token in text for token in VENTURE_TEXT_TOKENS):
        source_bonus += 0.5

    return round(score + freshness + rotation + source_bonus, 3)


def _is_top_story(candidate: CandidateStory) -> bool:
    return (
        candidate.content_type not in {"paper", "video"}
        and candidate.domain in TOP_NEWS_DOMAINS
    )


def _is_top_story_fallback(candidate: CandidateStory) -> bool:
    return (
        candidate.content_type not in {"paper", "video"}
        and candidate.domain in {"ai-news", "ai-company", "growth"}
    )


def _is_venture_story(candidate: CandidateStory) -> bool:
    if candidate.content_type in {"paper", "video"}:
        return False
    source_lower = candidate.source.lower()
    return candidate.domain == "venture" or any(token in source_lower for token in VENTURE_SOURCE_TOKENS)


def _is_insight_story(candidate: CandidateStory) -> bool:
    if candidate.content_type == "video":
        return False
    source_lower = candidate.source.lower()
    if any(token in source_lower for token in INSIGHT_SOURCE_TOKENS):
        return True
    return candidate.content_type == "paper"


def _is_ai_company_story(candidate: CandidateStory) -> bool:
    if candidate.content_type in {"paper", "video"}:
        return False
    source_lower = candidate.source.lower()
    if any(token in source_lower for token in AI_COMPANY_SOURCE_TOKENS):
        return True
    domain = candidate.domain.lower()
    return domain in {"solopreneur", "ai-company"}


def _source_age_days(source: str, today: str, rotation_state: dict[str, Any]) -> int:
    raw_date = str(rotation_state.get("sources", {}).get(source, {}).get("last_used", "")).strip()
    if not raw_date:
        return 999
    try:
        last_used = datetime.strptime(raw_date, "%Y-%m-%d").date()
        current = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return 999
    return max((current - last_used).days, 0)


def _freshness_bonus(published: str, today: str) -> float:
    if not published:
        return 0.0
    try:
        published_day = datetime.strptime(published[:10], "%Y-%m-%d").date()
        current_day = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    delta = max((current_day - published_day).days, 0)
    if delta <= 1:
        return 0.8
    if delta <= 3:
        return 0.4
    return 0.0


def _count_content_types(candidates: list[CandidateStory]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.content_type] = counts.get(candidate.content_type, 0) + 1
    return counts


def _guess_rotation_category(source: str) -> str:
    lowered = source.lower()
    if THREE_BLUE_ONE_BROWN in lowered or "youtube" in lowered:
        return "YouTube"
    if any(token in lowered for token in VENTURE_SOURCE_TOKENS):
        return "Venture Insight"
    if any(token in lowered for token in AI_COMPANY_SOURCE_TOKENS):
        return "AI Company"
    if any(token in lowered for token in INSIGHT_SOURCE_TOKENS):
        return "Insight"
    return "AI News"


def _week_start(date_str: str) -> str:
    current = datetime.strptime(date_str, "%Y-%m-%d").date()
    monday = current - timedelta(days=current.weekday())
    return monday.strftime("%Y-%m-%d")


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _normalize_url(url: str) -> str:
    normalized = url.strip()
    if not normalized:
        return ""
    normalized = normalized.rstrip("/")
    normalized = normalized.replace("http://", "https://", 1)
    return normalized


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
