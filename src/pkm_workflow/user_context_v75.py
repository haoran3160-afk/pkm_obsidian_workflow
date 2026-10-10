"""Approved v7.5 personalization context and exact knowledge-anchor allowlist."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APPROVED_USER_CONTEXT_V75_HASH = (
    "sha256:765e289b8832c986833962edca66ff8cf1e2bfc7e66610f911b79a17675e2521"
)
PRIVATE_CONTEXT_PATH = Path(
    r"D:\personal\obsidian_workflow\UserContextContract-v7.5.json"
)
PRIVATE_ANCHORS_PATH = Path(
    r"D:\personal\obsidian_workflow\Knowledge-Anchors-v7.5.json"
)
VAULT_ROOT = Path(r"D:\personal\ObsidianVault")
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_PILLAR_IDS = (
    "AGENTIC_RESEARCH",
    "AI_MASTERY",
    "BUILDERS",
    "VC",
    "COGNITION",
)


@dataclass(frozen=True)
class Pillar:
    pillar_id: str
    weight_bps: int


@dataclass(frozen=True)
class VerifiedKnowledgeAnchor:
    anchor_id: str
    title: str
    wikilink: str
    topics: tuple[str, ...]
    read_status: str
    approved_excerpt: str
    note_hash: str

    def model_payload(self) -> dict[str, object]:
        return {
            "anchor_id": self.anchor_id,
            "title": self.title,
            "wikilink": self.wikilink,
            "topics": list(self.topics),
            "read_status": self.read_status,
            "approved_excerpt": self.approved_excerpt,
            "note_hash": self.note_hash,
        }


@dataclass(frozen=True)
class UserContextBundle:
    user_context_hash: str
    fields: dict[str, object]
    pillars: tuple[Pillar, ...]
    prior_knowledge: tuple[str, ...]
    verified_anchors: tuple[VerifiedKnowledgeAnchor, ...]

    def model_payload(self) -> dict[str, object]:
        return {
            "user_context_hash": self.user_context_hash,
            "fields": self.fields,
            "knowledge_anchors": [anchor.model_payload() for anchor in self.verified_anchors],
        }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, error_code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error_code)
    return value.strip()


def _read_object(path: Path, error_code: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(error_code)
    return value


def _validate_context(raw: dict[str, Any]) -> tuple[tuple[Pillar, ...], tuple[str, ...]]:
    if raw.get("schema_version") != "7.5" or raw.get("vault_inference_allowed") is not False:
        raise ValueError("USER_CONTEXT_SCHEMA_INVALID")
    rows = raw.get("pillars")
    if not isinstance(rows, list) or len(rows) != len(_PILLAR_IDS):
        raise ValueError("USER_CONTEXT_PILLARS_INVALID")
    pillars: list[Pillar] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("USER_CONTEXT_PILLARS_INVALID")
        pillar_id = _text(row.get("pillar_id"), "USER_CONTEXT_PILLARS_INVALID")
        weight = row.get("weight_bps")
        if type(weight) is not int or not 1 <= weight <= 10_000:
            raise ValueError("USER_CONTEXT_PILLARS_INVALID")
        pillars.append(Pillar(pillar_id=pillar_id, weight_bps=weight))
    if tuple(pillar.pillar_id for pillar in pillars) != _PILLAR_IDS:
        raise ValueError("USER_CONTEXT_PILLARS_INVALID")
    if sum(pillar.weight_bps for pillar in pillars) != 10_000:
        raise ValueError("USER_CONTEXT_PILLAR_WEIGHTS_INVALID")
    prior_rows = raw.get("prior_knowledge")
    if not isinstance(prior_rows, list):
        raise ValueError("USER_CONTEXT_PRIOR_KNOWLEDGE_INVALID")
    prior = tuple(
        _text(row.get("topic"), "USER_CONTEXT_PRIOR_KNOWLEDGE_INVALID")
        for row in prior_rows
        if isinstance(row, dict)
    )
    if len(prior) != len(prior_rows):
        raise ValueError("USER_CONTEXT_PRIOR_KNOWLEDGE_INVALID")
    return tuple(pillars), prior


def _load_verified_anchors(
    anchors_path: Path,
    allowed_vault_root: Path,
) -> tuple[VerifiedKnowledgeAnchor, ...]:
    manifest = _read_object(anchors_path, "KNOWLEDGE_ANCHORS_SCHEMA_INVALID")
    if manifest.get("schema_version") != "7.5" or not isinstance(
        manifest.get("anchors"), list
    ):
        raise ValueError("KNOWLEDGE_ANCHORS_SCHEMA_INVALID")
    vault_root = allowed_vault_root.resolve(strict=False)
    verified: list[VerifiedKnowledgeAnchor] = []
    seen: set[str] = set()
    for row in manifest["anchors"]:
        if not isinstance(row, dict):
            raise ValueError("KNOWLEDGE_ANCHOR_INVALID")
        anchor_id = _text(row.get("anchor_id"), "KNOWLEDGE_ANCHOR_INVALID")
        if anchor_id in seen:
            raise ValueError("KNOWLEDGE_ANCHOR_DUPLICATE")
        seen.add(anchor_id)
        note_path = Path(
            _text(row.get("absolute_note_path"), "KNOWLEDGE_ANCHOR_INVALID")
        )
        if not note_path.is_absolute() or not note_path.resolve(strict=False).is_relative_to(
            vault_root
        ):
            raise ValueError("KNOWLEDGE_ANCHOR_PATH_OUTSIDE_VAULT")
        expected_hash = _text(row.get("note_hash"), "KNOWLEDGE_ANCHOR_INVALID")
        if not _HASH.fullmatch(expected_hash):
            raise ValueError("KNOWLEDGE_ANCHOR_HASH_INVALID")
        if not note_path.is_file():
            continue
        resolved_note = note_path.resolve(strict=True)
        if not resolved_note.is_relative_to(vault_root):
            raise ValueError("KNOWLEDGE_ANCHOR_PATH_OUTSIDE_VAULT")
        note_bytes = note_path.read_bytes()
        actual_hash = "sha256:" + hashlib.sha256(note_bytes).hexdigest()
        if actual_hash != expected_hash:
            continue
        excerpt_value = row.get("approved_excerpt")
        approved_excerpt = excerpt_value if isinstance(excerpt_value, str) else ""
        topics = row.get("topics")
        if not isinstance(topics, list) or any(
            not isinstance(topic, str) or not topic.strip() for topic in topics
        ):
            raise ValueError("KNOWLEDGE_ANCHOR_INVALID")
        verified.append(
            VerifiedKnowledgeAnchor(
                anchor_id=anchor_id,
                title=_text(row.get("title"), "KNOWLEDGE_ANCHOR_INVALID"),
                wikilink=_text(row.get("wikilink"), "KNOWLEDGE_ANCHOR_INVALID"),
                topics=tuple(topic.strip() for topic in topics),
                read_status=_text(row.get("read_status"), "KNOWLEDGE_ANCHOR_INVALID"),
                approved_excerpt=approved_excerpt,
                note_hash=actual_hash,
            )
        )
    return tuple(verified)


def load_user_context_bundle(
    *,
    context_path: Path,
    anchors_path: Path,
    expected_context_hash: str,
    allowed_vault_root: Path,
) -> UserContextBundle:
    """Read only the two exact approved files and allowlisted note paths."""

    raw = _read_object(context_path, "USER_CONTEXT_SCHEMA_INVALID")
    actual_hash = _digest(raw)
    if actual_hash != expected_context_hash:
        raise ValueError("USER_CONTEXT_HASH_MISMATCH")
    pillars, prior = _validate_context(raw)
    anchors = _load_verified_anchors(anchors_path, allowed_vault_root)
    return UserContextBundle(
        user_context_hash=actual_hash,
        fields=dict(raw),
        pillars=pillars,
        prior_knowledge=prior,
        verified_anchors=anchors,
    )


def load_approved_user_context_v75() -> UserContextBundle:
    return load_user_context_bundle(
        context_path=PRIVATE_CONTEXT_PATH,
        anchors_path=PRIVATE_ANCHORS_PATH,
        expected_context_hash=APPROVED_USER_CONTEXT_V75_HASH,
        allowed_vault_root=VAULT_ROOT,
    )
