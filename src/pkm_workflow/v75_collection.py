"""v7.5 metadata-first source collection and bounded evidence refill."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

from .source_catalog import SourceCatalog, SourceDefinition
from .user_context_v75 import UserContextBundle


class CoverageLevel(str, Enum):
    A = "A"
    B = "B"
    INSUFFICIENT = "INSUFFICIENT"


class AccessState(str, Enum):
    FULL_FREE = "FULL_FREE"
    PAYWALL_OR_PREVIEW_ONLY = "PAYWALL_OR_PREVIEW_ONLY"
    FETCH_FAILED = "FETCH_FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MetadataItem:
    item_native_id: str
    title: str
    canonical_url: str
    published_at: str
    summary: str
    content_type: str


@dataclass(frozen=True)
class MetadataObservation:
    healthy: bool
    zero_yield: bool
    reason_code: str
    items: tuple[MetadataItem, ...]


@dataclass(frozen=True)
class CollectionPorts:
    fetch_metadata: Callable[[SourceDefinition, date], MetadataObservation]
    fetch_fulltext: Callable[[str], str]


@dataclass(frozen=True)
class Candidate:
    evidence_id: str
    source: str
    title: str
    link: str
    published: str
    summary: str
    content_type: str
    fulltext_enriched: bool
    profile_refs: tuple[str, ...] = ()
    source_id: str = "legacy-source"
    canonical_origin_id: str = "legacy-origin"
    evidence_role: str = "PRIMARY_OR_EXPERT"
    pillars: tuple[str, ...] = ("AI_MASTERY",)
    metadata_score: int = 5000
    access_state: AccessState = AccessState.FULL_FREE
    evidence_kind: str = "CANONICAL_FULLTEXT"

    def model_payload(self) -> dict[str, object]:
        return {
            "access_state": self.access_state.value,
            "canonical_origin_id": self.canonical_origin_id,
            "content_type": self.content_type,
            "evidence_id": self.evidence_id,
            "evidence_kind": self.evidence_kind,
            "evidence_role": self.evidence_role,
            "fulltext_enriched": self.fulltext_enriched,
            "metadata_score": self.metadata_score,
            "pillars": list(self.pillars),
            "profile_refs": list(self.profile_refs),
            "published": self.published,
            "source": self.source,
            "source_id": self.source_id,
            "summary": self.summary,
            "title": self.title,
        }


@dataclass(frozen=True)
class CollectionResult:
    coverage: CoverageLevel
    configured_sources: int
    healthy_sources: int
    candidates: tuple[Candidate, ...]
    evidence_level: CoverageLevel = CoverageLevel.A
    evidence_healthy_sources: int = 0
    audit: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _RankedItem:
    source: SourceDefinition
    item: MetadataItem
    metadata_score: int


_HYPE_TERMS = ("sponsored", "webinar", "conference tickets", "coupon")
_PAYWALL_TERMS = (
    "subscribe to continue",
    "paid subscribers",
    "sign in to read",
    "already a subscriber",
)


def _policy_int(policies: Mapping[str, object], name: str) -> int:
    value = policies.get(name)
    if type(value) is not int:
        raise ValueError(f"INVALID_POLICY_INTEGER:{name}")
    return value


def _policy_mapping(policies: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = policies.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"INVALID_POLICY_MAPPING:{name}")
    return value


def _coverage_level(healthy: int, total: int, *, a_bps: int, b_bps: int) -> CoverageLevel:
    if total <= 0:
        return CoverageLevel.INSUFFICIENT
    value = healthy * 10_000 // total
    if value >= a_bps:
        return CoverageLevel.A
    if value >= b_bps:
        return CoverageLevel.B
    return CoverageLevel.INSUFFICIENT


def _is_due(source: SourceDefinition, content_date: date) -> bool:
    if source.operational_status != "ACTIVE":
        return False
    if source.cadence == "DAILY":
        return True
    if source.cadence == "WEEKDAY":
        return content_date.weekday() < 5
    if source.cadence == "WEEKLY":
        return content_date.weekday() == 6
    return False


def _normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            parts.query,
            "",
        )
    )


def _published_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _freshness_score(value: str, content_date: date) -> int:
    published = _published_date(value)
    if published is None or published > content_date:
        return 0
    age = (content_date - published).days
    if age <= 1:
        return 1000
    if age <= 3:
        return 600
    if age <= 7:
        return 200
    return 0


def _metadata_score(
    source: SourceDefinition,
    item: MetadataItem,
    *,
    content_date: date,
    user_context: UserContextBundle,
    source_quality_scores: Mapping[str, object],
) -> int:
    weights = {pillar.pillar_id: pillar.weight_bps for pillar in user_context.pillars}
    primary_weight = weights[source.pillars[0]]
    pillar_relevance = min(4000, primary_weight * 4000 // 3500)
    quality_value = source_quality_scores[source.evidence_role]
    if type(quality_value) is not int:
        raise ValueError("SOURCE_QUALITY_SCORE_INVALID")
    evidence_precheck = 1500 if item.content_type == "paper" else 1000
    if len(item.summary.strip()) < 200:
        evidence_precheck = 0
    text = f"{item.title}\n{item.summary}".casefold()
    if any(term.casefold() in text for term in source.exclude_terms):
        return -1
    hype_penalty = -1000 if any(term in text for term in _HYPE_TERMS) else 0
    return (
        pillar_relevance
        + quality_value
        + 1500
        + evidence_precheck
        + _freshness_score(item.published_at, content_date)
        + hype_penalty
    )


def _ranked_metadata(
    *,
    observations: Mapping[str, MetadataObservation],
    sources: Mapping[str, SourceDefinition],
    content_date: date,
    user_context: UserContextBundle,
    source_quality_scores: Mapping[str, object],
    quality_threshold: int,
    used_urls: set[str],
) -> tuple[_RankedItem, ...]:
    ranked: list[_RankedItem] = []
    seen_urls: set[str] = set()
    for source_id, observation in observations.items():
        source = sources[source_id]
        if source.evidence_role == "DISCOVERY_PROVENANCE" or source.selection_role in {
            "MARKET_SENTINEL",
            "OUT_OF_SCOPE",
        }:
            continue
        for item in observation.items:
            url = _normalize_url(item.canonical_url)
            if not url or url in seen_urls or url in used_urls:
                continue
            published = _published_date(item.published_at)
            if published is None or not content_date - timedelta(days=7) <= published <= content_date:
                continue
            score = _metadata_score(
                source,
                item,
                content_date=content_date,
                user_context=user_context,
                source_quality_scores=source_quality_scores,
            )
            if score < quality_threshold:
                continue
            seen_urls.add(url)
            ranked.append(_RankedItem(source=source, item=item, metadata_score=score))
    ranked.sort(
        key=lambda row: (
            -row.metadata_score,
            -(_published_date(row.item.published_at) or date.min).toordinal(),
            row.source.canonical_origin_id,
            row.source.source_id,
            row.item.item_native_id,
        )
    )
    exposed: list[_RankedItem] = []
    selected_ids: set[tuple[str, str]] = set()
    for pillar in ("AGENTIC_RESEARCH", "AI_MASTERY", "BUILDERS", "VC", "COGNITION"):
        match = next((row for row in ranked if row.source.pillars[0] == pillar), None)
        if match is not None:
            exposed.append(match)
            selected_ids.add((match.source.source_id, match.item.item_native_id))
    exposed.extend(
        row
        for row in ranked
        if (row.source.source_id, row.item.item_native_id) not in selected_ids
    )
    return tuple(exposed)


def _is_public_fulltext(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) < 200:
        return False
    lowered = normalized.casefold()[:2000]
    return not any(term in lowered for term in _PAYWALL_TERMS)


def _candidate(row: _RankedItem, evidence_text: str, evidence_kind: str) -> Candidate:
    source = row.source
    item = row.item
    evidence_id = "evidence-" + hashlib.sha256(
        f"{source.source_id}\n{item.item_native_id}\n{_normalize_url(item.canonical_url)}".encode()
    ).hexdigest()[:24]
    return Candidate(
        evidence_id=evidence_id,
        source=source.name,
        title=item.title.strip(),
        link=item.canonical_url.strip(),
        published=item.published_at.strip(),
        summary=evidence_text.strip()[:12_000],
        content_type=item.content_type.strip(),
        fulltext_enriched=evidence_kind == "CANONICAL_FULLTEXT",
        source_id=source.source_id,
        canonical_origin_id=source.canonical_origin_id,
        evidence_role=source.evidence_role,
        pillars=source.pillars,
        metadata_score=row.metadata_score,
        access_state=AccessState.FULL_FREE,
        evidence_kind=evidence_kind,
    )


def collect_v75_candidates(
    content_date: date,
    *,
    catalog: SourceCatalog,
    user_context: UserContextBundle,
    used_urls: set[str],
    ports: CollectionPorts,
) -> CollectionResult:
    """Collect one deterministic Top8 without persisting cache, rotation, or history."""

    policies = catalog.policies
    due_sources = tuple(source for source in catalog.sources if _is_due(source, content_date))
    observations = {
        source.source_id: ports.fetch_metadata(source, content_date) for source in due_sources
    }
    coverage_sources = tuple(
        source
        for source in due_sources
        if source.selection_role == "CORE_DAILY" and source.coverage_member
    )
    healthy_sources = sum(
        observations[source.source_id].healthy for source in coverage_sources
    )
    assessment_source_ids = {
        source.source_id
        for source in coverage_sources
        if observations[source.source_id].healthy
        and (
            observations[source.source_id].zero_yield
            or any(
                len(item.summary.strip()) >= 200
                for item in observations[source.source_id].items
            )
        )
    }
    coverage = _coverage_level(
        healthy_sources,
        len(coverage_sources),
        a_bps=_policy_int(policies, "operational_coverage_a_bps"),
        b_bps=_policy_int(policies, "operational_coverage_b_bps"),
    )
    ranked = _ranked_metadata(
        observations=observations,
        sources={source.source_id: source for source in due_sources},
        content_date=content_date,
        user_context=user_context,
        source_quality_scores=_policy_mapping(policies, "source_quality_scores"),
        quality_threshold=_policy_int(policies, "metadata_quality_threshold"),
        used_urls={_normalize_url(url) for url in used_urls},
    )
    max_candidates = _policy_int(policies, "max_admissible_candidates")
    max_attempts = _policy_int(policies, "max_fulltext_attempts")
    attempts = 0
    candidates: list[Candidate] = []
    source_attempts: dict[str, int] = {}
    source_candidates: dict[str, int] = {}
    exclusions: list[dict[str, str]] = []
    for row in ranked:
        if len(candidates) >= max_candidates or attempts >= max_attempts:
            break
        source_id = row.source.source_id
        if source_candidates.get(source_id, 0) >= 2 or source_attempts.get(source_id, 0) >= 2:
            continue
        if row.item.content_type == "paper" and len(row.item.summary.strip()) >= 200:
            candidates.append(_candidate(row, row.item.summary, "PUBLIC_ABSTRACT"))
            source_candidates[source_id] = source_candidates.get(source_id, 0) + 1
            continue
        attempts += 1
        source_attempts[source_id] = source_attempts.get(source_id, 0) + 1
        fulltext = ports.fetch_fulltext(row.item.canonical_url)
        if not _is_public_fulltext(fulltext):
            exclusions.append(
                {
                    "source_id": row.source.source_id,
                    "item_native_id": row.item.item_native_id,
                    "reason": "FULL_FREE_EVIDENCE_UNAVAILABLE",
                }
            )
            continue
        candidates.append(_candidate(row, fulltext, "CANONICAL_FULLTEXT"))
        source_candidates[source_id] = source_candidates.get(source_id, 0) + 1
    assessment_source_ids.update(
        candidate.source_id
        for candidate in candidates
        if candidate.source_id
        in {source.source_id for source in coverage_sources}
    )
    evidence_level = _coverage_level(
        len(assessment_source_ids),
        healthy_sources,
        a_bps=_policy_int(policies, "evidence_availability_a_bps"),
        b_bps=_policy_int(policies, "evidence_availability_b_bps"),
    )
    return CollectionResult(
        coverage=coverage,
        configured_sources=len(coverage_sources),
        healthy_sources=healthy_sources,
        candidates=tuple(candidates),
        evidence_level=evidence_level,
        evidence_healthy_sources=len(assessment_source_ids),
        audit={
            "due_source_ids": [source.source_id for source in due_sources],
            "source_observations": [
                {
                    "source_id": source.source_id,
                    "selection_role": source.selection_role,
                    "coverage_member": source.coverage_member,
                    "healthy": observations[source.source_id].healthy,
                    "zero_yield": observations[source.source_id].zero_yield,
                    "reason_code": observations[source.source_id].reason_code,
                    "item_count": len(observations[source.source_id].items),
                }
                for source in due_sources
            ],
            "metadata_qualified_count": len(ranked),
            "fulltext_attempt_count": attempts,
            "exclusions": exclusions,
            "candidate_audit": [
                {
                    "evidence_id": candidate.evidence_id,
                    "source_id": candidate.source_id,
                    "pillars": list(candidate.pillars),
                    "metadata_score": candidate.metadata_score,
                    "access_state": candidate.access_state.value,
                    "evidence_kind": candidate.evidence_kind,
                    "published": candidate.published,
                }
                for candidate in candidates
            ],
        },
    )


def default_collection_ports(catalog: SourceCatalog) -> CollectionPorts:
    """Adapt the existing RSS/fulltext fetcher without enabling legacy curation."""

    import fetcher

    limits = _policy_mapping(catalog.policies, "limits")
    summary_limit = _policy_int(limits, "metadata_summary_chars")

    def fetch_metadata(source: SourceDefinition, content_date: date) -> MetadataObservation:
        if source.adapter_kind != "RSS_ATOM":
            return MetadataObservation(False, False, "ADAPTER_NOT_ACTIVE", ())
        feed = {
            "name": source.name,
            "url": source.endpoints[0],
            "note_folder": "shadow-only",
            "domain": "ai-news",
            "filter_keywords": list(source.include_terms),
            "content_type": "paper" if source.evidence_role == "RESEARCH_PRIMARY" else "news",
        }
        items, meta = fetcher.fetch_rss_feed(
            feed,
            {},
            content_date.isoformat(),
            max_papers=50,
            raw_only=True,
            quality_config={
                "max_ai_items_per_feed": 50,
                "min_ai_interest_score": -10_000,
                "enable_fulltext_enrichment": False,
                "fulltext_enrichment_per_feed": 0,
                "ai_interest_topics": [],
                "ai_priority_topics": [],
                "ai_exclude_keywords": [],
            },
            return_meta=True,
        )
        reason = str(meta.get("reason_code") or "FETCH_FAILED")
        healthy = reason in {"OK", "ZERO_YIELD"}
        converted: list[MetadataItem] = []
        for index, item in enumerate(items):
            link = str(item.get("link") or "").strip()
            title = str(item.get("title") or "").strip()
            if not link or not title:
                continue
            native_id = str(item.get("guid") or link or f"item-{index}").strip()
            converted.append(
                MetadataItem(
                    item_native_id=native_id,
                    title=title,
                    canonical_url=link,
                    published_at=str(item.get("published") or "").strip(),
                    summary=str(item.get("summary") or "").strip()[:summary_limit],
                    content_type=str(item.get("content_type") or "news").strip(),
                )
            )
        return MetadataObservation(
            healthy=healthy,
            zero_yield=healthy and not converted,
            reason_code=reason,
            items=tuple(converted),
        )

    return CollectionPorts(
        fetch_metadata=fetch_metadata,
        fetch_fulltext=fetcher._fetch_article_fulltext,
    )
