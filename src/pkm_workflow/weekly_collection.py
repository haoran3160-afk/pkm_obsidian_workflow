"""Weekly synthesis inputs from verified daily publications, never another feed dump."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from datetime import timedelta

from . import ai_daily_production as publishing
from .daily_brief import SECTIONS
from .v75_collection import Candidate, CollectionResult, CoverageLevel


def _material(prepared, runtime, run_id):
    from . import ai_daily_luna as luna
    report = luna._sealed_read(prepared.shadow_report)
    snapshot = report.get("reviewed_material")
    if snapshot is not None:
        return snapshot
    # Compatibility for previously published six-module dailies. Read only that
    # receipt's exact sealed run; do not scan notes or treat arbitrary shadows as read.
    run = runtime / "scratch" / "runs" / run_id
    if luna._file_hash(run / "run-report.json") != luna._file_hash(prepared.shadow_report):
        raise ValueError("HISTORICAL_REPORT_MISMATCH")
    state = luna._sealed_read(run / "state.json")
    if state["run_id"] != run_id or luna._file_hash(run / "generator-input.json") != state["generator_input_hash"]:
        raise ValueError("HISTORICAL_INPUT_MISMATCH")
    stories, _, _, _ = luna._check_review(run, state)
    candidates = luna._candidates(state)
    packet = luna._read(run / "generator-input.json")["candidates"]
    return {"stories": list(stories), "candidates": [
        asdict(candidates[row["evidence_id"]]) | {"summary": row["summary"],
        "observed_author": row.get("observed_author")} for row in packet
    ]}


def collect_weekly(day, runtime, vault):
    from . import ai_daily_luna as luna
    start = day - timedelta(days=day.weekday())
    buckets = {section: [] for section in SECTIONS}
    reports = []
    exclusions = []
    for offset in range((day - start).days + 1):
        published_day = start + timedelta(days=offset)
        before, receipt = publishing._receipt_paths(runtime, published_day)
        if not receipt.is_file():
            continue
        destination = vault / f"AI-Daily-{published_day}.md"
        try:
            prepared = publishing._load_prepared(before, runtime, published_day, destination)
            publishing._validate_published(receipt, prepared, before, destination)
            material = _material(prepared, runtime, prepared.payload["run_id"])
            candidates = {row["evidence_id"]: row for row in material["candidates"]}
            for story in material["stories"]:
                section = story["section"]
                if section not in buckets:
                    continue
                ids = {eid for claim in story["claims"] for eid in claim["evidence_ids"]}
                for eid in ids:
                    row = candidates[eid]
                    if section == "research" and row["evidence_role"] != "PAPER_PRIMARY":
                        continue
                    judgments = [c["statement"] for c in story["claims"] if c["claim_kind"] == "editorial_inference"]
                    buckets[section].append((published_day, row, judgments))
            reports.append({"date": str(published_day), "run_id": prepared.payload["run_id"],
                            "report_sha256": luna._file_hash(prepared.shadow_report)})
        except (OSError, ValueError, KeyError, TypeError) as error:
            exclusions.append({"date": str(published_day), "reason": type(error).__name__})
    assembled = []
    for section, records in buckets.items():
        unique = {}
        for published_day, row, judgments in records:
            unique[row["link"]] = (published_day, row, judgments)
        # Normal cadence provides two items/module/week. Preserve dates and both
        # source passages instead of mixing their claims without provenance.
        items = list(unique.values())[-3:]
        if not items:
            continue
        passages = []
        links = []
        excerpt_chars = max(400, 3800 // len(items) - 500)
        for published_day, row, judgments in items:
            passages.append(
                f"Daily publication: {published_day}; original date: {row['published']}; source: {row['source']}.\n"
                f"Verified author: {row.get('observed_author') or 'not recorded; do not infer from source name'}.\n"
                f"Original evidence excerpt:\n{row['summary'][:excerpt_chars]}\n"
                f"Prior editorial interpretation (not primary fact): {' '.join(judgments)[:240]}"
            )
            links.append((f"{row['source']} · {row['published']}", row["link"]))
        latest = dict(items[-1][1])
        latest.pop("observed_author", None)
        latest["pillars"] = tuple(latest["pillars"])
        latest["profile_refs"] = tuple(latest["profile_refs"])
        latest["source_links"] = tuple(links)
        from .v75_collection import AccessState
        latest["access_state"] = AccessState(latest["access_state"])
        candidate = Candidate(**latest)
        assembled.append(replace(
            candidate,
            evidence_id="week-" + hashlib.sha256(f"{day}:{section}:{links}".encode()).hexdigest()[:24],
            source="本周已发布日报", title=f"{SECTIONS[section]} · 本周回顾",
            summary="\n\n".join(passages), content_type="weekly_excerpt",
            github_stars=None, source_links=tuple(links),
        ))
    missing = [section for section in SECTIONS if not buckets[section]]
    coverage = CoverageLevel.A if not missing else CoverageLevel.INSUFFICIENT
    return CollectionResult(coverage, len(SECTIONS), len(assembled), tuple(assembled), coverage,
                            len(assembled), {"period_start": str(start), "period_end": str(day),
                            "published_days": reports, "missing_modules": missing,
                            "exclusions": exclusions, "source_policy": "VERIFIED_DAILY_HISTORY_ONLY"})
