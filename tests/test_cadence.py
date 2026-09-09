from datetime import date, timedelta

from pkm_workflow.cadence import edition_for, sections_for


def test_two_modules_rotate_and_sunday_is_weekly():
    monday = date(2026, 9, 7)
    expected = [("research", "ai_practice"), ("builder", "vc"), ("cognition", "github")]
    for offset in range(6):
        day = monday + timedelta(days=offset)
        assert edition_for(day) == "daily"
        assert sections_for(day, "daily") == expected[offset % 3]
    assert edition_for(monday + timedelta(days=6)) == "weekly"
    assert set(sections_for(monday, "weekly")) == {key for pair in expected for key in pair}


def test_sparse_research_days_still_explore_rl_and_deep_learning():
    from pkm_workflow.paper_collection import research_topic
    monday = date(2026, 9, 7)
    assert {research_topic(monday + timedelta(days=7 * n)) for n in range(3)} == {"agents"}
    assert {research_topic(monday + timedelta(days=3 + 7 * n)) for n in range(3)} == {"agents", "rl", "deep_learning"}


def test_sunday_daily_override_cannot_start_production(tmp_path):
    from pkm_workflow.ai_daily_luna import run_luna_stage
    def forbidden(_):
        raise AssertionError("off-cadence publication must not collect")
    result = run_luna_stage("prepare", mode="production", edition="daily", today=date(2026, 9, 13),
                            runtime_root=tmp_path, vault_daily_dir=tmp_path, collect=forbidden)
    assert result["status"] == "DAILY_NOT_DUE"
    assert result["vault_write"] is False


def test_sunday_daily_preview_cannot_be_promoted(tmp_path):
    from pkm_workflow.ai_daily_luna import run_luna_stage
    from pkm_workflow.v75_collection import CollectionResult, CoverageLevel
    sunday = date(2026, 9, 13)
    preview = run_luna_stage("prepare", mode="shadow", edition="daily", today=sunday,
                             runtime_root=tmp_path, vault_daily_dir=tmp_path,
                             collect=lambda _: CollectionResult(CoverageLevel.A, 0, 0, ()),
                             context_loader=lambda: {"fields": {}, "user_context_hash": "sha256:" + "a" * 64})
    result = run_luna_stage("finalize", mode="production", today=sunday, run_id=preview["run_id"],
                            runtime_root=tmp_path, vault_daily_dir=tmp_path, confirm_vault_write=True)
    assert result["status"] == "DAILY_NOT_DUE"
