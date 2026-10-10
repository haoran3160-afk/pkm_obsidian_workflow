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


def test_primary_paper_keeps_reported_metrics_for_review_with_their_context():
    paper = Candidate(
        evidence_id="paper", source="arXiv", title="Agent evaluation",
        link="https://arxiv.org/abs/2405.15793", published="2024-05-06",
        content_type="paper", fulltext_enriched=True, story_type="research",
        evidence_role="PAPER_PRIMARY",
        summary="Experimental setup: 2294 SWE-bench test tasks.\nResults: the model resolves 12.47% of the benchmark.\nLimitations: the evaluation is limited to Python repositories.",
    )
    result = _model_candidate_payloads((paper,), {"fields": {}})[0]["summary"]
    assert "12.47%" in result
    assert "2294" in result
    assert "Python repositories" in result


def test_paper_line_wraps_do_not_turn_method_evidence_into_fragments():
    paper = Candidate(
        evidence_id="paper", source="arXiv", title="Verbal feedback",
        link="https://arxiv.org/abs/2303.11366", published="2023-03-20",
        content_type="paper", fulltext_enriched=True, story_type="research",
        evidence_role="PAPER_PRIMARY",
        summary="The evaluation signal is\nconverted into verbal feedback stored in memory.\nThis method does not update model weights.",
    )
    result = _model_candidate_payloads((paper,), {"fields": {}})[0]["summary"]
    assert "signal is converted into verbal feedback stored in memory." in result
