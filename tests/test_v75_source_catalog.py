from __future__ import annotations

import json
from pathlib import Path

import pytest

from pkm_workflow.source_catalog import load_source_catalog

REAL_MANIFEST = Path(
    r"D:\personal\obsidian_workflow\Source-Manifest-v7.5.json"
)


def test_approved_manifest_exposes_complete_unique_catalog_and_true_coverage_set() -> None:
    catalog = load_source_catalog(REAL_MANIFEST)

    assert catalog.version == "7.5.0"
    assert len(catalog.sources) == 100
    assert len({source.source_id for source in catalog.sources}) == 100
    assert {source.source_id for source in catalog.operational_coverage_sources} == {
        "rss_latent_space",
        "rss_simon_willison",
        "rss_openai_news",
        "rss_ethan_mollick",
        "rss_mlops_community",
        "rss_lilian_weng",
        "rss_google_ai",
        "rss_huggingface_blog",
        "rss_langchain_blog",
        "rss_hamel_husain",
        "rss_eugene_yan",
        "rss_chip_huyen",
        "rss_shreya_shankar",
        "rss_pragmatic_engineer",
    }
    hacker_news = catalog.source("rss_hackernews_best")
    assert hacker_news.operational_status == "ACTIVE"
    assert hacker_news.selection_role == "RESEARCH_WATCH"
    assert hacker_news.coverage_member is False
    assert hacker_news.evidence_role == "DISCOVERY_PROVENANCE"


def test_manifest_inheritance_produces_effective_required_fields(tmp_path: Path) -> None:
    manifest = {
        "manifest_version": "7.5.0",
        "status": "APPROVED_FOR_IMPLEMENTATION",
        "global_defaults": {
            "access_policy": "FREE_ENTRY_REQUIRED",
            "include_terms": [],
            "exclude_terms": [],
            "activation_gate": "SMOKE_REQUIRED",
        },
        "expected_counts": {"total_catalog_rows": 1},
        "groups": [
            {
                "group_id": "core",
                "defaults": {
                    "adapter_kind": "RSS_ATOM",
                    "operational_status": "ACTIVE",
                    "selection_role": "CORE_DAILY",
                    "coverage_member": True,
                    "cadence": "DAILY",
                    "pillars": ["AGENTIC_RESEARCH"],
                    "evidence_role": "PRIMARY_OFFICIAL",
                    "decision": "ADD",
                },
                "sources": [
                    {
                        "source_id": "rss_example",
                        "name": "Example",
                        "canonical_origin_id": "example",
                        "endpoints": ["https://example.com/feed.xml"],
                        "duplicate_group": "example",
                    }
                ],
            }
        ],
        "policies": {
            "source_quality_scores": {"PRIMARY_OFFICIAL": 2000},
            "max_admissible_candidates": 8,
            "max_fulltext_attempts": 16,
            "daily_final_top_max": 3,
            "daily_action_max": 2,
            "per_story_action_max": 1,
            "metadata_quality_threshold": 5000,
            "operational_coverage_a_bps": 8500,
            "operational_coverage_b_bps": 7000,
            "evidence_availability_a_bps": 7000,
            "evidence_availability_b_bps": 5000,
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    catalog = load_source_catalog(path)

    source = catalog.source("rss_example")
    assert source.access_policy == "FREE_ENTRY_REQUIRED"
    assert source.adapter_kind == "RSS_ATOM"
    assert source.pillars == ("AGENTIC_RESEARCH",)
    assert catalog.operational_coverage_sources == (source,)


def test_discovery_provenance_cannot_enter_operational_coverage(tmp_path: Path) -> None:
    payload = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    hacker_news = payload["groups"][0]["sources"][1]
    hacker_news["coverage_member"] = True
    path = tmp_path / "bad-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="DISCOVERY_PROVENANCE_COVERAGE_FORBIDDEN"):
        load_source_catalog(path)


def test_unknown_evidence_role_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    payload["groups"][0]["sources"][0]["evidence_role"] = "MODEL_GUESSED_ROLE"
    path = tmp_path / "bad-role.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="UNKNOWN_EVIDENCE_ROLE"):
        load_source_catalog(path)


def test_unknown_operational_status_cannot_silently_leave_coverage(tmp_path: Path) -> None:
    payload = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    payload["groups"][0]["sources"][0]["operational_status"] = "ACTVE"
    path = tmp_path / "bad-status.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="UNKNOWN_OPERATIONAL_STATUS"):
        load_source_catalog(path)


def test_coverage_thresholds_must_be_ordered_integer_basis_points(tmp_path: Path) -> None:
    payload = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    payload["policies"]["operational_coverage_a_bps"] = 6500
    path = tmp_path / "bad-policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="INVALID_COVERAGE_THRESHOLDS"):
        load_source_catalog(path)
