"""v7.5 metadata-first source collection and bounded evidence refill."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
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
    evidence_kind: str = "CANONICAL_EXCERPT"
    source_complete: bool = False
    story_type: str = "ai_practice"
    pillar_exposure: bool = False
    editorial_score: int = 5000
    github_stars: int | None = None

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
            "pillar_exposure": self.pillar_exposure,
            "profile_refs": list(self.profile_refs),
            "published": self.published,
            "source": self.source,
            "source_complete": self.source_complete,
            "source_id": self.source_id,
            "story_type": self.story_type,
            "editorial_score": self.editorial_score,
            "github_stars": self.github_stars,
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
    pillar_exposure: bool = False


_HYPE_TERMS = ("sponsored", "webinar", "conference tickets", "coupon")
_PAYWALL_TERMS = (
    "subscribe to continue",
    "subscribe now",
    "paid subscribers",
    "subscribers only",
    "members-only",
    "members only",
    "member-only",
    "premium content",
    "unlock this article",
    "unlock the full",
    "continue reading with",
    "sign in to read",
    "already a subscriber",
)
_PILLAR_TERMS = {
    "AGENTIC_RESEARCH": (
        "agent",
        "agentic",
        "harness",
        "scaffold",
        "eval",
        "evaluation",
        "benchmark",
        "swe-bench",
        "coding agent",
        "tool use",
        "computer use",
        "mcp",
        "reward hacking",
        "long-horizon",
        "trajectory",
        "sandbox",
    ),
    "AI_MASTERY": (
        "prompt",
        "context",
        "workflow",
        "tool",
        "skill",
        "mcp",
        "eval",
        "coding",
        "api",
        "inference",
        "reasoning",
        "model",
        "retrieval",
        "embedding",
        "quantization",
        "fine-tun",
        "deployment",
        "rag",
    ),
    "BUILDERS": (
        "builder",
        "founder",
        "startup",
        "saas",
        "product",
        "build",
        "built",
        "made",
        "revenue",
        "mrr",
        "customer",
        "pricing",
        "distribution",
        "launch",
        "bootstrap",
        "solo",
        "user",
        "review",
    ),
    "VC": (
        "investment",
        "investor",
        "venture",
        "funding",
        "capital",
        "valuation",
        "market",
        "moat",
        "economics",
    ),
    "COGNITION": (
        "framework",
        "assumption",
        "incentive",
        "institution",
        "mental model",
        "cognition",
        "agency",
        "progress",
        "innovation",
    ),
}


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


def _is_paid_learning_promotion(value: str) -> bool:
    text = value.casefold()
    return any(term in text for term in ("course", "cohort", "workshop")) and any(
        term in text
        for term in ("enroll", "discount", "office hours", "community access", "maven")
    )


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
        return source.selection_role != "CORE_DAILY" or content_date.weekday() == 6
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


def _exact_title_event_key(title: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()
    tokens = normalized.split()
    return f"title:{normalized}" if len(tokens) >= 4 else None


def _explicit_event_phrases(item: MetadataItem) -> set[str]:
    title = unicodedata.normalize("NFKC", item.title).casefold()
    title_tokens = re.sub(r"[^\w]+", " ", title, flags=re.UNICODE).split()
    path_tokens = re.sub(
        r"[^\w]+", " ", urlsplit(item.canonical_url).path.casefold(), flags=re.UNICODE
    ).split()
    generic = {
        "the",
        "and",
        "agent",
        "ai",
        "evaluation",
        "for",
        "with",
        "from",
        "new",
        "model",
        "release",
        "report",
        "review",
        "technical",
        "response",
        "publishes",
        "independent",
    }
    phrase_sets: list[set[str]] = []
    for tokens in (title_tokens, path_tokens):
        phrases: set[str] = set()
        for index in range(max(0, len(tokens) - 2)):
            phrase_tokens = tokens[index : index + 3]
            if all(token not in generic for token in phrase_tokens):
                phrases.add(" ".join(phrase_tokens))
        phrase_sets.append(phrases)
    return phrase_sets[0] & phrase_sets[1]


def _security_event_identities(item: MetadataItem) -> set[str]:
    def tokens(value: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).split()

    title_tokens = tokens(item.title)
    path_tokens = tokens(urlsplit(item.canonical_url).path)
    security_terms = {
        "attack",
        "breach",
        "hack",
        "hacking",
        "incident",
        "investigation",
        "security",
    }
    if not (security_terms & set(title_tokens)) or not (security_terms & set(path_tokens)):
        return set()

    def entity_bigrams(values: list[str]) -> set[str]:
        return {
            " ".join(values[index : index + 2])
            for index in range(max(0, len(values) - 1))
            if all(token not in security_terms for token in values[index : index + 2])
        }

    shared_entities = entity_bigrams(title_tokens) & entity_bigrams(path_tokens)
    return {f"security-event:{entity}" for entity in shared_entities}


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


def _lookback_days(source: SourceDefinition) -> int:
    if source.selection_role in {"VC_WEEKLY", "COGNITIVE_WEEKLY"}:
        return 30
    if source.selection_role in {"RESEARCH_WATCH", "BUILDER_DISCOVERY"}:
        return 14
    return 7


def _metadata_score(
    source: SourceDefinition,
    item: MetadataItem,
    *,
    content_date: date,
    user_context: UserContextBundle,
    source_quality_scores: Mapping[str, object],
) -> int:
    if source.source_id == "rss_latent_space" and re.match(
        r"^\s*\[?ainews\]?", item.title, re.IGNORECASE
    ):
        return -1
    promotional_text = f"{item.title}\n{item.summary}".casefold()
    if _is_paid_learning_promotion(promotional_text):
        return -1
    weights = {pillar.pillar_id: pillar.weight_bps for pillar in user_context.pillars}
    text = promotional_text
    matched_pillars = [
        pillar
        for pillar in source.pillars
        if any(term in text for term in _PILLAR_TERMS[pillar])
    ]
    if source.pillars[0] in matched_pillars:
        primary_weight = weights[source.pillars[0]]
        pillar_relevance = min(4000, primary_weight * 4000 // 3500)
    elif matched_pillars:
        secondary_relevance = max(
            min(4000, weights[pillar] * 4000 // 3500) for pillar in matched_pillars
        )
        pillar_relevance = secondary_relevance * 3 // 4
    else:
        pillar_relevance = 0
    quality_value = source_quality_scores[source.evidence_role]
    if type(quality_value) is not int:
        raise ValueError("SOURCE_QUALITY_SCORE_INVALID")
    evidence_precheck = _evidence_precheck_score(item)
    if any(term.casefold() in text for term in source.exclude_terms):
        return -1
    hype_penalty = -1000 if any(term in text for term in _HYPE_TERMS) else 0
    relevance_penalty = -1500 if not matched_pillars else 0
    return (
        pillar_relevance
        + quality_value
        + 1500
        + evidence_precheck
        + _freshness_score(item.published_at, content_date)
        + hype_penalty
        + relevance_penalty
    )


def _evidence_precheck_score(item: MetadataItem) -> int:
    if len(item.summary.strip()) < 200:
        return 0
    return 1500 if item.content_type == "paper" else 1000


def _ranked_metadata(
    *,
    observations: Mapping[str, MetadataObservation],
    sources: Mapping[str, SourceDefinition],
    content_date: date,
    user_context: UserContextBundle,
    source_quality_scores: Mapping[str, object],
    quality_threshold: int,
    used_urls: set[str],
    balanced_modules: bool = False,
    max_age_days: int | None = None,
) -> tuple[_RankedItem, ...]:
    ranked: list[_RankedItem] = []
    for source_id, observation in observations.items():
        source = sources[source_id]
        if source.evidence_role == "DISCOVERY_PROVENANCE" or source.selection_role in {
            "MARKET_SENTINEL",
            "OUT_OF_SCOPE",
        }:
            continue
        for item in observation.items:
            url = _normalize_url(item.canonical_url)
            if not url or url in used_urls:
                continue
            published = _published_date(item.published_at)
            if (
                published is None
                or not content_date - timedelta(days=max_age_days or _lookback_days(source))
                <= published
                <= content_date
            ):
                continue
            score = _metadata_score(
                source,
                item,
                content_date=content_date,
                user_context=user_context,
                source_quality_scores=source_quality_scores,
            )
            if balanced_modules and score >= 0:
                # Modules no longer compete on the user's cross-module weight.
                # Preserve source/body quality while normalizing relevant pillar fit.
                source_text = f"{item.title}\n{item.summary}".casefold()
                primary = source.pillars[0]
                if any(term in source_text for term in _PILLAR_TERMS[primary]):
                    weight = next(p.weight_bps for p in user_context.pillars if p.pillar_id == primary)
                    score += 4000 - min(4000, weight * 4000 // 3500)
            discovery_threshold = max(0, quality_threshold - 1200)
            required_score = (
                discovery_threshold
                if _evidence_precheck_score(item) == 0
                else quality_threshold
            )
            if score < required_score:
                continue
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
    deduped_ranked: list[_RankedItem] = []
    seen_urls: set[str] = set()
    seen_event_titles: set[str] = set()
    seen_event_phrases: set[str] = set()
    seen_event_identities: set[str] = set()
    for row in ranked:
        url = _normalize_url(row.item.canonical_url)
        event_title = _exact_title_event_key(row.item.title)
        event_phrases = _explicit_event_phrases(row.item)
        event_identities = _security_event_identities(row.item)
        if (
            url in seen_urls
            or (event_title is not None and event_title in seen_event_titles)
            or bool(event_phrases & seen_event_phrases)
            or bool(event_identities & seen_event_identities)
        ):
            continue
        seen_urls.add(url)
        if event_title is not None:
            seen_event_titles.add(event_title)
        seen_event_phrases.update(event_phrases)
        seen_event_identities.update(event_identities)
        deduped_ranked.append(row)
    exposed: list[_RankedItem] = []
    selected_ids: set[tuple[str, str]] = set()
    for pillar in ("AGENTIC_RESEARCH", "AI_MASTERY", "BUILDERS", "VC", "COGNITION"):
        match = next(
            (
                row
                for row in deduped_ranked
                if row.source.pillars[0] == pillar
                and row.source.access_policy == "FREE_ENTRY_REQUIRED"
            ),
            None,
        )
        if match is not None:
            exposed.append(replace(match, pillar_exposure=True))
            selected_ids.add((match.source.source_id, match.item.item_native_id))
    exposed.extend(
        row
        for row in deduped_ranked
        if (row.source.source_id, row.item.item_native_id) not in selected_ids
    )
    return tuple(exposed)


def _is_public_fulltext(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) < 800:
        return False
    lowered = normalized.casefold()
    if any(term in lowered for term in _PAYWALL_TERMS):
        return False
    direct_gate_pattern = (
        r"\b(?:sign|log) in(?: or (?:sign|log) up)? to "
        r"(?:continue|read|access|view|unlock)\b|"
        r"\b(?:this|the) (?:story|article|post|content) is "
        r"(?:available )?(?:only )?for (?:paid )?(?:members|subscribers)\b|"
        r"\bsubscribe(?: now)? (?:for|to get) (?:full|complete|unlimited) access\b|"
        r"\bsubscribers? (?:only |get |have |receive )?(?:full )?access "
        r"to (?:the )?(?:rest|full (?:story|article|post|content))\b"
    )
    if re.search(direct_gate_pattern, lowered):
        return False
    access_pattern = (
        r"\b(?:member(?:s|ship)?|subscrib(?:e|ers?|tions?)|upgrade|premium|paywall|"
        r"sign in|log in)\b"
    )
    reading_pattern = (
        r"\b(?:read (?:the )?(?:full )?(?:rest|article|post|content)|"
        r"keep reading|continue reading|view (?:this |the )?(?:article|post|content)|"
        r"unlock(?: the)?(?: full)?(?: article| post| content)?|"
        r"access (?:the )?(?:full )?(?:article|post|content)|"
        r"full (?:article|post|content))\b"
    )
    restricted_access = re.search(
        rf"(?:{access_pattern}.{{0,160}}{reading_pattern}|"
        rf"{reading_pattern}.{{0,160}}{access_pattern})",
        lowered,
    )
    return restricted_access is None


def _infer_story_type(
    source: SourceDefinition, item: MetadataItem, evidence_text: str
) -> str:
    story_type = {
        "AGENTIC_RESEARCH": "research",
        "AI_MASTERY": "ai_practice",
        "BUILDERS": "builder",
        "VC": "vc",
        "COGNITION": "cognition",
    }[source.pillars[0]]
    if story_type not in {"research", "ai_practice"}:
        return story_type
    normalized = f"{item.title}\n{evidence_text}".casefold()
    toolchain_markers = (
        "model provider",
        "supplier",
        "api service",
        "developer tool",
        "toolchain",
        "contract",
        "service terms",
        "terminate",
        "acquisition",
        "模型供应商",
        "工具链",
        "合同",
        "服务条款",
        "终止服务",
        "收购",
    )
    model_service_change = (
        "model" in normalized
        and any(
            marker in normalized
            for marker in ("access", "provider", "service", "api", "模型")
        )
        and any(
            marker in normalized
            for marker in (
                "acquisition",
                "acquired",
                "shut off",
                "terminate",
                "contract",
                "terms of service",
                "收购",
                "终止",
                "合同",
            )
        )
    )
    if model_service_change or sum(
        marker in normalized for marker in toolchain_markers
    ) >= 3:
        return "ai_practice"
    research_markers = (
        "research paper",
        "study",
        "benchmark",
        "evaluation",
        "dataset",
        "controlled experiment",
        "ablation",
        "method",
        "model results",
        "results",
        "accuracy",
        "研究",
        "论文",
        "基准",
        "评估",
        "数据集",
        "实验",
    )
    if sum(marker in normalized for marker in research_markers) >= 3:
        return "research"
    product_markers = (
        "product",
        "platform",
        " app ",
        "saas",
        "mcp",
        "产品",
        "平台",
    )
    business_markers = (
        "founder",
        " cto ",
        "startup",
        "saas",
        "customers",
        "users",
        "revenue",
        "funding",
        "valuation",
        "distribution",
        "pricing",
        "创始人",
        "客户",
        "用户",
        "营收",
        "融资",
        "估值",
        "分发",
    )
    if any(marker in normalized for marker in product_markers) and sum(
        marker in normalized for marker in business_markers
    ) >= 3:
        return "builder"
    if (
        story_type == "research"
        and source.evidence_role != "RESEARCH_PRIMARY"
        and item.content_type.casefold() not in {"paper", "research"}
    ):
        return "ai_practice"
    return story_type


def _candidate(
    row: _RankedItem,
    evidence_text: str,
    evidence_kind: str,
    *,
    metadata_score: int | None = None,
) -> Candidate:
    source = row.source
    item = row.item
    evidence_id = "evidence-" + hashlib.sha256(
        f"{source.source_id}\n{item.item_native_id}\n{_normalize_url(item.canonical_url)}".encode()
    ).hexdigest()[:24]
    story_type = _infer_story_type(source, item, evidence_text)
    return Candidate(
        evidence_id=evidence_id,
        source=source.name,
        title=item.title.strip(),
        link=item.canonical_url.strip(),
        published=item.published_at.strip(),
        # Keep the bounded access-probe text in memory so the model packet can select
        # results, limitations, and conclusions that appear after a long introduction.
        # The downstream packet builder still enforces the much smaller model budget.
        summary=evidence_text.strip()[:100_000],
        content_type=item.content_type.strip(),
        fulltext_enriched=evidence_kind == "CANONICAL_EXCERPT",
        source_id=source.source_id,
        canonical_origin_id=source.canonical_origin_id,
        evidence_role=source.evidence_role,
        pillars=source.pillars,
        metadata_score=row.metadata_score if metadata_score is None else metadata_score,
        access_state=AccessState.FULL_FREE,
        evidence_kind=evidence_kind,
        story_type=story_type,
        pillar_exposure=row.pillar_exposure,
        editorial_score=(
            row.metadata_score if metadata_score is None else metadata_score
        )
        + (2500 if row.pillar_exposure else 0),
    )


def _reapply_pillar_exposure(candidates: list[Candidate]) -> list[Candidate]:
    pillar_order = ("AGENTIC_RESEARCH", "AI_MASTERY", "BUILDERS", "VC", "COGNITION")
    exposed_ids: set[str] = set()
    ordered: list[Candidate] = []
    for pillar in pillar_order:
        match = next(
            (
                candidate
                for candidate in candidates
                if candidate.pillars[0] == pillar
                and candidate.evidence_id not in exposed_ids
            ),
            None,
        )
        if match is not None:
            exposed_ids.add(match.evidence_id)
            ordered.append(
                replace(
                    match,
                    pillar_exposure=True,
                    editorial_score=match.metadata_score + 2500,
                )
            )
    ordered.extend(
        replace(
            candidate,
            pillar_exposure=False,
            editorial_score=candidate.metadata_score,
        )
        for candidate in candidates
        if candidate.evidence_id not in exposed_ids
    )
    return ordered


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
    coverage_source_ids = {source.source_id for source in coverage_sources}
    healthy_sources = sum(
        observations[source.source_id].healthy for source in coverage_sources
    )
    assessment_source_ids = {
        source.source_id
        for source in coverage_sources
        if observations[source.source_id].healthy
        and (
            observations[source.source_id].zero_yield
            or (
                source.access_policy != "MIXED_FREEMIUM_DISCOVERY_ONLY"
                and any(
                    len(item.summary.strip()) >= 200
                    for item in observations[source.source_id].items
                )
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
    lane_candidates: dict[str, int] = {}
    source_attempts: dict[str, int] = {}
    origin_candidates: dict[str, int] = {}
    exclusions: list[dict[str, str]] = []
    for row in ranked:
        if len(candidates) >= max_candidates or attempts >= max_attempts:
            break
        source_id = row.source.source_id
        origin_id = row.source.canonical_origin_id
        if origin_candidates.get(origin_id, 0) >= 1 or source_attempts.get(source_id, 0) >= 2:
            continue
        if row.source.access_policy == "MIXED_FREEMIUM_DISCOVERY_ONLY":
            exclusions.append(
                {
                    "source_id": source_id,
                    "item_native_id": row.item.item_native_id,
                    "reason": "SOURCE_SPECIFIC_PUBLIC_PROOF_REQUIRED",
                }
            )
            source_attempts[source_id] = 2
            continue
        if row.item.content_type == "paper" and len(row.item.summary.strip()) >= 200:
            candidate = _candidate(row, row.item.summary, "PUBLIC_ABSTRACT")
            if source_id in coverage_source_ids:
                assessment_source_ids.add(source_id)
            if lane_candidates.get(candidate.story_type, 0) >= 2:
                continue
            candidates.append(candidate)
            lane_candidates[candidate.story_type] = (
                lane_candidates.get(candidate.story_type, 0) + 1
            )
            origin_candidates[origin_id] = origin_candidates.get(origin_id, 0) + 1
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
        final_metadata_score = (
            row.metadata_score + 1500 - _evidence_precheck_score(row.item)
        )
        if final_metadata_score < _policy_int(policies, "metadata_quality_threshold"):
            exclusions.append(
                {
                    "source_id": row.source.source_id,
                    "item_native_id": row.item.item_native_id,
                    "reason": "FULLTEXT_QUALITY_BELOW_THRESHOLD",
                }
            )
            continue
        if _is_paid_learning_promotion(
            f"{row.item.title}\n{row.item.summary}\n{fulltext}"
        ):
            exclusions.append(
                {
                    "source_id": row.source.source_id,
                    "item_native_id": row.item.item_native_id,
                    "reason": "PROMOTIONAL_CONTENT_NOT_DAILY",
                }
            )
            continue
        if source_id in coverage_source_ids:
            assessment_source_ids.add(source_id)
        candidate = _candidate(
            row,
            fulltext,
            "CANONICAL_EXCERPT",
            metadata_score=final_metadata_score,
        )
        if lane_candidates.get(candidate.story_type, 0) >= 2:
            continue
        candidates.append(candidate)
        lane_candidates[candidate.story_type] = (
            lane_candidates.get(candidate.story_type, 0) + 1
        )
        origin_candidates[origin_id] = origin_candidates.get(origin_id, 0) + 1
    candidates = _reapply_pillar_exposure(candidates)
    assessment_source_ids.update(
        candidate.source_id
        for candidate in candidates
        if candidate.source_id in coverage_source_ids
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
            "diversity_metadata_gap": _policy_int(
                policies, "diversity_metadata_gap"
            ),
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
                    "title": candidate.title,
                    "canonical_url": candidate.link,
                    "pillars": list(candidate.pillars),
                    "metadata_score": candidate.metadata_score,
                    "editorial_score": candidate.editorial_score,
                    "pillar_exposure": candidate.pillar_exposure,
                    "access_state": candidate.access_state.value,
                    "evidence_kind": candidate.evidence_kind,
                    "story_type": candidate.story_type,
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
    fulltext_limit = _policy_int(limits, "fulltext_chars")
    access_probe_limit = _policy_int(limits, "access_probe_chars")
    if access_probe_limit < fulltext_limit:
        raise ValueError("ACCESS_PROBE_LIMIT_TOO_SMALL")

    def fetch_metadata(source: SourceDefinition, content_date: date) -> MetadataObservation:
        if source.adapter_kind != "RSS_ATOM":
            return MetadataObservation(False, False, "ADAPTER_NOT_ACTIVE", ())
        feed = {
            "name": source.name,
            "url": source.endpoints[0],
            "note_folder": "shadow-only",
            "domain": "ai-news",
            "filter_keywords": list(source.include_terms),
            "content_type": "paper" if source.adapter_kind == "ARXIV_QUERY" else "news",
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
        fetch_fulltext=lambda url: fetcher._fetch_article_fulltext(
            url, max_chars=access_probe_limit
        ),
    )
