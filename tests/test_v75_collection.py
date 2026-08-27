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
        title=f"{source_id} Agent evaluation release {index}",
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

    assert result.configured_sources == 14
    assert result.healthy_sources == 14
    assert result.coverage is CoverageLevel.A
    assert result.evidence_level is CoverageLevel.A
    assert result.candidates == ()
    assert "rss_hackernews_best" in seen
    assert "rss_guardian_science" not in seen
    assert "youtube_3blue1brown" not in seen
    assert "youtube_statquest" not in seen
    source_audit = result.audit["source_observations"]
    assert len(source_audit) == len(seen)
    assert all(
        set(row)
        == {
            "source_id",
            "selection_role",
            "coverage_member",
            "healthy",
            "zero_yield",
            "reason_code",
            "item_count",
        }
        for row in source_audit
    )


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
    assert max(
        sum(candidate.source_id == source_id for candidate in result.candidates)
        for source_id in {candidate.source_id for candidate in result.candidates}
    ) <= 2
    candidate_audit = result.audit["candidate_audit"]
    assert len(candidate_audit) == 8
    assert all("summary" not in row and row["canonical_url"] for row in candidate_audit)


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


def test_verified_fulltext_upgrades_evidence_availability(tmp_path: Path) -> None:
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
            fetch_fulltext=lambda _url: "Verified canonical article body. " * 30,
        ),
    )

    assert result.evidence_level is CoverageLevel.B
    assert result.evidence_healthy_sources == 8


def test_members_only_preview_never_becomes_claim_evidence() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}

    def fetch_metadata(source, _content_date):
        items = (_item(source.source_id, 1),) if source.source_id in core_ids else ()
        return MetadataObservation(True, not items, "OK" if items else "ZERO_YIELD", items)

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: (
                "Members-only preview. Subscribe now to unlock this article. " * 8
            ),
        ),
    )

    assert result.candidates == ()
    assert all(
        row["reason"] == "FULL_FREE_EVIDENCE_UNAVAILABLE"
        for row in result.audit["exclusions"]
    )


def test_exact_same_event_title_across_urls_occupies_one_candidate_slot() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}

    def fetch_metadata(source, _content_date):
        if source.source_id not in core_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        item = MetadataItem(
            item_native_id=f"{source.source_id}-event",
            title="Exact Shared Agent Evaluation Release",
            canonical_url=f"https://example.com/{source.source_id}/event",
            published_at="2026-08-27",
            summary="Agent evaluation evidence and implementation detail. " * 8,
            content_type="engineering",
        )
        return MetadataObservation(True, False, "OK", (item,))

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "Canonical public article body. " * 30,
        ),
    )

    assert len(result.candidates) == 1


def test_shared_explicit_event_phrase_clusters_different_titles_and_urls() -> None:
    catalog = load_source_catalog(MANIFEST)
    target_ids = {"rss_openai_news", "rss_metr"}

    def fetch_metadata(source, _content_date):
        if source.source_id not in target_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        title = (
            "OpenAI publishes Hugging Face incident technical report"
            if source.source_id == "rss_openai_news"
            else "Independent review of Hugging Face incident response"
        )
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id=f"{source.source_id}-hf-incident",
                    title=title,
                    canonical_url=f"https://example.com/{source.source_id}/hugging-face-incident",
                    published_at="2026-08-27",
                    summary="Hugging Face incident evidence and technical investigation. " * 8,
                    content_type="engineering",
                ),
            ),
        )

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "Canonical public incident report body. " * 30,
        ),
    )

    assert len(result.candidates) == 1


def test_membership_upgrade_preview_is_not_full_free() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}

    def fetch_metadata(source, _content_date):
        items = (_item(source.source_id, 1),) if source.source_id in core_ids else ()
        return MetadataObservation(True, not items, "OK" if items else "ZERO_YIELD", items)

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: (
                "Become a member to read the rest. Upgrade your plan for access. " * 100
            ),
        ),
    )

    assert result.candidates == ()


def test_paywall_signal_at_end_of_bounded_text_is_not_full_free() -> None:
    catalog = load_source_catalog(MANIFEST)
    core_ids = {source.source_id for source in catalog.operational_coverage_sources}

    def fetch_metadata(source, _content_date):
        items = (_item(source.source_id, 1),) if source.source_id in core_ids else ()
        return MetadataObservation(True, not items, "OK" if items else "ZERO_YIELD", items)

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: (
                ("Preview paragraph without the full article. " * 120)
                + " Become a member to read the rest and upgrade your plan for access."
            ),
        ),
    )

    assert result.candidates == ()


def test_shared_generic_launch_phrase_does_not_merge_distinct_products() -> None:
    catalog = load_source_catalog(MANIFEST)
    target_ids = {"rss_openai_news", "rss_google_ai"}

    def fetch_metadata(source, _content_date):
        if source.source_id not in target_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        title = (
            "OpenAI launches new GPT 6 model"
            if source.source_id == "rss_openai_news"
            else "OpenAI launches new Agents SDK"
        )
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id=f"{source.source_id}-distinct",
                    title=title,
                    canonical_url=f"https://example.com/{source.source_id}/distinct-product",
                    published_at="2026-08-27",
                    summary="Distinct product evidence and implementation detail. " * 8,
                    content_type="engineering",
                ),
            ),
        )

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "Canonical public product article body. " * 30,
        ),
    )

    assert len(result.candidates) == 2
