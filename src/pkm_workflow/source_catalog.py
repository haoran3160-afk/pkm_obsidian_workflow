"""Typed reader for the user-approved v7.5 information-source catalog."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

_REQUIRED_SOURCE_FIELDS = {
    "source_id",
    "name",
    "canonical_origin_id",
    "adapter_kind",
    "endpoints",
    "operational_status",
    "selection_role",
    "coverage_member",
    "cadence",
    "pillars",
    "evidence_role",
    "access_policy",
    "include_terms",
    "exclude_terms",
    "duplicate_group",
    "decision",
    "activation_gate",
}
_PILLARS = {"AGENTIC_RESEARCH", "AI_MASTERY", "BUILDERS", "VC", "COGNITION"}
_EMPTY_PILLAR_ROLES = {"MARKET_SENTINEL", "OUT_OF_SCOPE"}
_OPERATIONAL_STATUSES = {"ACTIVE", "CATALOGED", "DISABLED"}
_SELECTION_ROLES = {
    "CORE_DAILY",
    "RESEARCH_WATCH",
    "VC_WEEKLY",
    "COGNITIVE_WEEKLY",
    "BUILDER_DISCOVERY",
    "MARKET_SENTINEL",
    "OUT_OF_SCOPE",
}
_ADAPTER_KINDS = {
    "AIHOT_PUBLIC_INTERFACE",
    "ARXIV_QUERY",
    "GITHUB_PUBLIC_SIGNAL",
    "RSS_ATOM",
    "WEB_SOURCE_SPECIFIC",
    "YOUTUBE_CHANNEL",
    "ZARA_BLOG_MEMBER",
    "ZARA_CENTRAL_JSON",
    "ZARA_PODCAST_MEMBER",
    "ZARA_X_MEMBER",
}
_CADENCES = {"ANNUAL_WITH_UPDATE_CHECK", "DAILY", "NONE", "WEEKDAY", "WEEKLY"}
_ACCESS_POLICIES = {"FREE_ENTRY_REQUIRED", "MIXED_FREEMIUM_DISCOVERY_ONLY"}
APPROVED_SOURCE_MANIFEST_PATH = Path(
    r"D:\personal\obsidian_workflow\Source-Manifest-v7.5.json"
)


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    name: str
    canonical_origin_id: str
    adapter_kind: str
    endpoints: tuple[str, ...]
    operational_status: str
    selection_role: str
    coverage_member: bool
    cadence: str
    pillars: tuple[str, ...]
    evidence_role: str
    access_policy: str
    include_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    duplicate_group: str
    decision: str
    activation_gate: str


@dataclass(frozen=True)
class SourceCatalog:
    version: str
    sources: tuple[SourceDefinition, ...]
    policies: Mapping[str, object]
    _by_id: Mapping[str, SourceDefinition]

    def source(self, source_id: str) -> SourceDefinition:
        try:
            return self._by_id[source_id]
        except KeyError as error:
            raise KeyError(f"unknown source_id: {source_id}") from error

    @property
    def operational_coverage_sources(self) -> tuple[SourceDefinition, ...]:
        return tuple(
            source
            for source in self.sources
            if source.operational_status == "ACTIVE"
            and source.selection_role == "CORE_DAILY"
            and source.coverage_member
        )


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}_MUST_BE_OBJECT")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"INVALID_{field.upper()}")
    return value.strip()


def _text_tuple(value: object, *, field: str, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"INVALID_{field.upper()}")
    result = tuple(_text(item, field=field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"DUPLICATE_{field.upper()}")
    return result


def _policy_integer(policies: Mapping[str, object], name: str) -> int:
    value = policies.get(name)
    if type(value) is not int:
        raise ValueError("INVALID_COVERAGE_THRESHOLDS")
    return value


def _source(effective: Mapping[str, object], quality_scores: Mapping[str, object]) -> SourceDefinition:
    missing = sorted(_REQUIRED_SOURCE_FIELDS - effective.keys())
    if missing:
        raise ValueError("MISSING_SOURCE_FIELDS:" + ",".join(missing))
    coverage_member = effective["coverage_member"]
    if type(coverage_member) is not bool:
        raise ValueError("INVALID_COVERAGE_MEMBER")
    evidence_role = _text(effective["evidence_role"], field="evidence_role")
    if evidence_role not in quality_scores:
        raise ValueError("UNKNOWN_EVIDENCE_ROLE")
    selection_role = _text(effective["selection_role"], field="selection_role")
    operational_status = _text(
        effective["operational_status"], field="operational_status"
    )
    if operational_status not in _OPERATIONAL_STATUSES:
        raise ValueError("UNKNOWN_OPERATIONAL_STATUS")
    if selection_role not in _SELECTION_ROLES:
        raise ValueError("UNKNOWN_SELECTION_ROLE")
    adapter_kind = _text(effective["adapter_kind"], field="adapter_kind")
    if adapter_kind not in _ADAPTER_KINDS:
        raise ValueError("UNKNOWN_ADAPTER_KIND")
    cadence = _text(effective["cadence"], field="cadence")
    if cadence not in _CADENCES:
        raise ValueError("UNKNOWN_CADENCE")
    if effective["access_policy"] not in _ACCESS_POLICIES:
        raise ValueError("UNKNOWN_ACCESS_POLICY")
    if evidence_role == "DISCOVERY_PROVENANCE" and coverage_member:
        raise ValueError("DISCOVERY_PROVENANCE_COVERAGE_FORBIDDEN")
    pillars = _text_tuple(effective["pillars"], field="pillars")
    if any(pillar not in _PILLARS for pillar in pillars):
        raise ValueError("UNKNOWN_PILLAR")
    if not pillars and selection_role not in _EMPTY_PILLAR_ROLES:
        raise ValueError("EMPTY_PILLARS_FORBIDDEN")
    endpoints = _text_tuple(effective["endpoints"], field="endpoints", allow_empty=False)
    if any(not endpoint.startswith("https://") for endpoint in endpoints):
        raise ValueError("HTTPS_ENDPOINT_REQUIRED")
    return SourceDefinition(
        source_id=_text(effective["source_id"], field="source_id"),
        name=_text(effective["name"], field="name"),
        canonical_origin_id=_text(
            effective["canonical_origin_id"], field="canonical_origin_id"
        ),
        adapter_kind=adapter_kind,
        endpoints=endpoints,
        operational_status=operational_status,
        selection_role=selection_role,
        coverage_member=coverage_member,
        cadence=cadence,
        pillars=pillars,
        evidence_role=evidence_role,
        access_policy=_text(effective["access_policy"], field="access_policy"),
        include_terms=_text_tuple(effective["include_terms"], field="include_terms"),
        exclude_terms=_text_tuple(effective["exclude_terms"], field="exclude_terms"),
        duplicate_group=_text(effective["duplicate_group"], field="duplicate_group"),
        decision=_text(effective["decision"], field="decision"),
        activation_gate=_text(effective["activation_gate"], field="activation_gate"),
    )


def load_source_catalog(path: Path) -> SourceCatalog:
    """Load and validate one complete, inheritance-expanded source catalog."""

    payload = _object(json.loads(path.read_text(encoding="utf-8")), label="MANIFEST")
    version = _text(payload.get("manifest_version"), field="manifest_version")
    if payload.get("status") != "APPROVED_FOR_IMPLEMENTATION":
        raise ValueError("SOURCE_MANIFEST_NOT_APPROVED")
    defaults = _object(payload.get("global_defaults"), label="GLOBAL_DEFAULTS")
    expected = _object(payload.get("expected_counts"), label="EXPECTED_COUNTS")
    policies = _object(payload.get("policies"), label="POLICIES")
    quality_scores = _object(
        policies.get("source_quality_scores"), label="SOURCE_QUALITY_SCORES"
    )
    operational_a = _policy_integer(policies, "operational_coverage_a_bps")
    operational_b = _policy_integer(policies, "operational_coverage_b_bps")
    evidence_a = _policy_integer(policies, "evidence_availability_a_bps")
    evidence_b = _policy_integer(policies, "evidence_availability_b_bps")
    if (
        not 0 < operational_b < operational_a <= 10_000
        or not 0 < evidence_b < evidence_a <= 10_000
    ):
        raise ValueError("INVALID_COVERAGE_THRESHOLDS")
    if (
        policies.get("max_admissible_candidates") != 10
        or policies.get("max_fulltext_attempts") != 16
        or policies.get("daily_final_top_max") != 3
        or policies.get("daily_action_max") != 2
        or policies.get("per_story_action_max") != 1
        or policies.get("metadata_quality_threshold") != 5000
    ):
        raise ValueError("INVALID_CURATION_LIMITS")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("GROUPS_MUST_BE_NONEMPTY_LIST")
    sources: list[SourceDefinition] = []
    seen: set[str] = set()
    for raw_group in groups:
        group = _object(raw_group, label="GROUP")
        group_defaults = _object(group.get("defaults"), label="GROUP_DEFAULTS")
        raw_sources = group.get("sources")
        if not isinstance(raw_sources, list):
            raise ValueError("GROUP_SOURCES_MUST_BE_LIST")
        for raw_source in raw_sources:
            source_overrides = _object(raw_source, label="SOURCE")
            effective = defaults | group_defaults | source_overrides
            source = _source(effective, quality_scores)
            if source.source_id in seen:
                raise ValueError("DUPLICATE_SOURCE_ID")
            seen.add(source.source_id)
            sources.append(source)
    expected_total = expected.get("total_catalog_rows")
    if type(expected_total) is not int or expected_total != len(sources):
        raise ValueError("CATALOG_ROW_COUNT_MISMATCH")
    by_id = MappingProxyType({source.source_id: source for source in sources})
    return SourceCatalog(
        version=version,
        sources=tuple(sources),
        policies=MappingProxyType(dict(policies)),
        _by_id=by_id,
    )


def load_approved_source_catalog() -> SourceCatalog:
    return load_source_catalog(APPROVED_SOURCE_MANIFEST_PATH)
