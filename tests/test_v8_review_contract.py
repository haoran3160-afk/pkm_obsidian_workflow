from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from test_v8_curation import build_valid_closure, curate_closure

from pkm_workflow.curation import CurationExclusion, ExclusionReason
from pkm_workflow.provenance import SourceLane, canonical_json_bytes
from pkm_workflow.review_contract import (
    AssertiveClaim,
    GeneratorDraft,
    ReviewContract,
    SourceExcerpt,
    StoryDraft,
)


def _claim(number: int, evidence_id: str) -> AssertiveClaim:
    return AssertiveClaim(
        claim_id=f"claim-{number}",
        statement=f"Assertive statement {number}",
        evidence_ids=(evidence_id,),
        claimed_risk="LOW",
    )


def _story(number: int, claims: tuple[AssertiveClaim, ...]) -> StoryDraft:
    return StoryDraft(
        story_id=f"story-{number}",
        title_claim_id=claims[0].claim_id,
        top_rank=number if number <= 3 else None,
        claims=claims,
        conclusion_claim_ids=(claims[0].claim_id,),
        recommendation_claim_ids=(),
        source_excerpts=(),
    )


def _rehash_curation(curation):
    return replace(
        curation,
        curation_result_hash="sha256:"
        + hashlib.sha256(canonical_json_bytes(curation.payload())).hexdigest(),
    )


def test_generator_cannot_self_report_low_risk_to_bypass_review() -> None:
    curation = curate_closure(build_valid_closure())
    evidence_id = curation.model_candidates[0].evidence[0].evidence_id
    claim = AssertiveClaim(
        claim_id="claim-1",
        statement="Agent release improves coding evals.",
        evidence_ids=(evidence_id,),
        claimed_risk="LOW",
    )
    draft = GeneratorDraft(
        stories=(
            StoryDraft(
                story_id="story-1",
                title_claim_id="claim-1",
                top_rank=1,
                claims=(claim,),
                conclusion_claim_ids=("claim-1",),
                recommendation_claim_ids=(),
                source_excerpts=(
                    SourceExcerpt(
                        excerpt_id="excerpt-1",
                        evidence_id=evidence_id,
                        text="Agent release improves coding evals.",
                    ),
                ),
            ),
        ),
        digest_conclusion_claim_ids=("claim-1",),
    )

    requirements = ReviewContract().derive(curation=curation, draft=draft)

    assert len(requirements) == 1
    requirement = requirements[0]
    assert requirement.required is True
    assert requirement.claim_id == "claim-1"
    assert requirement.evidence_ids == (evidence_id,)
    assert requirement.curation_result_hash == curation.curation_result_hash
    assert requirement.fetch_batch_hash == curation.fetch_batch_hash
    assert requirement.observation_set_hash == curation.observation_set_hash
    assert requirement.coverage_proof_hash == curation.coverage_proof_hash
    assert requirement.review_requirement_hash.startswith("sha256:")
    requirement.validate()
    for forged, message in (
        (replace(requirement, statement="Changed after derivation"), "review requirement hash"),
        (replace(requirement, required=False), "must remain required"),
    ):
        with pytest.raises(ValueError, match=message):
            forged.validate()


def test_claim_references_must_exist_and_stay_within_one_event_cluster() -> None:
    curation = curate_closure(build_valid_closure())
    first = curation.model_candidates[0].evidence[0].evidence_id
    second = curation.model_candidates[1].evidence[0].evidence_id

    for evidence_ids, message in (
        (("evidence-does-not-exist",), "unknown evidence"),
        ((first, second), "one event cluster"),
    ):
        claim = AssertiveClaim("claim-1", "A claim", evidence_ids, "LOW")
        draft = GeneratorDraft(
            stories=(_story(1, (claim,)),),
            digest_conclusion_claim_ids=("claim-1",),
        )
        with pytest.raises(ValueError, match=message):
            ReviewContract().derive(curation=curation, draft=draft)


def test_source_excerpt_is_exact_and_cannot_fill_an_assertive_output_field() -> None:
    curation = curate_closure(build_valid_closure())
    item = curation.model_candidates[0].evidence[0]
    claim = _claim(1, item.evidence_id)
    invented = SourceExcerpt(
        excerpt_id="excerpt-1",
        evidence_id=item.evidence_id,
        text="This sentence is absent from the source.",
    )
    draft = GeneratorDraft(
        stories=(
            replace(
                _story(1, (claim,)),
                source_excerpts=(invented,),
            ),
        ),
        digest_conclusion_claim_ids=(claim.claim_id,),
    )
    with pytest.raises(ValueError, match="exact evidence span"):
        ReviewContract().derive(curation=curation, draft=draft)

    for altered in (
        item.title.swapcase(),
        item.title.replace(" ", "  ", 1),
        item.title.replace(" ", ", ", 1),
    ):
        altered_draft = replace(
            draft,
            stories=(
                replace(
                    draft.stories[0],
                    source_excerpts=(replace(invented, text=altered),),
                ),
            ),
        )
        with pytest.raises(ValueError, match="exact evidence span"):
            ReviewContract().derive(curation=curation, draft=altered_draft)

    exact = replace(invented, text=item.content_text)
    injected = GeneratorDraft(
        stories=(
            replace(
                _story(1, (claim,)),
                title_claim_id=exact.excerpt_id,
                source_excerpts=(exact,),
            ),
        ),
        digest_conclusion_claim_ids=(claim.claim_id,),
    )
    with pytest.raises(ValueError, match="only assertive claims"):
        ReviewContract().derive(curation=curation, draft=injected)


def test_story_claim_and_total_limits_are_enforced_without_partial_requirements() -> None:
    curation = curate_closure(build_valid_closure())
    evidence_id = curation.model_candidates[0].evidence[0].evidence_id
    five_claims = tuple(_claim(number, evidence_id) for number in range(1, 6))
    with pytest.raises(ValueError, match="too many claims in a story"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=(_story(1, five_claims),),
                digest_conclusion_claim_ids=("claim-1",),
            ),
        )

    stories = tuple(
        _story(
            story_number,
            tuple(_claim((story_number - 1) * 4 + offset, evidence_id) for offset in range(1, 5)),
        )
        for story_number in range(1, 8)
    )
    with pytest.raises(ValueError, match="too many assertive claims"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=stories,
                digest_conclusion_claim_ids=("claim-1",),
            ),
        )

    nine_stories = tuple(_story(number, (_claim(number, evidence_id),)) for number in range(1, 10))
    with pytest.raises(ValueError, match="too many stories"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=nine_stories,
                digest_conclusion_claim_ids=("claim-1",),
            ),
        )


def test_claim_and_excerpt_identities_are_globally_disjoint_in_any_story_order() -> None:
    curation = curate_closure(build_valid_closure())
    evidence_id = curation.model_candidates[0].evidence[0].evidence_id
    first_claim = _claim(1, evidence_id)
    first_story = replace(
        _story(1, (first_claim,)),
        source_excerpts=(
            SourceExcerpt(
                excerpt_id="claim-2",
                evidence_id=evidence_id,
                text=curation.model_candidates[0].evidence[0].content_text,
            ),
        ),
    )
    second_story = _story(2, (_claim(2, evidence_id),))

    with pytest.raises(ValueError, match="globally unique"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=(first_story, second_story),
                digest_conclusion_claim_ids=("claim-1",),
            ),
        )


def test_review_schema_rejects_invalid_identity_rank_and_references() -> None:
    curation = curate_closure(build_valid_closure())
    evidence_id = curation.model_candidates[0].evidence[0].evidence_id
    valid_claim = _claim(1, evidence_id)
    valid_story = _story(1, (valid_claim,))
    cases = (
        (
            GeneratorDraft(
                stories=(replace(valid_story, story_id="BAD"),),
                digest_conclusion_claim_ids=("claim-1",),
            ),
            "stable lowercase identity",
        ),
        (
            GeneratorDraft(
                stories=(replace(valid_story, top_rank=4),),
                digest_conclusion_claim_ids=("claim-1",),
            ),
            "top rank",
        ),
        (
            GeneratorDraft(
                stories=(replace(valid_story, claims=(valid_claim, valid_claim)),),
                digest_conclusion_claim_ids=("claim-1",),
            ),
            "claim IDs must be unique",
        ),
        (
            GeneratorDraft(
                stories=(
                    valid_story,
                    replace(valid_story, story_id="story-2", top_rank=2),
                ),
                digest_conclusion_claim_ids=("claim-1",),
            ),
            "globally unique",
        ),
        (
            GeneratorDraft(
                stories=(valid_story,),
                digest_conclusion_claim_ids=("claim-unknown",),
            ),
            "digest conclusions",
        ),
    )
    for draft, message in cases:
        with pytest.raises(ValueError, match=message):
            ReviewContract().derive(curation=curation, draft=draft)

    invalid_claims = (
        (replace(valid_claim, claim_id="BAD"), "stable lowercase identity"),
        (replace(valid_claim, statement=""), "non-empty text"),
        (replace(valid_claim, evidence_ids=()), "unique evidence"),
    )
    for claim, message in invalid_claims:
        with pytest.raises(ValueError, match=message):
            ReviewContract().derive(
                curation=curation,
                draft=GeneratorDraft(
                    stories=(_story(1, (claim,)),),
                    digest_conclusion_claim_ids=(claim.claim_id,),
                ),
            )


def test_blocked_or_empty_input_cannot_create_review_requirements() -> None:
    blocked = curate_closure(build_valid_closure(extra_item_count=17))
    with pytest.raises(ValueError, match="blocked curation"):
        ReviewContract().derive(
            curation=blocked,
            draft=GeneratorDraft(stories=(), digest_conclusion_claim_ids=()),
        )
    ready = curate_closure(build_valid_closure())
    with pytest.raises(ValueError, match="at least one story"):
        ReviewContract().derive(
            curation=ready,
            draft=GeneratorDraft(stories=(), digest_conclusion_claim_ids=()),
        )


def test_model_candidate_cannot_change_evidence_while_reusing_cluster_hash() -> None:
    curation = curate_closure(build_valid_closure())
    candidate = curation.model_candidates[0]
    forged_item = replace(candidate.evidence[0], title="forged model-visible title")
    forged_candidate = replace(
        candidate,
        evidence=(forged_item, *candidate.evidence[1:]),
    )
    forged_curation = replace(
        curation,
        model_candidates=(forged_candidate, *curation.model_candidates[1:]),
    )
    claim = _claim(1, forged_item.evidence_id)

    with pytest.raises(ValueError, match="exactly match audit clusters"):
        ReviewContract().derive(
            curation=forged_curation,
            draft=GeneratorDraft(
                stories=(_story(1, (claim,)),),
                digest_conclusion_claim_ids=(claim.claim_id,),
            ),
        )


def test_curation_result_rejects_rehashed_cluster_metadata_and_duplicate_accounting() -> None:
    curation = curate_closure(build_valid_closure())
    first = curation.audit_clusters[0]
    forged_clusters = (
        replace(first, latest_event_time=first.latest_event_time + timedelta(seconds=1)),
        replace(first, best_role_rank=first.best_role_rank + 999),
        replace(first, best_profile_rank=(first.best_profile_rank or 0) + 1),
        replace(first, profile_fields=("priorities[999].topics[999]",)),
    )
    for forged_cluster in forged_clusters:
        forged = replace(
            curation,
            audit_clusters=(forged_cluster, *curation.audit_clusters[1:]),
            model_candidates=(
                forged_cluster.model_candidate(),
                *curation.model_candidates[1:],
            ),
        )
        forged = _rehash_curation(forged)
        with pytest.raises(ValueError, match="cluster"):
            forged.validate()

    overlap = replace(
        curation,
        exclusions=(
            CurationExclusion(
                evidence_id=first.evidence[0].evidence_id,
                reason=ExclusionReason.EVENT_TIME_OUTSIDE_WINDOW,
            ),
        ),
    )
    overlap = _rehash_curation(overlap)
    with pytest.raises(ValueError, match="exactly one cluster or exclusion"):
        overlap.validate()

    omitted = replace(
        curation,
        audit_clusters=curation.audit_clusters[1:],
        model_candidates=curation.model_candidates[1:],
    )
    omitted = _rehash_curation(omitted)
    with pytest.raises(ValueError, match="complete evidence partition"):
        omitted.validate()


def test_story_cannot_join_claims_or_excerpts_from_different_event_clusters() -> None:
    curation = curate_closure(build_valid_closure())
    first_item = curation.model_candidates[0].evidence[0]
    second_item = curation.model_candidates[1].evidence[0]
    first_claim = _claim(1, first_item.evidence_id)
    second_claim = _claim(2, second_item.evidence_id)
    cross_claim_story = replace(
        _story(1, (first_claim, second_claim)),
        conclusion_claim_ids=(first_claim.claim_id, second_claim.claim_id),
    )
    with pytest.raises(ValueError, match="one event cluster"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=(cross_claim_story,),
                digest_conclusion_claim_ids=(first_claim.claim_id,),
            ),
        )

    cross_excerpt_story = replace(
        _story(1, (first_claim,)),
        source_excerpts=(
            SourceExcerpt(
                excerpt_id="excerpt-1",
                evidence_id=second_item.evidence_id,
                text=second_item.content_text,
            ),
        ),
    )
    with pytest.raises(ValueError, match="one event cluster"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=(cross_excerpt_story,),
                digest_conclusion_claim_ids=(first_claim.claim_id,),
            ),
        )


def test_split_overmerged_cluster_requires_disjoint_evidence_at_review_boundary() -> None:
    shared_title = "Agent framework release improves deterministic evaluation"
    curation = curate_closure(
        build_valid_closure(
            title_overrides={
                (SourceLane.OFFICIAL_LABS, 0): shared_title,
                (SourceLane.RESEARCH, 0): shared_title,
            }
        )
    )
    candidate = next(cluster for cluster in curation.model_candidates if len(cluster.evidence) == 2)
    first_evidence, second_evidence = (item.evidence_id for item in candidate.evidence)
    first = _story(1, (_claim(1, first_evidence),))

    reused = replace(_story(2, (_claim(2, first_evidence),)), top_rank=2)
    with pytest.raises(ValueError, match="disjoint evidence"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=(first, reused),
                digest_conclusion_claim_ids=("claim-1", "claim-2"),
            ),
        )

    disjoint = replace(_story(2, (_claim(2, second_evidence),)), top_rank=2)
    requirements = ReviewContract().derive(
        curation=curation,
        draft=GeneratorDraft(
            stories=(first, disjoint),
            digest_conclusion_claim_ids=("claim-1", "claim-2"),
        ),
    )
    assert {requirement.claim_id for requirement in requirements} == {"claim-1", "claim-2"}


def test_top_ranks_are_unique_across_the_draft() -> None:
    curation = curate_closure(build_valid_closure())
    evidence_id = curation.model_candidates[0].evidence[0].evidence_id
    first = _story(1, (_claim(1, evidence_id),))
    second = replace(_story(2, (_claim(2, evidence_id),)), top_rank=1)
    with pytest.raises(ValueError, match="top ranks must be unique"):
        ReviewContract().derive(
            curation=curation,
            draft=GeneratorDraft(
                stories=(first, second),
                digest_conclusion_claim_ids=("claim-1",),
            ),
        )
