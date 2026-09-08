from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

from pkm_workflow.daily_brief import SECTIONS
from pkm_workflow.module_collection import GITHUB_REPOS, collect_modules, github_candidates
from pkm_workflow.source_catalog import load_source_catalog
from pkm_workflow.user_context_v75 import load_approved_user_context_v75
from pkm_workflow.v75_collection import (
    Candidate,
    CollectionPorts,
    MetadataItem,
    MetadataObservation,
)

DAY = date(2026, 9, 8)
CATALOG = Path(r"D:\personal\obsidian_workflow\Source-Manifest-v7.5.json")


def github_response(endpoint):
    repo = endpoint.removeprefix("repos/").removesuffix("/readme")
    if endpoint.endswith("/readme"):
        return {"content": base64.b64encode(
            (f"# {repo}\nAgent harness evaluation with configurable tools and reproducible tests.\n" * 20).encode()
        ).decode()}
    return {
        "full_name": repo, "html_url": f"https://github.com/{repo}",
        "description": "Agent harness research", "archived": False, "private": False,
        "license": {"spdx_id": "MIT"}, "pushed_at": "2026-09-07T12:00:00Z",
    }


def test_github_reads_metadata_and_readme_without_executing_projects():
    calls = []
    def get(endpoint):
        calls.append(endpoint)
        return github_response(endpoint)
    candidates, failures = github_candidates(DAY, set(), get)
    assert len(candidates) == 2
    assert not failures
    assert all(item.story_type == "github" for item in candidates)
    assert all("License: MIT" in item.summary for item in candidates)
    assert len(calls) == 4
    assert all(call.startswith("repos/") for call in calls)


def test_github_stars_are_observed_metadata_not_invented_or_ranked():
    def get(endpoint):
        return github_response(endpoint) | {"stargazers_count": 7162}
    candidates, _ = github_candidates(DAY, set(), get)
    assert all(item.github_stars == 7162 for item in candidates)
    assert all(item.model_payload()["github_stars"] == 7162 for item in candidates)
    assert len({item.editorial_score for item in candidates}) == 1
    unknown, _ = github_candidates(DAY, set(), github_response)
    assert all(item.github_stars is None for item in unknown)


def test_archived_or_unlicensed_repositories_are_not_recommended():
    def get(endpoint):
        value = github_response(endpoint)
        value["archived"] = True
        return value
    candidates, _ = github_candidates(DAY, set(), get)
    assert candidates == []


def test_github_discovers_new_projects_when_recent_pool_is_exhausted():
    calls = []
    def get(endpoint):
        calls.append(endpoint)
        if endpoint.startswith("search/repositories?"):
            return {"items": [{"full_name": "example/agent-evaluation"}]}
        return github_response(endpoint)
    used = {f"https://github.com/{repo}" for repo in GITHUB_REPOS}
    candidates, failures = github_candidates(DAY, used, get)
    assert not failures
    assert [candidate.source for candidate in candidates] == ["example/agent-evaluation"]
    assert sum(call.startswith("search/") for call in calls) == 1


def test_each_reader_module_gets_evidence_and_evergreen_is_labelled():
    examples = {
        "rss_metr": ("Agent benchmark evaluation experiments", "research", "2026-09-07"),
        "rss_ethan_mollick": ("Prompt context workflow API coding tools", "news", "2026-09-07"),
        "rss_pieter_levels": ("Founder startup product users pricing distribution", "news", "2026-09-07"),
        "rss_air_street_press": ("Venture investment capital AI market economics", "essay", "2026-06-01"),
        "rss_morgan_housel": ("Mental model assumption incentive decision framework", "essay", "2026-07-01"),
    }
    def metadata(source, _day):
        if source.source_id not in examples:
            return MetadataObservation(True, True, "ZERO_YIELD", ())
        title, kind, published = examples[source.source_id]
        return MetadataObservation(True, False, "OK", (
            MetadataItem(source.source_id, title, "https://example.com/" + source.source_id,
                         published, (title + ". ") * 15, kind),
        ))
    result = collect_modules(
        DAY, catalog=load_source_catalog(CATALOG), user_context=load_approved_user_context_v75(),
        used_urls=set(), ports=CollectionPorts(metadata, lambda _url: "Concrete source evidence with method and limitations. " * 30),
        get_github=github_response,
        get_papers=lambda _day, _used: ([Candidate(
            evidence_id="paper-2405.15793", source="arXiv", title="Agent interfaces",
            link="https://arxiv.org/abs/2405.15793", published="2024-05-06",
            summary="Method and experiment with limitations. " * 100,
            content_type="evergreen", fulltext_enriched=True, story_type="research",
            evidence_role="PAPER_PRIMARY",
        )], {"fulltext_attempts": 1}),
    )
    assert {candidate.story_type for candidate in result.candidates} == set(SECTIONS)
    assert result.audit["missing_modules"] == []
    assert result.evidence_level.value == "A"
    assert all(candidate.content_type == "evergreen"
               for candidate in result.candidates if candidate.story_type in {"vc", "cognition"})
    assert result.audit["fulltext_attempt_count"] <= 18
