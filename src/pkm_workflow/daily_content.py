"""Model-independent evidence preparation and daily artifact types."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from .v75_collection import Candidate, CoverageLevel


@dataclass(frozen=True)
class ShadowResult:
    exit_code: int
    status: str
    content_date: date
    coverage: CoverageLevel
    candidate_count: int
    story_count: int
    request_count: int
    markdown_path: Path | None
    report_path: Path
    evidence_level: CoverageLevel = CoverageLevel.A
    error_code: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "content_date": self.content_date.isoformat(),
            "coverage": self.coverage.value,
            "evidence_level": self.evidence_level.value,
            "candidate_count": self.candidate_count,
            "story_count": self.story_count,
            "request_count": self.request_count,
            "markdown_path": str(self.markdown_path) if self.markdown_path else None,
            "report_path": str(self.report_path),
            "error_code": self.error_code,
            "vault_write": False,
            "production_activation": False,
        }


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_head() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_repository_root(),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


_RATE_PATTERN = re.compile(
    r"(?<!\d)\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*%"
)


_SAMPLE_SCOPE_PATTERN = re.compile(
    r"(?<!\d)\d+\s*/\s*\d+(?!\d)|"
    r"(?<!\d)\d+\s+out of\s+\d+(?!\d)|"
    r"(?:在|共|基于).{0,30}\d+\s*(?:次|项|个|条|份|例|段|轮|组|模型)|"
    r"\bin\s+\d+\s+(?:tests?|runs?|samples?|cases?|trials?|models?)\b",
    re.IGNORECASE,
)


def _has_unscoped_rate(value: str) -> bool:
    return _RATE_PATTERN.search(value) is not None and _SAMPLE_SCOPE_PATTERN.search(
        value
    ) is None


def _model_safe_summary(value: str) -> str:
    sentences = re.split(r"(?<=[.!?。！？])\s+", value.strip())
    retained = [sentence for sentence in sentences if not _has_unscoped_rate(sentence)]
    if retained:
        return " ".join(retained)
    return _RATE_PATTERN.sub("reported rate", value)


_GENERATOR_EVIDENCE_CHARS = 24_000


_CANDIDATE_EVIDENCE_MAX_CHARS = 4_000


_SECTION_TERMS = {
    "github": ("agent", "eval", "harness", "install", "usage", "example", "limitation", "license", "readme"),
    "research": (
        "related work",
        "prior work",
        "future work",
        "open problem",
        "method",
        "experiment",
        "benchmark",
        "evaluation",
        "result",
        "ablation",
        "baseline",
        "limitation",
        "dataset",
        "方法",
        "实验",
        "评测",
        "结果",
        "限制",
    ),
    "ai_practice": (
        "failure",
        "problem",
        "debug",
        "test",
        "evaluation",
        "context",
        "token",
        "latency",
        "cost",
        "codebase",
        "file",
        "directory",
        "structure",
        "limitation",
        "claude.md",
        "失败",
        "测试",
        "上下文",
        "成本",
        "限制",
    ),
    "builder": (
        "problem",
        "user",
        "customer",
        "launch",
        "revenue",
        "distribution",
        "feedback",
        "cost",
        "failed",
        "limitation",
        "用户",
        "收入",
        "分发",
        "反馈",
        "成本",
        "失败",
    ),
    "vc": (
        "investment",
        "market",
        "margin",
        "economics",
        "moat",
        "distribution",
        "revenue",
        "cost",
        "risk",
        "投资",
        "市场",
        "护城河",
        "收入",
        "成本",
        "风险",
    ),
    "cognition": (
        "assumption",
        "argument",
        "incentive",
        "framework",
        "counter",
        "limitation",
        "decision",
        "假设",
        "论证",
        "激励",
        "框架",
        "限制",
        "决策",
    ),
}


def _evidence_segments(value: str, *, preserve_rates: bool = False) -> list[str]:
    segments: list[str] = []
    for raw_line in value.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        pieces = re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+", line)
        for raw_piece in pieces:
            piece = raw_piece.strip()
            if not preserve_rates and _has_unscoped_rate(piece):
                continue
            while len(piece) > 600:
                cut = piece.rfind(" ", 0, 600)
                if cut < 300:
                    cut = 600
                segments.append(piece[:cut].strip())
                piece = piece[cut:].strip()
            if piece:
                segments.append(piece)
    if not segments and value.strip():
        segments = [value.strip()]
    return segments


def _safe_evidence_segment(value: str) -> str:
    sentences = re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+", value.strip())
    retained = [sentence.strip() for sentence in sentences if not _has_unscoped_rate(sentence)]
    return " ".join(sentence for sentence in retained if sentence)


def _section_aware_excerpt(
    candidate: Candidate,
    *,
    max_chars: int,
    context_terms: set[str],
) -> str:
    primary_paper = candidate.evidence_role == "PAPER_PRIMARY"
    # arXiv HTML often wraps a sentence across source lines. Rank whole sentences,
    # not isolated keyword-bearing fragments from the middle of those sentences.
    source_text = re.sub(r"\s+", " ", candidate.summary) if primary_paper else candidate.summary
    raw_segments = _evidence_segments(source_text, preserve_rates=primary_paper)
    segments = [
        safe
        for segment in raw_segments
        if (safe := segment if primary_paper else _safe_evidence_segment(segment))
    ]
    if not segments:
        return _RATE_PATTERN.sub("reported rate", candidate.summary)[:max_chars].rstrip()
    story_terms = _SECTION_TERMS[candidate.story_type]

    def score(index: int, segment: str) -> tuple[int, int, int]:
        lowered = segment.casefold()
        term_hits = sum(term in lowered for term in story_terms)
        context_hits = sum(term in lowered for term in context_terms)
        concrete = int(any(char.isdigit() for char in segment))
        heading = int(len(segment) <= 80 and term_hits > 0)
        return (term_hits * 6 + context_hits * 2 + concrete + heading * 3, -index, len(segment))

    selected: set[int] = set()
    lead_budget = max_chars // 3
    used = 0
    for index in range(min(2, len(segments))):
        segment_cost = len(segments[index]) + 1
        if used + segment_cost > lead_budget:
            continue
        selected.add(index)
        used += segment_cost
    ranked = sorted(
        range(len(segments)),
        key=lambda index: score(index, segments[index]),
        reverse=True,
    )
    for index in ranked:
        if index in selected:
            continue
        segment = segments[index]
        if used + len(segment) + 1 > max_chars:
            continue
        selected.add(index)
        used += len(segment) + 1
    ordered = [segments[index] for index in sorted(selected)]
    excerpt = "\n".join(ordered)
    safe = excerpt if primary_paper else _model_safe_summary(excerpt)
    return safe[:max_chars].rstrip()


def _model_candidate_payload(
    candidate: Candidate,
    *,
    max_chars: int = _CANDIDATE_EVIDENCE_MAX_CHARS,
    context_terms: set[str] | None = None,
) -> dict[str, object]:
    payload = candidate.model_payload()
    if candidate.content_type == "weekly_excerpt":
        # Preserve per-source dates and interpretation labels; do not re-rank
        # sentences across different days and accidentally lose their attribution.
        payload["summary"] = candidate.summary[:max_chars]
        return payload
    byline = re.search(
        r"\bby\s+([A-Z][A-Za-z'-]+(?:[ \t]+[A-Z][A-Za-z'-]+){1,3})\b",
        candidate.summary[:2000],
    )
    if byline:
        payload["observed_author"] = byline.group(1)
    payload["summary"] = _section_aware_excerpt(
        candidate,
        max_chars=max_chars,
        context_terms=context_terms or set(),
    )
    return payload


def _model_candidate_payloads(
    candidates: tuple[Candidate, ...],
    context: Mapping[str, object],
    *, total_chars: int = _GENERATOR_EVIDENCE_CHARS,
) -> tuple[dict[str, object], ...]:
    if not candidates:
        return ()
    def richness_band(candidate: Candidate) -> int:
        length = len(candidate.summary)
        if length >= 8_000:
            return 2
        if length >= 2_000:
            return 1
        return 0

    ordered_candidates = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -richness_band(candidate),
                -candidate.editorial_score,
                candidate.evidence_id,
            ),
        )
    )
    paper_count = sum(c.evidence_role == "PAPER_PRIMARY" for c in candidates)
    paper_budget = min(4000, total_chars // max(1, paper_count))
    other_budget = min(4000, (total_chars - paper_count * paper_budget)
                       // max(1, len(candidates) - paper_count))
    context_terms = _semantic_terms(
        json.dumps(context, ensure_ascii=False, sort_keys=True)
    )
    return tuple(
        _model_candidate_payload(
            candidate,
            max_chars=paper_budget if candidate.evidence_role == "PAPER_PRIMARY" else other_budget,
            context_terms=context_terms,
        )
        for candidate in ordered_candidates
    )


_GENERIC_ACTION_TERMS = {
    "agent",
    "builder",
    "current",
    "project",
    "record",
    "user",
    "本周",
    "一个",
    "用户",
    "当前",
    "相关",
    "可以",
    "需要",
    "进行",
    "记录",
    "信息",
    "观察",
    "关注",
    "影响",
    "意味",
    "启示",
    "行动",
    "验证",
    "结果",
}


def _semantic_terms(value: str) -> set[str]:
    lowered = value.casefold()
    terms = {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", lowered)
        if token not in _GENERIC_ACTION_TERMS
    }
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
        terms.update(
            sequence[index : index + 2]
            for index in range(len(sequence) - 1)
            if sequence[index : index + 2] not in _GENERIC_ACTION_TERMS
        )
    return terms


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _no_story_disposition(
    coverage: CoverageLevel,
    evidence_level: CoverageLevel,
) -> tuple[str, int, str | None]:
    if coverage is CoverageLevel.A and evidence_level is CoverageLevel.A:
        return "NO_SIGNIFICANT_NEWS", 0, None
    if evidence_level is CoverageLevel.INSUFFICIENT:
        return "EVIDENCE_INSUFFICIENT", 4, "EVIDENCE_INSUFFICIENT"
    if evidence_level is CoverageLevel.B:
        return "EVIDENCE_LIMITED", 4, "EVIDENCE_LIMITED_NO_NEWS"
    return "COVERAGE_LIMITED", 4, "COVERAGE_LIMITED_NO_NEWS"


def _validate_user_context(value: object) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or not {"fields", "user_context_hash"}.issubset(value)
        or set(value) - {"fields", "knowledge_anchors", "user_context_hash"}
        or not isinstance(value.get("fields"), dict)
        or not isinstance(value.get("user_context_hash"), str)
        or (
            "knowledge_anchors" in value
            and not isinstance(value.get("knowledge_anchors"), list)
        )
    ):
        raise ValueError("UserContext payload is invalid")
    return value


def _attach_profile_refs(
    candidates: tuple[Candidate, ...],
    context: Mapping[str, object],
) -> tuple[Candidate, ...]:
    fields = context.get("fields")
    priorities = fields.get("priorities") if isinstance(fields, dict) else None
    if not isinstance(priorities, list):
        priorities = []
    topic_refs: list[tuple[str, str]] = []
    for priority_index, priority in enumerate(priorities):
        topics = priority.get("topics") if isinstance(priority, dict) else None
        if not isinstance(topics, list):
            continue
        for topic_index, topic in enumerate(topics):
            if isinstance(topic, str) and topic.strip():
                topic_refs.append(
                    (topic.strip(), f"priorities[{priority_index}].topics[{topic_index}]")
                )
    prior_knowledge = fields.get("prior_knowledge") if isinstance(fields, dict) else None
    if isinstance(prior_knowledge, list):
        for knowledge_index, knowledge in enumerate(prior_knowledge):
            topic = knowledge.get("topic") if isinstance(knowledge, dict) else None
            if isinstance(topic, str) and topic.strip():
                topic_refs.append(
                    (topic.strip(), f"prior_knowledge[{knowledge_index}].topic")
                )
    projects = fields.get("projects") if isinstance(fields, dict) else None
    if isinstance(projects, list):
        for project_index, project in enumerate(projects):
            if isinstance(project, str) and project.strip():
                topic_refs.append((project.strip(), f"projects[{project_index}]"))
    anchors = context.get("knowledge_anchors")
    if isinstance(anchors, list):
        for anchor_index, anchor in enumerate(anchors):
            topics = anchor.get("topics") if isinstance(anchor, dict) else None
            if not isinstance(topics, list):
                continue
            for topic_index, topic in enumerate(topics):
                if isinstance(topic, str) and topic.strip():
                    topic_refs.append(
                        (
                            topic.strip(),
                            f"knowledge_anchors[{anchor_index}].topics[{topic_index}]",
                        )
                    )
    context_pillars = fields.get("pillars") if isinstance(fields, dict) else None
    pillar_refs: dict[str, tuple[str, ...]] = {}
    if isinstance(context_pillars, list):
        for pillar_index, pillar in enumerate(context_pillars):
            pillar_id = pillar.get("pillar_id") if isinstance(pillar, dict) else None
            if isinstance(pillar_id, str):
                semantic_refs = [f"pillars[{pillar_index}].purpose"]
                questions = pillar.get("selection_questions")
                if isinstance(questions, list):
                    semantic_refs.extend(
                        f"pillars[{pillar_index}].selection_questions[{question_index}]"
                        for question_index in range(len(questions))
                    )
                pillar_refs[pillar_id] = tuple(semantic_refs)
    enriched: list[Candidate] = []
    for candidate in candidates:
        searchable = f"{candidate.title}\n{candidate.summary}"
        refs = tuple(
            dict.fromkeys(
                [
                    path
                    for topic, path in topic_refs
                    if re.search(
                        rf"(?<!\w){re.escape(topic)}(?!\w)", searchable, re.IGNORECASE
                    )
                ]
                + [
                    ref
                    for pillar in candidate.pillars
                    for ref in pillar_refs.get(pillar, ())
                ]
            )
        )
        enriched.append(replace(candidate, profile_refs=refs))
    return tuple(enriched)


def _approved_context_paths(context: Mapping[str, object]) -> set[str]:
    paths: set[str] = set()

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif isinstance(value, (str, int, bool)) and path:
            paths.add(path)

    visit(context.get("fields"), "")
    visit(context.get("knowledge_anchors"), "knowledge_anchors")
    return paths


def _review_requirements(stories: tuple[dict[str, Any], ...]) -> tuple[list[dict[str, object]], str]:
    draft = {"stories": list(stories)}
    draft_hash = _digest(draft)
    requirements: list[dict[str, object]] = []
    for story in stories:
        for claim in story["claims"]:
            requirement = {
                "claim_id": claim["claim_id"],
                "statement": claim["statement"],
                "evidence_ids": claim["evidence_ids"],
                "claim_kind": claim["claim_kind"],
                "context_refs": claim["context_refs"],
                "required": True,
                "generator_draft_hash": draft_hash,
            }
            requirements.append(
                requirement | {"review_requirement_hash": _digest(requirement)}
            )
    return requirements, draft_hash
