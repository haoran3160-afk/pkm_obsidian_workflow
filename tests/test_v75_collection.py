from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pkm_workflow.source_catalog import load_source_catalog
from pkm_workflow.user_context_v75 import load_approved_user_context_v75
from pkm_workflow.v75_collection import (
    AccessState,
    CollectionPorts,
    CoverageLevel,
    MetadataItem,
    MetadataObservation,
    _is_public_fulltext,
    collect_v75_candidates,
    default_collection_ports,
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
    assert "rss_air_street_press" in seen
    assert "rss_stratechery" in seen
    assert "rss_not_boring" in seen
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


def test_rolling_candidate_observation_can_expose_all_five_pillars() -> None:
    catalog = load_source_catalog(MANIFEST)
    target_text = {
        "rss_simon_willison": "agent harness evaluation benchmark trajectory",
        "rss_ethan_mollick": "prompt context workflow API coding tool",
        "rss_pieter_levels": "founder startup SaaS product revenue distribution",
        "rss_air_street_press": "venture investment capital market moat economics",
        "rss_works_in_progress": "mental model assumption incentive framework innovation",
    }

    def fetch_metadata(source, _content_date):
        text = target_text.get(source.source_id)
        if text is None:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id=f"{source.source_id}-pillar",
                    title=text.title(),
                    canonical_url=f"https://example.com/{source.source_id}/pillar",
                    published_at="2026-08-27",
                    summary=(text + " with direct evidence and implementation detail. ") * 8,
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
            fetch_fulltext=lambda _url: "Canonical public article body. " * 40,
        ),
    )

    assert result.configured_sources == 14
    assert {candidate.pillars[0] for candidate in result.candidates} >= {
        "AGENTIC_RESEARCH",
        "AI_MASTERY",
        "BUILDERS",
        "VC",
        "COGNITION",
    }
    exposed = [candidate for candidate in result.candidates if candidate.pillar_exposure]
    assert {candidate.pillars[0] for candidate in exposed} >= {
        "AGENTIC_RESEARCH",
        "AI_MASTERY",
        "BUILDERS",
        "VC",
        "COGNITION",
    }
    assert all(
        candidate.editorial_score == candidate.metadata_score + 2500
        for candidate in exposed
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


def test_short_public_cognitive_metadata_can_qualify_after_verified_fulltext() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_works_in_progress":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            healthy=True,
            zero_yield=False,
            reason_code="OK",
            items=(
                MetadataItem(
                    item_native_id="works-in-progress-autonomy",
                    title="Autonomy and Innovation",
                    canonical_url="https://example.com/autonomy-and-innovation",
                    published_at="2026-08-24",
                    summary=(
                        "A framework challenging assumptions about institutional incentives "
                        "and innovation."
                    ),
                    content_type="essay",
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
            fetch_fulltext=lambda _url: "Verified public essay body and evidence. " * 40,
        ),
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.story_type == "cognition"
    assert candidate.metadata_score >= 5000


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
        items = (
            (_item(source.source_id, 1),)
            if source.source_id in core_ids
            and source.access_policy == "FREE_ENTRY_REQUIRED"
            else ()
        )
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


@pytest.mark.parametrize(
    "gate",
    [
        "Sign in to continue.",
        "This story is for members.",
        "Subscribe for full access.",
        "Subscribers get access to the rest.",
    ],
)
def test_common_access_gates_never_qualify_as_public_fulltext(gate: str) -> None:
    preview = ("Long editorial preview with useful technical context. " * 24) + gate

    assert _is_public_fulltext(preview) is False


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
            "The Hugging Face incident and the road ahead"
            if source.source_id == "rss_openai_news"
            else "Brief independent investigation of agents in the OpenAI / Hugging Face hacking event"
        )
        slug = (
            "hugging-face-incident-and-the-road-ahead"
            if source.source_id == "rss_openai_news"
            else "openai-hugging-face-incident-investigation"
        )
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id=f"{source.source_id}-hf-incident",
                    title=title,
                    canonical_url=f"https://example.com/{slug}",
                    published_at="2026-08-27",
                    summary="Agent behavior in the Hugging Face incident investigation. " * 8,
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


@pytest.mark.parametrize(
    "gate_text",
    [
        "Become a member to keep reading.",
        "Log in to view this article.",
        "This post is for subscribers only.",
    ],
)
def test_common_paywall_gates_never_become_claim_evidence(gate_text: str) -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_pieter_levels":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="gated-builder-preview",
                    title="I built an Agent product for users",
                    canonical_url="https://example.com/gated-builder-preview",
                    published_at="2026-08-28",
                    summary="Builder product users validation and distribution evidence. " * 6,
                    content_type="builder",
                ),
            ),
        )

    body = ("Public preview paragraph about the product and users. " * 60) + gate_text
    result = collect_v75_candidates(
        date(2026, 8, 28),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: body,
        ),
    )

    assert result.candidates == ()


def test_public_article_with_distant_newsletter_footer_remains_full_free() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_pieter_levels":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="public-builder-article",
                    title="I built a product to fix hotel review incentives",
                    canonical_url="https://example.com/public-builder-article",
                    published_at="2026-08-27",
                    summary="Builder product users reviews distribution evidence. " * 6,
                    content_type="engineering",
                ),
            ),
        )

    public_body = (
        "Read this full public case study about the product, users, validation, and distribution. "
        * 100
    ) + ("General analysis. " * 100) + " Subscribe to the free newsletter."
    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: public_body,
        ),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].story_type == "builder"


def test_default_access_probe_scans_beyond_model_excerpt_limit(
    monkeypatch,
) -> None:
    catalog = load_source_catalog(MANIFEST)
    observed: list[int] = []

    def fake_fulltext(_url: str, max_chars: int) -> str:
        observed.append(max_chars)
        return ("Public preview body. " * 700) + " This post is for paid subscribers."

    monkeypatch.setattr("fetcher._fetch_article_fulltext", fake_fulltext)
    ports = default_collection_ports(catalog)

    body = ports.fetch_fulltext("https://example.com/paid-preview")

    assert observed == [100000]
    assert len(body) > 12000


def test_research_blog_rss_is_not_mislabeled_as_a_paper_abstract(monkeypatch) -> None:
    catalog = load_source_catalog(MANIFEST)
    metr = catalog.source("rss_metr")
    observed: list[str] = []

    def fake_feed(feed_config, *_args, **_kwargs):
        observed.append(feed_config["content_type"])
        return [], {"reason_code": "ZERO_YIELD"}

    monkeypatch.setattr("fetcher.fetch_rss_feed", fake_feed)
    ports = default_collection_ports(catalog)

    ports.fetch_metadata(metr, date(2026, 8, 27))

    assert observed == ["news"]


def test_mixed_freemium_source_stays_discovery_only_without_public_proof_adapter() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id == "rss_pragmatic_engineer":
            return MetadataObservation(
                True,
                False,
                "OK",
                (_item(source.source_id, 1),),
            )
        if source.coverage_member and source.selection_role == "CORE_DAILY":
            return MetadataObservation(False, False, "FETCH_FAILED", ())
        return MetadataObservation(True, True, "ZERO_YIELD", ())

    result = collect_v75_candidates(
        date(2026, 8, 27),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "Apparently complete public article body. " * 100,
        ),
    )

    assert result.healthy_sources == 1
    assert result.evidence_healthy_sources == 0
    assert result.evidence_level is CoverageLevel.INSUFFICIENT
    assert result.candidates == ()
    assert result.audit["exclusions"] == [
        {
            "source_id": "rss_pragmatic_engineer",
            "item_native_id": "rss_pragmatic_engineer-1",
            "reason": "SOURCE_SPECIFIC_PUBLIC_PROOF_REQUIRED",
        }
    ]


def test_public_same_pillar_candidate_inherits_exposure_after_discovery_only_drop() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id == "rss_stratechery":
            return MetadataObservation(
                True,
                False,
                "OK",
                (
                    MetadataItem(
                        item_native_id="paid-cognition",
                        title="Autonomy innovation institutional framework",
                        canonical_url="https://example.com/paid-cognition",
                        published_at="2026-08-28",
                        summary="Mental model incentives autonomy innovation framework. " * 8,
                        content_type="essay",
                    ),
                ),
            )
        if source.source_id == "rss_works_in_progress":
            return MetadataObservation(
                True,
                False,
                "OK",
                (
                    MetadataItem(
                        item_native_id="public-cognition",
                        title="A public framework for institutional innovation",
                        canonical_url="https://example.com/public-cognition",
                        published_at="2026-08-27",
                        summary="Assumption incentive institution mental model innovation. " * 8,
                        content_type="essay",
                    ),
                ),
            )
        return MetadataObservation(True, True, "ZERO_YIELD", ())

    result = collect_v75_candidates(
        date(2026, 8, 29),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: "Verified public cognition essay body. " * 40,
        ),
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source_id == "rss_works_in_progress"
    assert candidate.pillar_exposure is True
    assert candidate.editorial_score == candidate.metadata_score + 2500


def test_product_strategy_interview_is_classified_as_builder_story() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_latent_space":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="lovable-capabilities",
                    title="Lovable CTO: SaaS apps become capabilities for agents",
                    canonical_url="https://example.com/lovable-capabilities",
                    published_at="2026-08-28",
                    summary=(
                        "The CTO explains a SaaS product, MCP platform, customers, funding, "
                        "distribution, and product strategy for agent users. " * 6
                    ),
                    content_type="interview",
                ),
            ),
        )

    result = collect_v75_candidates(
        date(2026, 8, 29),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: (
                "The founder and CTO launched a SaaS product and MCP platform for customers. "
                "The company reports users, revenue, funding, distribution, and product adoption. "
            )
            * 30,
        ),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].story_type == "builder"


def test_model_supplier_contract_event_is_ai_practice_story() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_openai_news":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="cursor-supplier-contract",
                    title="Model supplier terminates Cursor contract after acquisition",
                    canonical_url="https://example.com/cursor-contract",
                    published_at="2026-08-29",
                    summary=(
                        "A model provider will terminate API service to a developer tool "
                        "after an acquisition, citing contract terms and supplier risk. " * 6
                    ),
                    content_type="news",
                ),
            ),
        )

    result = collect_v75_candidates(
        date(2026, 8, 29),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: (
                "The model provider terminated an API supplier contract for a developer "
                "tool after its acquisition. The service terms create toolchain risk. "
            )
            * 30,
        ),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].story_type == "ai_practice"


def test_research_evaluation_with_commercial_terms_stays_research() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_huggingface_blog":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="commercial-benchmark",
                    title="Benchmarking product platforms for enterprise users",
                    canonical_url="https://example.com/commercial-benchmark",
                    published_at="2026-08-29",
                    summary=(
                        "A research paper evaluates product platforms, customer pricing, "
                        "and user outcomes with a benchmark dataset and controlled experiment. "
                        * 6
                    ),
                    content_type="research",
                ),
            ),
        )

    result = collect_v75_candidates(
        date(2026, 8, 29),
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=set(),
        ports=CollectionPorts(
            fetch_metadata=fetch_metadata,
            fetch_fulltext=lambda _url: (
                "This study reports a benchmark evaluation, dataset, controlled experiment, "
                "method, ablation, and model results for product platform users and pricing. "
            )
            * 30,
        ),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].story_type == "research"


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


def test_natural_title_phrase_without_matching_url_slug_does_not_merge() -> None:
    catalog = load_source_catalog(MANIFEST)
    target_ids = {"rss_openai_news", "rss_google_ai"}

    def fetch_metadata(source, _content_date):
        if source.source_id not in target_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        suffix = "research-update" if source.source_id == "rss_openai_news" else "product-news"
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id=f"{source.source_id}-natural-phrase",
                    title=f"Practical systems improve teams {source.source_id}",
                    canonical_url=f"https://example.com/{suffix}",
                    published_at="2026-08-27",
                    summary="Distinct Agent workflow evidence and implementation detail. " * 8,
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
            fetch_fulltext=lambda _url: "Canonical public article body. " * 30,
        ),
    )

    assert len(result.candidates) == 2


def test_latent_space_ainews_roundup_is_discovery_only_without_direct_children() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_latent_space":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="latent-roundup",
                    title="[AINews] Hot Chips: Jalapeño, AutoSaddler, and more",
                    canonical_url="https://www.latent.space/p/ainews-hot-chips-roundup",
                    published_at="2026-08-27",
                    summary="A roundup containing several unrelated primary events. " * 8,
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
            fetch_fulltext=lambda _url: "Roundup body with multiple unrelated items. " * 30,
        ),
    )

    assert result.candidates == ()


def test_generic_official_expansion_gets_no_agentic_source_bonus() -> None:
    catalog = load_source_catalog(MANIFEST)
    target_ids = {"rss_openai_news", "rss_hamel_husain"}

    def fetch_metadata(source, _content_date):
        if source.source_id not in target_ids:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        if source.source_id == "rss_openai_news":
            title = "Company expands regional presence"
            summary = "Regional office, community partnerships, and local operations. " * 6
        else:
            title = "AI Evals for Engineers"
            summary = "Agent evaluation, error analysis, and coding workflow evidence. " * 6
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id=f"{source.source_id}-item",
                    title=title,
                    canonical_url=f"https://example.com/{source.source_id}/item",
                    published_at="2026-08-27",
                    summary=summary,
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
            fetch_fulltext=lambda _url: "Canonical public article body. " * 30,
        ),
    )

    assert [candidate.source_id for candidate in result.candidates] == ["rss_hamel_husain"]


def test_paid_course_cohort_promotion_is_not_a_daily_candidate() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_hamel_husain":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="hamel-paid-cohort",
                    title="AI Evals Course for Engineers and Product Managers",
                    canonical_url="https://example.com/paid-evals-course",
                    published_at="2026-08-27",
                    summary=(
                        "Enroll in the live cohort with office hours, community access, "
                        "a Maven course, and an early discount. " * 6
                    ),
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
            fetch_fulltext=lambda _url: "Paid course cohort enrollment page. " * 40,
        ),
    )

    assert result.candidates == ()


def test_paid_cohort_promotion_hidden_from_metadata_is_rejected_after_access_probe() -> None:
    catalog = load_source_catalog(MANIFEST)

    def fetch_metadata(source, _content_date):
        if source.source_id != "rss_hamel_husain":
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        return MetadataObservation(
            True,
            False,
            "OK",
            (
                MetadataItem(
                    item_native_id="hamel-neutral-metadata",
                    title="AI Evals for Engineers",
                    canonical_url="https://example.com/evals",
                    published_at="2026-08-27",
                    summary="Agent evaluation, error analysis, and CI integration methods. " * 6,
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
            fetch_fulltext=lambda _url: (
                "Join the live cohort course on Maven. Enroll now for office hours. " * 30
            ),
        ),
    )

    assert result.candidates == ()
    assert result.audit["exclusions"][0]["reason"] == "PROMOTIONAL_CONTENT_NOT_DAILY"
