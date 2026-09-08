from pkm_workflow.daily_content import _model_candidate_payloads
from pkm_workflow.v75_collection import Candidate


def test_bounded_evidence_keeps_late_methods_and_limits():
    candidates = tuple(Candidate(
        evidence_id=f"e{i}", source="Builder", title="Agent build retrospective",
        link=f"https://example.com/{i}", published="2026-09-08", content_type="essay",
        fulltext_enriched=True, story_type="builder",
        summary=("Decorative introduction about backgrounds and colors. " * 200)
        + "\nFailure and limitations: the code grew past 3000 lines and tests became slow."
        + "\nMethod: directory limits and a user feedback loop reduced repeated work.",
    ) for i in range(12))
    packet = _model_candidate_payloads(candidates, {"fields": {"projects": ["Agent research"]}})
    assert sum(len(item["summary"]) for item in packet) <= 24000
    assert all("3000 lines" in item["summary"] for item in packet)
    assert all("feedback loop" in item["summary"] for item in packet)


def test_author_survives_excerpt_budget_even_when_feed_names_someone_else():
    candidate = Candidate(
        evidence_id="author", source="Morgan Housel / Collaborative Fund",
        title="A company and public goods", link="https://example.com/article",
        published="2026-07-27", content_type="evergreen", story_type="cognition",
        fulltext_enriched=True,
        summary="A company and public goods\nJul 27, 2026\nby\n\nCraig Shapiro\n"
        + "An incentive framework connects company growth and public goods. " * 100,
    )
    payload = _model_candidate_payloads((candidate,), {"fields": {}})[0]
    assert payload["observed_author"] == "Craig Shapiro"
