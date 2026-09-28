"""Weekly synthesis inputs from verified daily publications, never another feed dump."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, replace
from datetime import timedelta

from . import ai_daily_production as publishing
from .daily_brief import SECTIONS
from .daily_content import _section_aware_excerpt, _semantic_terms
from .v75_collection import AccessState, Candidate, CollectionResult, CoverageLevel, _normalize_url

_EVENT_TOPIC = re.compile(r"\b(?:meetup|webinar|birds of a feather)\b", re.I)
_EVENT_INTENT = re.compile(r"\b(?:register|tickets?|rsvp|join us|hosting|invites?|event)\b", re.I)


def _event_notice(candidate):
    return bool(_EVENT_TOPIC.search(candidate.title)
                and _EVENT_INTENT.search(candidate.title + " " + candidate.summary[:600]))


def _material(report_path, runtime):
    from . import ai_daily_luna as luna
    report = luna._sealed_read(report_path)
    snapshot = report.get("reviewed_material")
    if snapshot is not None:
        return snapshot | {
            "weekly_candidates": report.get("weekly_candidate_snapshot") or snapshot["candidates"],
        }
    # Compatibility for previously published six-module dailies. Read only that
    # receipt's exact sealed run; do not scan notes or treat arbitrary shadows as read.
    run_id = report_path.stem
    run = runtime / "scratch" / "runs" / run_id
    if luna._file_hash(run / "run-report.json") != luna._file_hash(report_path):
        raise ValueError("HISTORICAL_REPORT_MISMATCH")
    state = luna._sealed_read(run / "state.json")
    if state["run_id"] != run_id or luna._file_hash(run / "generator-input.json") != state["generator_input_hash"]:
        raise ValueError("HISTORICAL_INPUT_MISMATCH")
    stories, _, _, _ = luna._check_review(run, state, archived_model=report["model"])
    candidates = luna._candidates(state)
    packet = luna._read(run / "generator-input.json")["candidates"]
    selected_ids = {eid for story in stories for claim in story["claims"]
                    for eid in claim["evidence_ids"]}
    candidates = [
        asdict(candidates[row["evidence_id"]]) | {"summary": row["summary"],
        "observed_author": row.get("observed_author")}
        for row in packet if row["evidence_id"] in selected_ids
    ]
    return {"stories": list(stories), "candidates": candidates, "weekly_candidates": candidates}


def _candidate(row):
    fields = {key: value for key, value in row.items() if key != "observed_author"}
    fields["access_state"] = AccessState(fields["access_state"])
    fields["pillars"] = tuple(fields["pillars"])
    fields["profile_refs"] = tuple(fields["profile_refs"])
    fields["source_links"] = tuple(tuple(pair) for pair in fields.get("source_links", ()))
    return Candidate(**fields)


def collect_weekly(day, runtime, vault, *, supplement=None):
    from . import ai_daily_luna as luna
    start = day - timedelta(days=day.weekday())
    pool = {}
    reports = []
    exclusions = []
    for offset in range((day - start).days + 1):
        published_day = start + timedelta(days=offset)
        _, receipt = publishing._receipt_paths(runtime, published_day)
        if not receipt.is_file():
            continue
        destination = vault / f"AI-Daily-{published_day}.md"
        try:
            report_path = publishing.load_published_report(runtime, published_day, destination)
            material = _material(report_path, runtime)
            published_ids = {eid for story in material["stories"] for row in story["claims"]
                             for eid in row["evidence_ids"]}
            for row in material["weekly_candidates"]:
                original = _candidate(row)
                if (original.story_type not in SECTIONS
                        or original.access_state is not AccessState.FULL_FREE
                        or not original.fulltext_enriched
                        or _event_notice(original)
                        or (original.story_type == "research" and original.evidence_role != "PAPER_PRIMARY")):
                    continue
                already_read = original.evidence_id in published_ids
                author = row.get("observed_author") or "原文未记录署名"
                excerpt = _section_aware_excerpt(
                    original, max_chars=1900,
                    context_terms=_semantic_terms(original.title)
                    | {"limitation", "risk", "boundary", "benchmark", "experiment"},
                )
                summary = (
                    f"Daily publication: {published_day}; original date: {original.published}; "
                    f"source: {original.source}; author: {author}.\n"
                    f"Reading status: {'本周已读回顾' if already_read else '本周新增阅读候选'}。\n"
                    f"Original evidence excerpt:\n{excerpt}"
                )
                label = f"{author} · {original.source} · {original.published}"
                candidate = replace(
                    original,
                    evidence_id="week-" + hashlib.sha256(
                        f"{day}:{_normalize_url(original.link)}".encode()
                    ).hexdigest()[:24],
                    summary=summary,
                    content_type="weekly_excerpt" if already_read else original.content_type,
                    source_links=((label, original.link),),
                )
                key = _normalize_url(original.link)
                if key not in pool or (already_read and pool[key].content_type != "weekly_excerpt"):
                    pool[key] = candidate
            reports.append({"date": str(published_day), "run_id": report_path.stem,
                            "report_sha256": luna._file_hash(report_path)})
        except (OSError, ValueError, KeyError, TypeError) as error:
            exclusions.append({"date": str(published_day), "reason": type(error).__name__})
    missing = [section for section in SECTIONS
               if section not in {candidate.story_type for candidate in pool.values()}]
    supplemented = []
    supplement_audit = {}
    supplement_unhealthy = False
    if missing and supplement is not None:
        extra = supplement(tuple(missing))
        supplement_audit = dict(extra.audit)
        supplement_unhealthy = (extra.coverage is CoverageLevel.INSUFFICIENT
                                or extra.evidence_level is CoverageLevel.INSUFFICIENT)
        for section in missing:
            options = sorted(
                (candidate for candidate in extra.candidates if candidate.story_type == section
                 and candidate.access_state is AccessState.FULL_FREE
                 and candidate.fulltext_enriched
                 and not _event_notice(candidate)
                 and (section != "research" or candidate.evidence_role == "PAPER_PRIMARY")),
                key=lambda candidate: (-candidate.editorial_score, candidate.evidence_id),
            )[:2]
            for selected in options:
                key = _normalize_url(selected.link)
                if key in pool:
                    continue
                pool[key] = replace(selected, source_links=((
                    f"{selected.source} · {selected.published}", selected.link,
                ),))
                if section not in supplemented:
                    supplemented.append(section)
    ranked = sorted(pool.values(), key=lambda candidate: (
        -candidate.editorial_score, candidate.evidence_id,
    ))
    chosen = []
    lane_counts = {}
    origin_counts = {}
    def origin(candidate):
        return (candidate.canonical_origin_id
                if candidate.canonical_origin_id != "legacy-origin" else candidate.source)
    def add(candidate, *, reserve=False):
        if (candidate in chosen or len(chosen) >= 12
                or lane_counts.get(candidate.story_type, 0) >= 3
                or (not reserve and origin_counts.get(origin(candidate), 0) >= 2)):
            return
        chosen.append(candidate)
        lane_counts[candidate.story_type] = lane_counts.get(candidate.story_type, 0) + 1
        origin_counts[origin(candidate)] = origin_counts.get(origin(candidate), 0) + 1
    for section in SECTIONS:
        selected = next((candidate for candidate in ranked if candidate.story_type == section), None)
        if selected is not None:
            add(selected, reserve=True)
    for candidate in ranked:
        add(candidate)
    absent = [section for section in SECTIONS if section not in {item.story_type for item in pool.values()}]
    coverage = (CoverageLevel.INSUFFICIENT if not chosen else
                CoverageLevel.B if exclusions or supplement_unhealthy
                or supplement_audit.get("retryable_collection_failure") else CoverageLevel.A)
    return CollectionResult(coverage, len(SECTIONS), len(chosen), tuple(chosen), coverage,
                            len(chosen), {"period_start": str(start), "period_end": str(day),
                            "published_days": reports, "missing_modules": absent,
                            "screened_sections": list(SECTIONS), "unavailable_sections": absent,
                            "weekly_pool_size": len(pool), "exclusions": exclusions,
                            "source_policy": "VERIFIED_WEEK_CANDIDATES_WITH_TARGETED_SUPPLEMENT",
                            "supplemented_sections": supplemented,
                            "supplemented_urls": [],
                            "supplement_collection": supplement_audit,
                            "retryable_collection_failure": bool(not chosen and supplement_audit.get("retryable_collection_failure"))})
