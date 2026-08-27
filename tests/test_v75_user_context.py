from __future__ import annotations

import json
from pathlib import Path

import pytest

from pkm_workflow.user_context_v75 import (
    APPROVED_USER_CONTEXT_V75_HASH,
    load_approved_user_context_v75,
    load_user_context_bundle,
)

PRIVATE_ROOT = Path(r"D:\personal\obsidian_workflow")


def test_approved_v75_context_contains_the_five_user_selected_pillars() -> None:
    bundle = load_approved_user_context_v75()

    assert bundle.user_context_hash == APPROVED_USER_CONTEXT_V75_HASH
    assert [(pillar.pillar_id, pillar.weight_bps) for pillar in bundle.pillars] == [
        ("AGENTIC_RESEARCH", 3500),
        ("AI_MASTERY", 2500),
        ("BUILDERS", 1500),
        ("VC", 1500),
        ("COGNITION", 1000),
    ]
    assert bundle.prior_knowledge == ("ReAct", "SWE-bench")
    assert bundle.verified_anchors == ()
    payload = bundle.model_payload()
    assert payload["user_context_hash"] == APPROVED_USER_CONTEXT_V75_HASH
    assert payload["knowledge_anchors"] == []


def test_context_hash_mismatch_is_rejected_before_personalization(tmp_path: Path) -> None:
    context = json.loads(
        (PRIVATE_ROOT / "UserContextContract-v7.5.json").read_text(encoding="utf-8")
    )
    context["pillars"][0]["weight_bps"] = 3499
    context_path = tmp_path / "context.json"
    anchors_path = tmp_path / "anchors.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    anchors_path.write_text('{"schema_version":"7.5","anchors":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="USER_CONTEXT_HASH_MISMATCH"):
        load_user_context_bundle(
            context_path=context_path,
            anchors_path=anchors_path,
            expected_context_hash=APPROVED_USER_CONTEXT_V75_HASH,
            allowed_vault_root=tmp_path / "vault",
        )


def test_missing_allowlisted_note_is_omitted_without_scanning_or_failure(tmp_path: Path) -> None:
    context_path = PRIVATE_ROOT / "UserContextContract-v7.5.json"
    missing_note = tmp_path / "vault" / "10-Notes" / "missing.md"
    anchors = {
        "schema_version": "7.5",
        "anchors": [
            {
                "anchor_id": "missing",
                "absolute_note_path": str(missing_note),
                "title": "Missing",
                "wikilink": "[[Missing]]",
                "topics": ["agent"],
                "read_status": "READ",
                "approved_excerpt": "",
                "note_hash": "sha256:" + "0" * 64,
            }
        ],
    }
    anchors_path = tmp_path / "anchors.json"
    anchors_path.write_text(json.dumps(anchors), encoding="utf-8")

    bundle = load_user_context_bundle(
        context_path=context_path,
        anchors_path=anchors_path,
        expected_context_hash=APPROVED_USER_CONTEXT_V75_HASH,
        allowed_vault_root=tmp_path / "vault",
    )

    assert bundle.verified_anchors == ()


def test_allowlisted_note_outside_vault_is_rejected_without_reading_it(tmp_path: Path) -> None:
    anchors = {
        "schema_version": "7.5",
        "anchors": [
            {
                "anchor_id": "escape",
                "absolute_note_path": str(tmp_path / "outside.md"),
                "title": "Outside",
                "wikilink": "[[Outside]]",
                "topics": ["agent"],
                "read_status": "READ",
                "approved_excerpt": "",
                "note_hash": "sha256:" + "0" * 64,
            }
        ],
    }
    anchors_path = tmp_path / "anchors.json"
    anchors_path.write_text(json.dumps(anchors), encoding="utf-8")

    with pytest.raises(ValueError, match="KNOWLEDGE_ANCHOR_PATH_OUTSIDE_VAULT"):
        load_user_context_bundle(
            context_path=PRIVATE_ROOT / "UserContextContract-v7.5.json",
            anchors_path=anchors_path,
            expected_context_hash=APPROVED_USER_CONTEXT_V75_HASH,
            allowed_vault_root=tmp_path / "vault",
        )
