"""Balanced collection for five reading needs and one GitHub recommendation."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from typing import Any
from urllib.parse import urlencode

from .daily_brief import SECTIONS
from .paper_collection import paper_candidates
from .source_catalog import SourceCatalog
from .v75_collection import (
    Candidate,
    CollectionPorts,
    CollectionResult,
    CoverageLevel,
    MetadataObservation,
    _candidate,
    _coverage_level,
    _is_due,
    _is_paid_learning_promotion,
    _is_public_fulltext,
    _policy_mapping,
    _ranked_metadata,
)

PILLAR_SECTION = {
    "AGENTIC_RESEARCH": "research", "AI_MASTERY": "ai_practice",
    "BUILDERS": "builder", "VC": "vc", "COGNITION": "cognition",
}
GITHUB_REPOS = (
    "SWE-agent/mini-swe-agent", "SWE-agent/SWE-agent", "SWE-bench/SWE-bench",
    "UKGovernmentBEIS/inspect_ai", "stanfordnlp/dspy", "langchain-ai/langgraph",
    "All-Hands-AI/OpenHands", "ServiceNow/BrowserGym", "xlang-ai/OSWorld",
    "METR/RE-Bench",
)


def github_json(endpoint):
    if not (re.fullmatch(r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/readme)?", endpoint)
            or endpoint.startswith("search/repositories?q=")):
        raise ValueError("GITHUB_ENDPOINT_INVALID")
    process = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True, encoding="utf-8",
        timeout=25, check=False,
    )
    if process.returncode:
        raise ValueError("GITHUB_FETCH_FAILED")
    value = json.loads(process.stdout)
    if not isinstance(value, dict):
        raise ValueError("GITHUB_RESPONSE_INVALID")
    return value


def github_candidates(day, used_urls, get_json=github_json):
    """Read only public metadata and README; do not clone/install/run projects."""
    found = []
    failures = []
    offset = day.toordinal() % len(GITHUB_REPOS)
    repos = GITHUB_REPOS[offset:] + GITHUB_REPOS[:offset]
    used = {u.casefold().rstrip("/") for u in used_urls}
    if all(f"https://github.com/{repo}".casefold() in used for repo in repos):
        query = urlencode({
            "q": "topic:ai-agents archived:false fork:false stars:>50",
            "sort": "updated", "per_page": 10,
        })
        try:
            discovered = get_json("search/repositories?" + query).get("items", [])
            repos = tuple(row["full_name"] for row in discovered
                          if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row.get("full_name", "")))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return [], [{"source_id": "github_search", "reason": "GITHUB_DISCOVERY_UNAVAILABLE"}]
    for repo in repos:
        link = f"https://github.com/{repo}"
        if link.casefold().rstrip("/") in {u.casefold().rstrip("/") for u in used_urls}:
            continue
        try:
            metadata = get_json(f"repos/{repo}")
            canonical = metadata["html_url"]
            if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", canonical):
                raise ValueError("GITHUB_CANONICAL_INVALID")
            license_id = (metadata.get("license") or {}).get("spdx_id")
            if (metadata.get("archived") or metadata.get("private")
                    or not license_id or license_id == "NOASSERTION"
                    or canonical.casefold().rstrip("/") in {u.casefold().rstrip("/") for u in used_urls}):
                continue
            readme = get_json(f"repos/{repo}/readme")
            body = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
            if len(body.strip()) < 400:
                continue
            pushed = metadata.get("pushed_at", "")[:10]
            summary = (
                f"Repository: {metadata['full_name']}\nDescription: {metadata.get('description')}\n"
                f"License: {license_id}\nArchived: false\nLast push: {pushed}\n"
                f"Verified on: {day}\nREADME (project author's own documentation):\n{body[:24000]}"
            )
            found.append(Candidate(
                evidence_id="github-" + hashlib.sha256(canonical.encode()).hexdigest()[:24],
                source=metadata["full_name"], title=metadata["full_name"], link=canonical,
                published=pushed or str(day), summary=summary, content_type="project",
                fulltext_enriched=True, source_id="github_" + repo.replace("/", "_"),
                canonical_origin_id=metadata["full_name"].casefold(),
                evidence_role="PROJECT_PRIMARY", pillars=("AGENTIC_RESEARCH", "AI_MASTERY"),
                story_type="github", editorial_score=8000,
                github_stars=metadata.get("stargazers_count"),
            ))
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            failures.append({"source_id": repo, "reason": "GITHUB_FREE_README_UNAVAILABLE"})
        if len(found) == 2 or len(failures) >= 3:
            break
    return found, failures


def _module(source, item, candidate):
    primary = PILLAR_SECTION[source.pillars[0]]
    if primary in {"vc", "cognition", "builder"}:
        return primary
    # Research is populated exclusively by verified primary-paper collection.
    return "ai_practice"


def collect_modules(
    day: date, *, catalog: SourceCatalog, user_context, used_urls: set[str],
    ports: CollectionPorts, get_github=github_json, get_papers=paper_candidates, evergreen=True,
) -> CollectionResult:
    sources = [s for s in catalog.sources if _is_due(s, day) and s.adapter_kind != "ARXIV_QUERY"]
    def fetch(source):
        try:
            return source.source_id, ports.fetch_metadata(source, day)
        except (OSError, ValueError, TimeoutError):
            return source.source_id, MetadataObservation(False, False, "FETCH_FAILED", ())
    with ThreadPoolExecutor(max_workers=6) as pool:
        observations = dict(pool.map(fetch, sources))
    core = [s for s in sources if s.coverage_member and s.selection_role == "CORE_DAILY"]
    healthy = sum(observations[s.source_id].healthy for s in core)
    coverage = _coverage_level(healthy, len(core), a_bps=8500, b_bps=7000)
    audit: dict[str, Any] = {
        "source_observations": [
            {"source_id": s.source_id, "healthy": observations[s.source_id].healthy,
             "reason_code": observations[s.source_id].reason_code, "coverage_member": s in core}
            for s in sources
        ],
        "fulltext_attempt_count": 0, "metadata_qualified_count": 0, "exclusions": [],
        "selection_policy": "ONE_STORY_PER_READER_MODULE",
    }
    if coverage is CoverageLevel.INSUFFICIENT:
        return CollectionResult(coverage, len(core), healthy, (), audit=audit)
    ranked = _ranked_metadata(
        observations=observations, sources={s.source_id: s for s in sources},
        content_date=day, user_context=user_context,
        source_quality_scores=_policy_mapping(catalog.policies, "source_quality_scores"),
        quality_threshold=5000, used_urls=used_urls, balanced_modules=True,
    )
    buckets: dict[str, list[Candidate]] = {key: [] for key in SECTIONS}
    attempted = set()
    origins = set()
    assessment = {s.source_id for s in core if observations[s.source_id].healthy and (
        observations[s.source_id].zero_yield or any(len(i.summary) >= 200 for i in observations[s.source_id].items)
    )}
    def consume(rows, *, classics=False):
        for row in rows:
            if audit["fulltext_attempt_count"] >= 14:
                break
            preliminary = _candidate(row, row.item.summary, "CANONICAL_EXCERPT")
            module = _module(row.source, row.item, preliminary)
            if module not in buckets or len(buckets[module]) >= (1 if classics else 2):
                continue
            url = row.item.canonical_url
            if url in attempted or row.source.canonical_origin_id in origins:
                continue
            attempted.add(url)
            if row.source.access_policy != "FREE_ENTRY_REQUIRED":
                audit["exclusions"].append({"source_id": row.source.source_id, "reason": "FREE_BODY_NOT_VERIFIED"})
                continue
            audit["fulltext_attempt_count"] += 1
            try:
                fulltext = ports.fetch_fulltext(url)
            except (OSError, ValueError, TimeoutError):
                fulltext = ""
            if not _is_public_fulltext(fulltext) or _is_paid_learning_promotion(fulltext):
                audit["exclusions"].append({"source_id": row.source.source_id, "reason": "FREE_BODY_UNAVAILABLE"})
                continue
            candidate = _candidate(row, fulltext, "CANONICAL_EXCERPT")
            candidate = replace(candidate, story_type=module,
                                content_type="evergreen" if classics else candidate.content_type)
            buckets[module].append(candidate)
            origins.add(row.source.canonical_origin_id)
            assessment.add(row.source.source_id)
    audit["metadata_qualified_count"] = len(ranked)
    # Visit one item in each module before its backup.
    ordered = []
    for turn in range(2):
        for module in list(SECTIONS)[:-1]:
            matches = [row for row in ranked if _module(
                row.source, row.item, _candidate(row, row.item.summary, "CANONICAL_EXCERPT")
            ) == module]
            if len(matches) > turn:
                ordered.append(matches[turn])
    consume(ordered + list(ranked))
    if evergreen and any(not buckets[key] for key in list(SECTIONS)[:-1]):
        older = _ranked_metadata(
            observations=observations, sources={s.source_id: s for s in sources},
            content_date=day, user_context=user_context,
            source_quality_scores=_policy_mapping(catalog.policies, "source_quality_scores"),
            quality_threshold=5000, used_urls=used_urls, balanced_modules=True, max_age_days=365,
        )
        consume(older, classics=True)
    projects, failures = github_candidates(day, used_urls, get_json=get_github)
    buckets["github"] = projects
    if any(s.adapter_kind == "ARXIV_QUERY" and s.operational_status == "ACTIVE" for s in catalog.sources):
        papers, paper_audit = get_papers(day, used_urls)
        buckets["research"] = papers
        audit["paper_collection"] = paper_audit
        audit["fulltext_attempt_count"] += paper_audit["fulltext_attempts"]
    audit["exclusions"].extend(failures)
    audit["module_candidates"] = {key: len(value) for key, value in buckets.items()}
    audit["missing_modules"] = [key for key, value in buckets.items() if not value]
    evidence_core = len({s.source_id for s in core} & assessment)
    audit["legacy_core_evidence_sources"] = evidence_core
    available_modules = sum(bool(value) for value in buckets.values())
    audit["evidence_availability_scope"] = "SIX_MODULES_WITH_VERIFIED_FREE_BODY"
    evidence_level = _coverage_level(available_modules, 6, a_bps=10000, b_bps=8000)
    candidates = tuple(candidate for values in buckets.values() for candidate in values)
    return CollectionResult(coverage, len(core), healthy, candidates,
                            evidence_level, len({c.source_id for c in candidates}), audit)
