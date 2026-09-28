import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pkm_workflow.ai_daily_luna import MODEL, run_luna_stage
from pkm_workflow.cadence import sections_for
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel
from pkm_workflow.weekly_collection import collect_weekly


def test_weekly_supplement_uses_only_missing_modules_and_marks_new_reading(tmp_path):
    calls = []
    def supplement(sections):
        calls.append(sections)
        candidates = tuple(Candidate(
            evidence_id=section, source="Approved source", title="Unread classic",
            link=f"https://example.com/{section}", published="2025-10-01",
            summary="Original method, results and limitations. " * 100,
            content_type="evergreen", fulltext_enriched=True, story_type=section,
            evidence_role="PAPER_PRIMARY" if section == "research" else "PRIMARY_OR_EXPERT",
        ) for section in sections)
        return CollectionResult(CoverageLevel.A, 6, 6, candidates)
    result = collect_weekly(date(2026, 9, 20), tmp_path, tmp_path, supplement=supplement)
    assert len(calls) == 1
    assert set(calls[0]) == {"research", "ai_practice", "builder", "vc", "cognition", "github"}
    assert result.coverage == CoverageLevel.A
    assert len(result.candidates) == 6
    assert result.audit["supplemented_sections"] == list(calls[0])
    assert result.audit["supplemented_urls"] == []
    assert {candidate.link for candidate in result.candidates} == {
        f"https://example.com/{section}" for section in calls[0]
    }
    assert all(candidate.content_type == "evergreen" for candidate in result.candidates)
    assert all(candidate.source_links[0][1] == candidate.link for candidate in result.candidates)


def test_weekly_without_verified_daily_history_cannot_invent_six_modules(tmp_path):
    result = collect_weekly(date(2026, 9, 13), tmp_path, tmp_path)
    assert result.candidates == ()
    assert result.coverage.value == "INSUFFICIENT"
    assert len(result.audit["missing_modules"]) == 6


def test_weekly_keeps_verified_supplement_when_other_lanes_are_unavailable(tmp_path):
    builder = Candidate(
        evidence_id="builder", source="Approved source", title="One builder case",
        link="https://example.com/builder-case", published="2026-09-20",
        summary="The original case describes a documented product decision.",
        content_type="evergreen", fulltext_enriched=True, story_type="builder",
    )
    result = collect_weekly(
        date(2026, 9, 20), tmp_path, tmp_path,
        supplement=lambda _sections: CollectionResult(
            CoverageLevel.INSUFFICIENT, 6, 1, (builder,),
            evidence_level=CoverageLevel.INSUFFICIENT,
        ),
    )
    assert [candidate.link for candidate in result.candidates] == [builder.link]
    assert result.coverage is CoverageLevel.B
    assert "builder" not in result.audit["missing_modules"]
    assert "research" in result.audit["missing_modules"]


def test_weekly_event_filter_keeps_substantive_registration_guide(tmp_path):
    invitation = Candidate(
        evidence_id="invite", source="Event Host", title="Register for an agent meetup",
        link="https://example.com/invite", published="2026-09-20",
        summary="We are hosting an evening event. Register to attend.",
        content_type="news", fulltext_enriched=True, story_type="ai_practice",
        editorial_score=20000,
    )
    guide = Candidate(
        evidence_id="guide", source="Engineer", title="Register tool calls and issue tickets",
        link="https://example.com/guide", published="2026-09-20",
        summary="The guide documents how tool registrations and issue tickets are audited.",
        content_type="technical", fulltext_enriched=True, story_type="ai_practice",
        editorial_score=5000,
    )
    result = collect_weekly(
        date(2026, 9, 20), tmp_path, tmp_path,
        supplement=lambda _sections: CollectionResult(
            CoverageLevel.A, 6, 2, (invitation, guide),
        ),
    )
    assert [candidate.link for candidate in result.candidates] == [guide.link]


def test_weekly_uses_nonduplicate_backup_and_rejects_unqualified_supplements(tmp_path):
    from pkm_workflow.v75_collection import AccessState
    def supplement(_sections):
        return CollectionResult(CoverageLevel.A, 2, 2, tuple(Candidate(
            evidence_id=identifier, source="Approved", title="Project", link=url,
            published="2026-09-20", summary="Verified public body. " * 100,
            content_type="project", fulltext_enriched=True, story_type=section,
            access_state=access,
        ) for identifier, section, url, access in (
            ("practice", "ai_practice", "https://example.com/shared/", AccessState.FULL_FREE),
            ("github-a", "github", "https://example.com/shared/?utm_source=feed", AccessState.FULL_FREE),
            ("github-b", "github", "https://example.com/backup", AccessState.FULL_FREE),
            ("paper", "research", "https://example.com/not-a-paper", AccessState.FULL_FREE),
        )))
    result = collect_weekly(date(2026, 9, 20), tmp_path, tmp_path, supplement=supplement)
    assert {c.link for c in result.candidates} == {"https://example.com/shared/", "https://example.com/backup"}
    assert "research" in result.audit["missing_modules"]
    assert result.coverage is CoverageLevel.A
    assert "research" in result.audit["unavailable_sections"]


@pytest.mark.parametrize("long_evidence", [False, True])
def test_six_daily_runs_feed_one_weekly_without_fetching_or_repeating_daily_output(tmp_path, monkeypatch, long_evidence):
    vault = tmp_path / "vault"
    vault.mkdir()
    context = {"fields": {"projects": ["Agent research"]}, "user_context_hash": "sha256:" + "a" * 64}
    def call(stage, day, **kwargs):
        return run_luna_stage(stage, today=day, runtime_root=tmp_path, vault_daily_dir=vault,
                              context_loader=lambda: context, **kwargs)
    def complete(prepared, day):
        packet = json.loads(Path(prepared["generator_input_path"]).read_text(encoding="utf-8"))
        if packet["edition"] == "weekly":
            eid = packet["candidates"][0]["evidence_id"]
            def clause(text):
                return {"text": text, "evidence_ids": [eid]}
            draft = {"lead": None, "signals": [{
                "kind": "CASE", "title": clause("A verified agent evaluation case"),
                "body": [clause("The original records tool failures.")],
                "takeaway": clause("Failure records offer one bounded evaluation case."),
                "connection": clause("This is relevant to the approved research project.")
                | {"context_refs": ["projects[0]"]},
                "watch": None,
            }]}
        else:
            by_section = {row["story_type"]: row for row in packet["candidates"]}
            stories = [{"section": section, "evidence_id": by_section[section]["evidence_id"],
                        "title": f"{section} observation", "body": ["The experiment records tool failures."],
                        "takeaway": "Recording failures makes the tool boundary testable.",
                        "connection": "This supports the approved research project.", "context_refs": ["projects[0]"],
                        "action": None, "priority": "USEFUL"} for section in packet["requested_sections"]]
            draft = {"stories": stories}
        Path(prepared["draft_path"]).write_text(json.dumps({"model": MODEL,
            "session_id": "/root/test_generator", "draft": draft}), encoding="utf-8")
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
            summary=(f"by Jane Doe\nOriginal evidence from {day}: the experiment records tool failures."
                     + ("\nBackground narrative about the organization. " * 50
                        + "\nThe experiment limitation: context evaluation failed outside the tested environment."
                        if long_evidence else "")),
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
        collection = collect_weekly(sunday, tmp_path, vault, supplement=forbidden)
    assert all(len(candidate.source_links) == 1 for candidate in collection.candidates)
    assert all("author: Jane Doe" in candidate.summary for candidate in collection.candidates)
    if long_evidence:
        assert all("outside the tested environment." in candidate.summary for candidate in collection.candidates)
    packet = json.loads(Path(weekly["generator_input_path"]).read_text(encoding="utf-8"))
    assert sum(len(row["summary"]) for row in packet["candidates"]) <= 24000
    assert len(collection.audit["published_days"]) == 6
    result = complete(weekly, sunday)
    assert result["vault_write"] is True
    assert Path(result["vault_path"]).name == "AI-Weekly-2026-09-13.md"
    note = Path(result["vault_path"]).read_text(encoding="utf-8")
    assert note.count("\n## ") == 1
    assert "A verified agent evaluation case" in note
    assert "## 🧪 学术研究" not in note
    assert "type: ai-weekly" in note
    assert not (vault / "AI-Daily-2026-09-13.md").exists()
    assert call("prepare", sunday, mode="production")["status"] == "ALREADY_EXISTS"
