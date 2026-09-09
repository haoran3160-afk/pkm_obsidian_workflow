import json
from datetime import date, timedelta
from pathlib import Path

from pkm_workflow.ai_daily_luna import MODEL, run_luna_stage
from pkm_workflow.cadence import sections_for
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel
from pkm_workflow.weekly_collection import collect_weekly


def test_weekly_without_verified_daily_history_cannot_invent_six_modules(tmp_path):
    result = collect_weekly(date(2026, 9, 13), tmp_path, tmp_path)
    assert result.candidates == ()
    assert result.coverage.value == "INSUFFICIENT"
    assert len(result.audit["missing_modules"]) == 6


def test_six_daily_runs_feed_one_weekly_without_fetching_or_repeating_daily_output(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    context = {"fields": {"projects": ["Agent research"]}, "user_context_hash": "sha256:" + "a" * 64}
    def call(stage, day, **kwargs):
        return run_luna_stage(stage, today=day, runtime_root=tmp_path, vault_daily_dir=vault,
                              context_loader=lambda: context, **kwargs)
    def complete(prepared, day):
        packet = json.loads(Path(prepared["generator_input_path"]).read_text(encoding="utf-8"))
        by_section = {row["story_type"]: row for row in packet["candidates"]}
        stories = [{"section": section, "evidence_id": by_section[section]["evidence_id"],
                    "title": f"{section} observation", "body": ["The experiment records tool failures."],
                    "takeaway": "Recording failures makes the tool boundary testable.",
                    "connection": "This supports the approved research project.", "context_refs": ["projects[0]"],
                    "action": None, "priority": "USEFUL"} for section in packet["requested_sections"]]
        Path(prepared["draft_path"]).write_text(json.dumps({"model": MODEL,
            "session_id": "/root/test_generator", "draft": {"stories": stories}}), encoding="utf-8")
        review = call("review", day, run_id=prepared["run_id"])
        request = json.loads(Path(review["review_input_path"]).read_text(encoding="utf-8"))
        response = {"model": MODEL, "session_id": "/root/test_reviewer",
                    "review_request_hash": review["review_request_hash"], "decisions": [
                        {"claim_id": row["claim_id"], "evidence_ids": row["evidence_ids"],
                         "review_requirement_hash": row["review_requirement_hash"], "decision": "ACCEPT",
                         "reason_code": "SUPPORTED_BY_SEALED_EVIDENCE"} for row in request["rubric"]["requirements"]]}
        Path(review["review_path"]).write_text(json.dumps(response), encoding="utf-8")
        return call("finalize", day, run_id=prepared["run_id"], mode="production", confirm_vault_write=True)
    monday = date(2026, 9, 7)
    for offset in range(6):
        day = monday + timedelta(days=offset)
        candidates = tuple(Candidate(
            evidence_id=f"{section}-{day}", source="Primary source", title=f"{section} {day}",
            link=f"https://example.com/{section}/{day}", published=str(day),
            summary=f"by Jane Doe\nOriginal evidence from {day}: the experiment records tool failures.",
            content_type="paper" if section == "research" else "news", fulltext_enriched=True,
            story_type=section, evidence_role="PAPER_PRIMARY" if section == "research" else "PRIMARY_OR_EXPERT",
        ) for section in sections_for(day, "daily"))
        prepared = call("prepare", day, mode="production", collect=lambda _, c=candidates: CollectionResult(
            CoverageLevel.A, 2, 2, c))
        assert complete(prepared, day)["vault_write"] is True
    # Any network fetch would fail this test; weekly must use durable reviewed material.
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError("weekly must not fetch network sources")
    monkeypatch.setattr(requests, "get", forbidden)
    sunday = monday + timedelta(days=6)
    weekly = call("prepare", sunday, mode="production")
    assert weekly["edition"] == "weekly"
    assert len(weekly["requested_sections"]) == 6
    from pkm_workflow import ai_daily_luna as luna
    with monkeypatch.context() as only_durable:
        only_durable.setattr(luna, "_check_review", forbidden)
        collection = collect_weekly(sunday, tmp_path, vault)
    assert all(len(candidate.source_links) == 2 for candidate in collection.candidates)
    assert all("Verified author: Jane Doe" in candidate.summary for candidate in collection.candidates)
    assert len(collection.audit["published_days"]) == 6
    result = complete(weekly, sunday)
    assert result["vault_write"] is True
    assert Path(result["vault_path"]).name == "AI-Weekly-2026-09-13.md"
    note = Path(result["vault_path"]).read_text(encoding="utf-8")
    assert note.count("\n## ") == 6
    assert "type: ai-weekly" in note
    assert not (vault / "AI-Daily-2026-09-13.md").exists()
    assert call("prepare", sunday, mode="production")["status"] == "ALREADY_EXISTS"
