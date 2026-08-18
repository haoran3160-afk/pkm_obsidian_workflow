#!/usr/bin/env python3
"""
daily_curation.py - Local-parity selection and state management for AI digest output.

The local workflow quality comes from strict section quotas, duplicate filtering,
source rotation, and a curated final slate. This module mirrors that behavior.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import state_schema
from workflow_contracts import (
    DuplicateKind,
    DuplicateRecord,
    IntentKind,
    IntentPortfolio,
    PortfolioCandidate,
    SemanticClusterProposal,
)

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


class CurationPolicyError(ValueError):
    """The candidate pool cannot satisfy the declared digest policy."""


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


def build_intent_portfolio(
    items_by_source: dict[str, list[dict[str, Any]]],
    *,
    source_intents: dict[str, list[IntentKind]] | None = None,
) -> IntentPortfolio:
    """Place every raw candidate into one or more deterministic intent pools."""

    explicit = source_intents or {}
    pools: dict[str, list[PortfolioCandidate]] = {intent.value: [] for intent in IntentKind}
    all_candidates: list[PortfolioCandidate] = []
    for source in sorted(items_by_source, key=str.casefold):
        source_id = "source-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        for origin_index, raw_item in enumerate(items_by_source[source]):
            title = str(raw_item.get("title", "")).strip()
            link = _normalize_url(str(raw_item.get("link", "")).strip())
            if not title or not link.startswith(("http://", "https://")):
                raise CurationPolicyError(
                    "portfolio candidates require title and canonical HTTP URL"
                )
            summary = str(raw_item.get("summary", "")).strip()
            content_hash = hashlib.sha256(f"{title.casefold()}\n{summary}".encode()).hexdigest()
            item_id = (
                "item-"
                + hashlib.sha256(
                    (
                        f"{source}\n{origin_index}\n{link}\n"
                        f"{title}\n{raw_item.get('published', '')}"
                    ).encode()
                ).hexdigest()[:24]
            )
            inferred = _infer_intents(source, raw_item)
            inferred.update(explicit.get(source, []))
            ordered_intents = [intent for intent in IntentKind if intent in inferred]
            raw_ref = str(raw_item.get("raw_ref", "")).strip()
            evidence_refs = [raw_ref] if raw_ref else [f"raw:{item_id}"]
            candidate = PortfolioCandidate(
                item_id=item_id,
                source_id=source_id,
                source_name=source,
                title=title,
                canonical_url=link,
                content_hash=content_hash,
                content_type=str(raw_item.get("content_type") or "article").strip().lower(),
                intents=ordered_intents,
                evidence_refs=evidence_refs,
            )
            all_candidates.append(candidate)
            for intent in candidate.intents:
                pools[intent.value].append(candidate)
    for candidates in pools.values():
        candidates.sort(key=lambda candidate: candidate.item_id)
    return IntentPortfolio(
        pools=pools,
        all_item_ids=[
            candidate.item_id
            for candidate in sorted(
                all_candidates,
                key=lambda candidate: candidate.item_id,
            )
        ],
    )


def _infer_intents(
    source: str,
    item: dict[str, Any],
) -> set[IntentKind]:
    source_text = source.casefold()
    content_type = str(item.get("content_type") or "").casefold()
    domain = str(item.get("domain") or "").casefold()
    text = (f"{source} {item.get('title', '')} {item.get('summary', '')}").casefold()
    intents: set[IntentKind] = set()
    if domain in {"ai-news", "ai-company"} or any(
        token in source_text for token in ("openai", "google ai", "anthropic", "hugging face")
    ):
        intents.add(IntentKind.OFFICIAL_AI)
    if content_type in {"engineering", "code", "tool"} or any(
        token in text for token in ("agent", "coding", "workflow", "api", "evaluation")
    ):
        intents.add(IntentKind.ENGINEERING)
    if content_type == "paper" or domain == "research" or "arxiv" in text:
        intents.add(IntentKind.RESEARCH)
    if domain in {"venture", "growth", "market"} or any(
        token in text for token in ("market", "startup", "revenue", "growth", "investment")
    ):
        intents.add(IntentKind.MARKET_GROWTH)
    if content_type == "video" or "youtube" in text:
        intents.add(IntentKind.VIDEO)
    if domain == "ielts" or "ielts" in text:
        intents.add(IntentKind.IELTS)
    if not intents:
        intents.add(IntentKind.ENGINEERING)
    return intents


def _unique_candidates(portfolio: IntentPortfolio) -> dict[str, PortfolioCandidate]:
    return {
        candidate.item_id: candidate
        for candidates in portfolio.pools.values()
        for candidate in candidates
    }


def record_python_duplicates(
    portfolio: IntentPortfolio,
) -> list[DuplicateRecord]:
    """Record deterministic URL/content duplicates without removing candidates."""

    candidates = _unique_candidates(portfolio)
    groups: list[tuple[DuplicateKind, dict[str, list[PortfolioCandidate]]]] = [
        (DuplicateKind.EXACT, {}),
        (DuplicateKind.CONTENT, {}),
    ]
    for candidate in candidates.values():
        groups[0][1].setdefault(
            candidate.canonical_url.casefold(),
            [],
        ).append(candidate)
        groups[1][1].setdefault(candidate.content_hash, []).append(candidate)

    records: list[DuplicateRecord] = []
    for kind, grouped in groups:
        for group_key, members in sorted(grouped.items()):
            unique_urls = {member.canonical_url for member in members}
            if len(members) < 2:
                continue
            if kind is DuplicateKind.CONTENT and len(unique_urls) < 2:
                continue
            ordered = sorted(members, key=lambda member: member.item_id)
            member_ids = [member.item_id for member in ordered]
            cluster_id = (
                f"{kind.value}-"
                + hashlib.sha256(f"{kind.value}\n{group_key}\n{member_ids}".encode()).hexdigest()[
                    :20
                ]
            )
            records.append(
                DuplicateRecord(
                    cluster_id=cluster_id,
                    duplicate_kind=kind,
                    member_item_ids=member_ids,
                    representative_item_id=member_ids[0],
                    evidence_refs=_unique_nonempty(
                        [evidence for member in ordered for evidence in member.evidence_refs]
                    ),
                )
            )
    return records


def validate_semantic_clusters(
    portfolio: IntentPortfolio,
    proposals: list[SemanticClusterProposal],
) -> list[SemanticClusterProposal]:
    """Validate model-proposed clusters against immutable Python candidates."""

    candidates = _unique_candidates(portfolio)
    claimed: set[str] = set()
    result: list[SemanticClusterProposal] = []
    for proposal in proposals:
        member_ids = set(proposal.member_item_ids)
        unknown = member_ids - set(candidates)
        if unknown:
            raise CurationPolicyError("semantic cluster contains unknown item IDs")
        overlap = member_ids & claimed
        if overlap:
            raise CurationPolicyError("an item cannot occupy multiple semantic cluster slots")
        required_evidence = {
            evidence for item_id in member_ids for evidence in candidates[item_id].evidence_refs
        }
        if set(proposal.evidence_refs) != required_evidence:
            raise CurationPolicyError("semantic cluster evidence must preserve every member source")
        claimed.update(member_ids)
        result.append(proposal)
    return sorted(result, key=lambda proposal: proposal.cluster_id)


def load_used_urls(path: Path) -> set[str]:
    payload = state_schema.load_used_articles_state(path)
    return {_normalize_url(article.url) for article in payload.articles if article.url.strip()}


def load_rotation_state(path: Path) -> dict[str, Any]:
    return state_schema.load_source_rotation_state(path).model_dump()


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
    used_urls = {_normalize_url(url) for url in (used_urls or set()) if url}
    rotation_state = rotation_state or {"sources": {}, "weekly_summary": {}}

    candidates = _build_candidates(
        items_by_source, today=today, used_urls=used_urls, rotation_state=rotation_state
    )
    selected_keys: set[str] = set()
    selection_notes: list[str] = []

    def reserve(candidate: CandidateStory | None) -> tuple[str, dict[str, Any]] | None:
        if candidate is None or candidate.key in selected_keys:
            return None
        selected_keys.add(candidate.key)
        return candidate.source, candidate.item

    top_stories = _select_policy_top_stories(
        candidates,
        selected_keys=selected_keys,
        top_picks=top_picks,
        min_top_nonpaper=min_top_nonpaper,
        min_top_content_types=min_top_content_types,
        max_paper_in_top=max_paper_in_top,
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
        *(
            story
            for story in (venture_story, insight_story, video_story, ai_company_story)
            if story
        ),
    ]
    selected_links = _unique_nonempty(
        [_normalize_url(str(item.get("link", ""))) for _, item in selected_pairs]
    )
    selected_sources = _unique_nonempty([source for source, _ in selected_pairs])

    snapshot = {
        "candidate_count": len(candidates),
        "filtered_duplicates": sum(1 for candidate in candidates if candidate.recent_duplicate),
        "selected_count": len(selected_pairs),
        "top_story_count": len(top_stories),
        "rotation_hits": sum(
            1 for source in selected_sources if _source_age_days(source, today, rotation_state) >= 4
        ),
        "content_type_counts": _count_content_types(candidates),
        "policy": {
            "top_picks": top_picks,
            "min_top_nonpaper": min_top_nonpaper,
            "min_top_content_types": min_top_content_types,
            "max_paper_in_top": max_paper_in_top,
        },
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
        used_three_blue_one_brown=bool(
            video_story and THREE_BLUE_ONE_BROWN in video_story[0].lower()
        ),
    )


def _select_policy_top_stories(
    candidates: list[CandidateStory],
    *,
    selected_keys: set[str],
    top_picks: int,
    min_top_nonpaper: int,
    min_top_content_types: int,
    max_paper_in_top: int,
) -> list[tuple[str, dict[str, Any]]]:
    if top_picks < 1:
        raise CurationPolicyError("top_picks must be at least 1")
    if not 0 <= min_top_nonpaper <= top_picks:
        raise CurationPolicyError("minimum nonpaper stories must be between 0 and top_picks")
    if not 1 <= min_top_content_types <= top_picks:
        raise CurationPolicyError("minimum content types must be between 1 and top_picks")
    if not 0 <= max_paper_in_top <= top_picks:
        raise CurationPolicyError("maximum papers must be between 0 and top_picks")

    eligible = [
        candidate
        for candidate in candidates
        if not candidate.recent_duplicate
        and candidate.content_type != "video"
        and (
            candidate.content_type == "paper"
            or candidate.domain in {"ai-news", "ai-company", "growth", "research"}
        )
    ]
    if len(eligible) < top_picks:
        raise CurationPolicyError(
            f"top_picks requires {top_picks} stories but only "
            f"{len(eligible)} eligible candidates exist"
        )
    available_types = {candidate.content_type for candidate in eligible}
    if len(available_types) < min_top_content_types:
        raise CurationPolicyError(
            f"minimum content types requires {min_top_content_types}, "
            f"only {len(available_types)} are available"
        )

    picked: list[CandidateStory] = []
    picked_keys: set[str] = set()

    def can_pick(candidate: CandidateStory) -> bool:
        if candidate.key in picked_keys:
            return False
        paper_count = sum(item.content_type == "paper" for item in picked)
        return not (candidate.content_type == "paper" and paper_count >= max_paper_in_top)

    def add(candidate: CandidateStory) -> None:
        picked.append(candidate)
        picked_keys.add(candidate.key)

    if min_top_content_types > 1:
        for candidate in eligible:
            represented = {item.content_type for item in picked}
            if candidate.content_type in represented or not can_pick(candidate):
                continue
            add(candidate)
            if len({item.content_type for item in picked}) >= min_top_content_types:
                break

    for candidate in eligible:
        nonpaper_count = sum(item.content_type != "paper" for item in picked)
        if nonpaper_count >= min_top_nonpaper:
            break
        if candidate.content_type == "paper" or not can_pick(candidate):
            continue
        add(candidate)

    for candidate in eligible:
        if len(picked) >= top_picks:
            break
        if can_pick(candidate):
            add(candidate)

    if len(picked) != top_picks:
        raise CurationPolicyError("paper limit and nonpaper minimum leave too few top stories")
    nonpaper_count = sum(item.content_type != "paper" for item in picked)
    content_type_count = len({item.content_type for item in picked})
    paper_count = sum(item.content_type == "paper" for item in picked)
    if nonpaper_count < min_top_nonpaper:
        raise CurationPolicyError("minimum nonpaper story policy was not satisfied")
    if content_type_count < min_top_content_types:
        raise CurationPolicyError("minimum content types policy was not satisfied")
    if paper_count > max_paper_in_top:
        raise CurationPolicyError("maximum paper policy was not satisfied")

    selected_keys.update(picked_keys)
    return [(candidate.source, candidate.item) for candidate in picked]


def persist_daily_digest_selection(
    plan: DailyDigestPlan,
    *,
    used_articles_path: Path,
    source_rotation_path: Path,
    retention_days: int = 30,
) -> None:
    if not plan.selected_links:
        return

    used_payload = state_schema.load_used_articles_state(used_articles_path)
    articles = list(used_payload.articles)
    for link in plan.selected_links:
        articles.append(state_schema.UsedArticleEntry(date=plan.date, url=link))

    cutoff = datetime.strptime(plan.date, "%Y-%m-%d").date() - timedelta(days=retention_days)
    normalized: dict[str, state_schema.UsedArticleEntry] = {}
    for item in articles:
        url = _normalize_url(item.url)
        current = datetime.strptime(item.date, "%Y-%m-%d").date()
        if current < cutoff:
            continue
        previous = normalized.get(url)
        if previous is None or previous.date < item.date:
            normalized[url] = state_schema.UsedArticleEntry(date=item.date, url=url)

    used_payload.articles = sorted(normalized.values(), key=lambda row: (row.date, row.url))
    state_schema.save_used_articles_state(used_articles_path, used_payload)

    rotation_payload = state_schema.load_source_rotation_state(source_rotation_path)
    sources = rotation_payload.sources
    for source in plan.selected_sources:
        entry = sources.setdefault(
            source,
            state_schema.SourceRotationEntry(
                category=_guess_rotation_category(source),
                last_used=plan.date,
            ),
        )
        entry.last_used = plan.date

    weekly = rotation_payload.weekly_summary
    if not weekly.week_start:
        weekly.week_start = _week_start(plan.date)
    if plan.used_three_blue_one_brown:
        weekly.threeblue1brown_used_this_week = True

    state_schema.save_source_rotation_state(source_rotation_path, rotation_payload)


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
                priority=_candidate_priority(
                    source, item, today=today, rotation_state=rotation_state
                ),
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
            lambda candidate: (
                candidate.content_type == "video"
                and THREE_BLUE_ONE_BROWN in candidate.source.lower()
            ),
        )
        if forced is not None:
            return forced
    return _pick_first(
        candidates, selected_keys, lambda candidate: candidate.content_type == "video"
    )


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
        *(
            story
            for story in (venture_story, insight_story, video_story, ai_company_story)
            if story
        ),
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
    if any(
        token in text
        for token in ("agent", "coding", "workflow", "evaluation", "tool", "prompt", "model")
    ):
        source_bonus += 0.8
    if any(token in text for token in VENTURE_TEXT_TOKENS):
        source_bonus += 0.5

    return round(score + freshness + rotation + source_bonus, 3)


def _is_top_story(candidate: CandidateStory) -> bool:
    return candidate.content_type not in {"paper", "video"} and candidate.domain in TOP_NEWS_DOMAINS


def _is_top_story_fallback(candidate: CandidateStory) -> bool:
    return candidate.content_type not in {"paper", "video"} and candidate.domain in {
        "ai-news",
        "ai-company",
        "growth",
    }


def _is_venture_story(candidate: CandidateStory) -> bool:
    if candidate.content_type in {"paper", "video"}:
        return False
    source_lower = candidate.source.lower()
    return candidate.domain == "venture" or any(
        token in source_lower for token in VENTURE_SOURCE_TOKENS
    )


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
