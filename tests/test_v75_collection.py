from __future__ import annotations

from datetime import date
from pathlib import Path

from pkm_workflow.source_catalog import load_source_catalog
from pkm_workflow.user_context_v75 import load_approved_user_context_v75
from pkm_workflow.v75_collection import (
    AccessState,
    CollectionPorts,
    CoverageLevel,
    MetadataItem,
    MetadataObservation,
    collect_v75_candidates,
)

MANIFEST = Path(r"D:\personal\obsidian_workflow\Source-Manifest-v7.5.json")


def _item(source_id: str, index: int, *, summary: str | None = None) -> MetadataItem:
    return MetadataItem(
        item_native_id=f"{source_id}-{index}",
        title=f"Agent evaluation release {index}",
        canonical_url=f"https://example.com/{source_id}/{index}",
        published_at="2026-08-27",
        summary=summary or ("Agent evaluation evidence and implementation detail. " * 8),
        content_type="engineering",
    )


def test_only_due_active_sources_are_fetched_and_only_core_sources_form_denominator() -> None:
    catalog = load_source_catalog(MANIFEST)
    seen: list[str] = []

    def fetch_metadata(source, _content_date):
        seen.append(source.source_id)
        return MetadataObservation(
            healthy=True,
            zero_yield=True,
            reason_code="ZERO_YIELD",
            items=(),
        )

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "",
        ),
    )

    assert result.configured_sources == 9
    assert result.healthy_sources == 9
    assert result.coverage is CoverageLevel.A
    assert result.evidence_level is CoverageLevel.A
    assert result.candidates == ()
    assert "rss_hackernews_best" in seen
    assert "rss_guardian_science" not in seen
    assert "youtube_3blue1brown" not in seen
    assert "youtube_statquest" not in seen


def test_metadata_rank_refills_failed_fulltext_up_to_eight_candidates() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}
    attempts: list[str] = []

    def fetch_metadata(source, _content_date):
        if source.source_id not in core_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        items = tuple(_item(source.source_id, index) for index in range(3))
        return MetadataObservation(True, False, "OK", items)

    def fetch_fulltext(url: str) -> str:
        attempts.append(url)
        if len(attempts) <= 4:
            return ""
        return "Canonical public article body with direct evidence. " * 30

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=fetch_fulltext,
        ),
    )

    assert len(attempts) == 12
    assert len(result.candidates) == 8
    assert all(candidate.access_state is AccessState.FULL_FREE for candidate in result.candidates)
    assert all(candidate.fulltext_enriched for candidate in result.candidates)
    assert all(candidate.metadata_score >= 5000 for candidate in result.candidates)


def test_fulltext_attempts_are_bounded_at_sixteen() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}
    attempts: list[str] = []

    def fetch_metadata(source, _content_date):
        items = (
            tuple(_item(source.source_id, index) for index in range(3))
            if source.source_id in core_ids
            else ()
        )
        return MetadataObservation(True, not items, "OK" if items else "ZERO_YIELD", items)

    def fetch_fulltext(url: str) -> str:
        attempts.append(url)
        return ""

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=fetch_fulltext,
        ),
    )

    assert len(attempts) == 16
    assert result.candidates == ()


def test_short_feed_preview_does_not_count_as_assessment_or_claim_evidence() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}

    def fetch_metadata(source, _content_date):
        if source.source_id not in core_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            healthy=True,
            zero_yield=False,
            reason_code="OK",
            items=(_item(source.source_id, 1, summary="short preview"),),
        )

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "",
        ),
    )

    assert result.coverage is CoverageLevel.A
    assert result.evidence_level is CoverageLevel.INSUFFICIENT
    assert result.candidates == ()
