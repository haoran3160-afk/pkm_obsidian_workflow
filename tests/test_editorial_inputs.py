import json
from datetime import date
from pathlib import Path

from pkm_workflow.ai_daily_luna import run_luna_stage
from pkm_workflow.v75_collection import Candidate, CollectionResult, CoverageLevel


def test_cognition_day_receives_only_its_editorial_guidance(tmp_path):
    candidates = tuple(Candidate(
        evidence_id=section, source="Example", title="Source", link="https://example.com/" + section,
        published="2026-09-09", summary="Detailed source text. " * 100,
        content_type="news", fulltext_enriched=True, story_type=section,
    ) for section in ("cognition", "github"))
    result = run_luna_stage(
        "prepare", today=date(2026, 9, 9), runtime_root=tmp_path, vault_daily_dir=tmp_path,
        collect=lambda _: CollectionResult(CoverageLevel.A, 2, 2, candidates),
        context_loader=lambda: {"fields": {}, "user_context_hash": "sha256:" + "a" * 64},
    )
    assert result["status"] == "GENERATOR_READY"
    instructions = Path(result["instructions_path"]).read_text(encoding="utf-8")
    assert "输入什么、得到什么" in instructions
    assert "被挑战的假设" in instructions
    assert "未经查新" not in instructions
    assert "领投" not in instructions
    assert "顶会" not in instructions
    packet = json.loads(Path(result["generator_input_path"]).read_text(encoding="utf-8"))
    assert packet["requested_sections"] == ["cognition", "github"]
    assert sum(len(row["summary"]) for row in packet["candidates"]) <= 12000
