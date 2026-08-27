"""One-shot AI Daily shadow pipeline over the existing collection and curation flow."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .deepseek_mvp_strategy import deepseek_mvp_strategy_binding
from .deepseek_provider import (
    MVP_CONTRACT_VERSION,
    DeepSeekShadowProvider,
    ProviderResult,
    ProviderRole,
    RequestBudget,
    TerminalClassification,
    mvp_generator_output_schema,
    mvp_reviewer_output_schema,
)
from .source_catalog import load_approved_source_catalog
from .user_context_v75 import load_approved_user_context_v75
from .v75_collection import (
    Candidate,
    CollectionResult,
    CoverageLevel,
    collect_v75_candidates,
    default_collection_ports,
)


@dataclass(frozen=True)
class ShadowPorts:
    collect: Callable[[date], CollectionResult]
    provider: DeepSeekShadowProvider
    runtime_root: Path
    load_user_context: Callable[[], dict[str, object]]


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


def _new_run_dir(runtime_root: Path) -> Path:
    root = runtime_root.resolve(strict=True)
    scratch = root / "scratch"
    runs = scratch / "runs"
    for existing in (scratch, runs):
        if existing.exists() and not existing.resolve(strict=True).is_relative_to(root):
            raise ValueError("RUNTIME_PATH_ESCAPE")
    return runs / uuid4().hex


def _existing_state_path(root: Path, name: str) -> Path:
    local = root / name
    if local.is_file():
        return local
    preserved = Path(r"D:\Agent_programs\obsidian_workflow_open") / name
    if preserved.is_file():
        return preserved
    return local


def _read_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _healthy_fetch(meta: Mapping[str, object]) -> bool:
    return meta.get("reason_code") in {"OK", "ZERO_YIELD"}


def _collect_v75_sources(content_date: date) -> CollectionResult:
    """Run the v7.5 catalog and metadata-first curation without persisting state."""

    import daily_curation

    root = _repository_root()
    used_urls = daily_curation.load_used_urls(_existing_state_path(root, "used_articles.json"))
    catalog = load_approved_source_catalog()
    return collect_v75_candidates(
        content_date,
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=used_urls,
        ports=default_collection_ports(catalog),
    )


def _default_ports() -> ShadowPorts:
    return ShadowPorts(
        collect=_collect_v75_sources,
        provider=DeepSeekShadowProvider(),
        runtime_root=Path(r"D:\personal\obsidian_workflow-runtime"),
        load_user_context=lambda: load_approved_user_context_v75().model_payload(),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _provider_exit(result: ProviderResult) -> int:
    if result.terminal_classification is TerminalClassification.AUTH_REQUIRED:
        return 8
    if result.terminal_classification in {
        TerminalClassification.INSUFFICIENT_BALANCE,
        TerminalClassification.RATE_LIMITED,
        TerminalClassification.EXTERNAL_SERVICE_UNAVAILABLE,
    }:
        return 5
    if result.terminal_classification is TerminalClassification.INTERNAL_ERROR:
        return 10
    return 4


def _model_metrics(*results: ProviderResult) -> dict[str, int]:
    return {
        "prompt_tokens": sum(result.token_usage.prompt_tokens for result in results),
        "completion_tokens": sum(result.token_usage.completion_tokens for result in results),
        "total_tokens": sum(result.token_usage.total_tokens for result in results),
        "latency_ms": sum(result.latency_ms for result in results),
    }


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
    pillar_refs: dict[str, str] = {}
    if isinstance(context_pillars, list):
        for pillar_index, pillar in enumerate(context_pillars):
            pillar_id = pillar.get("pillar_id") if isinstance(pillar, dict) else None
            if isinstance(pillar_id, str):
                pillar_refs[pillar_id] = f"pillars[{pillar_index}].pillar_id"
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
                + [pillar_refs[pillar] for pillar in candidate.pillars if pillar in pillar_refs]
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


def _validate_generator_draft(
    draft: object,
    evidence: Mapping[str, Candidate],
    context: Mapping[str, object],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(draft, dict) or set(draft) != {"stories"}:
        raise ValueError("GENERATOR_DRAFT_INVALID")
    stories = draft.get("stories")
    if not isinstance(stories, list) or len(stories) > 3:
        raise ValueError("GENERATOR_STORIES_INVALID")
    story_ids: set[str] = set()
    claim_ids: set[str] = set()
    ranks: set[int] = set()
    validated: list[dict[str, Any]] = []
    role_fields = (
        "event_claim_ids",
        "mechanism_claim_ids",
        "importance_claim_ids",
        "impact_claim_ids",
        "uncertainty_claim_ids",
        "watch_claim_ids",
        "action_claim_ids",
    )
    allowed_context_refs = _approved_context_paths(context)
    action_count = 0
    for story in stories:
        if not isinstance(story, dict):
            raise ValueError("GENERATOR_STORY_INVALID")
        story_id = story.get("story_id")
        rank = story.get("rank")
        claims = story.get("claims")
        if (
            not isinstance(story_id, str)
            or story_id in story_ids
            or type(rank) is not int
            or not 1 <= rank <= 3
            or rank in ranks
            or not isinstance(claims, list)
        ):
            raise ValueError("GENERATOR_STORY_ID_OR_RANK_INVALID")
        story_ids.add(story_id)
        ranks.add(rank)
        local_claims: dict[str, dict[str, Any]] = {}
        for claim in claims:
            if not isinstance(claim, dict):
                raise ValueError("GENERATOR_CLAIM_INVALID")
            claim_id = claim.get("claim_id")
            evidence_ids = claim.get("evidence_ids")
            claim_kind = claim.get("claim_kind")
            context_refs = claim.get("context_refs")
            if (
                not isinstance(claim_id, str)
                or claim_id in claim_ids
                or not isinstance(claim.get("statement"), str)
            ):
                raise ValueError("GENERATOR_CLAIM_ID_INVALID")
            if (
                not isinstance(evidence_ids, list)
                or not evidence_ids
                or any(not isinstance(item, str) or item not in evidence for item in evidence_ids)
            ):
                raise ValueError("GENERATOR_EVIDENCE_BINDING_INVALID")
            if claim_kind not in {"fact", "editorial_inference", "action"}:
                raise ValueError("GENERATOR_CLAIM_KIND_INVALID")
            if (
                not isinstance(context_refs, list)
                or len(context_refs) != len(set(context_refs))
                or any(not isinstance(item, str) for item in context_refs)
            ):
                raise ValueError("GENERATOR_CONTEXT_REFS_INVALID")
            if any(item not in allowed_context_refs for item in context_refs):
                raise ValueError("GENERATOR_CONTEXT_REF_UNKNOWN")
            candidate_context_refs = {
                ref
                for evidence_id in evidence_ids
                for ref in evidence[evidence_id].profile_refs
            }
            if any(item not in candidate_context_refs for item in context_refs):
                raise ValueError("GENERATOR_CONTEXT_REF_NOT_BOUND_TO_EVIDENCE")
            if claim_kind in {"editorial_inference", "action"} and not context_refs:
                raise ValueError("GENERATOR_CONTEXT_BINDING_REQUIRED")
            action_type = claim.get("action_type")
            action_object = claim.get("action_object")
            success_criterion = claim.get("success_criterion")
            if claim_kind == "action":
                action_count += 1
                if (
                    action_type
                    not in {
                        "IMMEDIATE_ACTION",
                        "THIS_WEEK_EXPERIMENT",
                        "RESEARCH_QUESTION",
                        "CONTINUE_WATCHING",
                    }
                    or not isinstance(action_object, str)
                    or not action_object.strip()
                    or not isinstance(success_criterion, str)
                    or not success_criterion.strip()
                ):
                    raise ValueError("GENERATOR_ACTION_BINDING_INVALID")
            elif action_type != "NONE" or action_object != "" or success_criterion != "":
                raise ValueError("GENERATOR_NON_ACTION_FIELDS_INVALID")
            claim_ids.add(claim_id)
            local_claims[claim_id] = claim
        title_claim_id = story.get("title_claim_id")
        if title_claim_id not in local_claims:
            raise ValueError("GENERATOR_TITLE_CLAIM_INVALID")
        for field in role_fields:
            ids = story.get(field)
            if not isinstance(ids, list) or any(item not in local_claims for item in ids):
                raise ValueError(f"GENERATOR_{field.upper()}_BINDING_INVALID")
        if any(
            local_claims[claim_id]["claim_kind"] != "editorial_inference"
            or not local_claims[claim_id]["context_refs"]
            for claim_id in story["impact_claim_ids"]
        ):
            raise ValueError("GENERATOR_IMPACT_CONTEXT_INVALID")
        if any(
            local_claims[claim_id]["claim_kind"] != "action"
            for claim_id in story["action_claim_ids"]
        ):
            raise ValueError("GENERATOR_ACTION_ROLE_INVALID")
        action_ids = {
            claim_id
            for claim_id, claim in local_claims.items()
            if claim["claim_kind"] == "action"
        }
        if action_ids != set(story["action_claim_ids"]):
            raise ValueError("GENERATOR_ACTION_ROLE_INVALID")
        if not story["event_claim_ids"] or not story["importance_claim_ids"]:
            raise ValueError("GENERATOR_REQUIRED_CLAIMS_MISSING")
        validated.append(story)
    if action_count > 2:
        raise ValueError("GENERATOR_DAILY_ACTION_LIMIT_EXCEEDED")
    return tuple(validated)


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
                "action_type": claim["action_type"],
                "action_object": claim["action_object"],
                "success_criterion": claim["success_criterion"],
                "required": True,
                "generator_draft_hash": draft_hash,
            }
            requirements.append(
                requirement | {"review_requirement_hash": _digest(requirement)}
            )
    return requirements, draft_hash


def _accepted_stories(
    stories: tuple[dict[str, Any], ...],
    decisions: object,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(decisions, dict) or set(decisions) != {"decisions"}:
        raise ValueError("Reviewer decision set is invalid")
    rows = decisions.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("Reviewer decisions are invalid")
    verdicts = {
        row["claim_id"]: row["decision"]
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("claim_id"), str)
        and isinstance(row.get("decision"), str)
    }
    accepted: list[dict[str, Any]] = []
    for story in stories:
        rendered_claim_ids = {
            story["title_claim_id"],
            *story["event_claim_ids"],
            *story["importance_claim_ids"],
        }
        if any(
            verdicts.get(claim_id) != "ACCEPT" for claim_id in rendered_claim_ids
        ):
            continue
        kept_claims = [
            claim for claim in story["claims"] if verdicts.get(claim["claim_id"]) == "ACCEPT"
        ]
        kept_ids = {claim["claim_id"] for claim in kept_claims}
        projected = dict(story)
        projected["claims"] = kept_claims
        for field in (
            "event_claim_ids",
            "mechanism_claim_ids",
            "importance_claim_ids",
            "impact_claim_ids",
            "uncertainty_claim_ids",
            "watch_claim_ids",
            "action_claim_ids",
        ):
            projected[field] = [claim_id for claim_id in story[field] if claim_id in kept_ids]
        accepted.append(projected)
    accepted.sort(key=lambda story: story["rank"])
    for index, story in enumerate(accepted, start=1):
        story["rank"] = index
    return tuple(accepted)


def _claim_texts(story: Mapping[str, Any], field: str) -> list[str]:
    claims = {claim["claim_id"]: claim for claim in story["claims"]}
    rendered: list[str] = []
    for claim_id in story[field]:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        text = claim["statement"]
        if claim.get("claim_kind") == "action":
            text = (
                f"{text}（对象：{claim['action_object']}；"
                f"判据：{claim['success_criterion']}）"
            )
        rendered.append(text)
    return rendered


def _render_markdown(
    content_date: date,
    coverage: CoverageLevel,
    stories: tuple[dict[str, Any], ...],
    evidence: Mapping[str, Candidate],
) -> str:
    window_start = content_date - timedelta(days=2)
    lines = [
        "---",
        f"date: {content_date.isoformat()}",
        "type: ai-daily-shadow",
        f"coverage: {coverage.value}",
        f"window_start: {window_start.isoformat()}",
        f"window_end: {content_date.isoformat()}",
        "catch_up_window: 3-calendar-days",
        "production: false",
        "---",
        "",
        f"# AI Daily · {content_date.isoformat()}",
        "",
        f"> 今日入选 {len(stories)} 条经证据与 Reviewer 双审的变化。",
        "> 信息窗口：近 72 小时补漏；条目发布日期见各证据。",
    ]
    labels = (
        ("event_claim_ids", "发生了什么"),
        ("mechanism_claim_ids", "关键细节"),
        ("importance_claim_ids", "为什么值得注意"),
        ("impact_claim_ids", "与你的连接"),
        ("uncertainty_claim_ids", "证据边界"),
        ("watch_claim_ids", "继续观察"),
        ("action_claim_ids", "可以做什么"),
    )
    for story in stories:
        claims = {claim["claim_id"]: claim for claim in story["claims"]}
        title = claims[story["title_claim_id"]]["statement"]
        lines.extend(("", f"## Top {story['rank']}｜{title}"))
        for field, label in labels:
            texts = _claim_texts(story, field)
            if texts:
                lines.append(f"- **{label}**：{'；'.join(texts)}")
        evidence_ids = []
        for claim in story["claims"]:
            for evidence_id in claim["evidence_ids"]:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        links = []
        for evidence_id in evidence_ids:
            item = evidence[evidence_id]
            label = "原文" if item.fulltext_enriched else "来源链接"
            links.append(
                f"[{label} · {item.source}]({item.link})（发布 {item.published}）"
            )
        lines.append(f"- **证据**：{'；'.join(links)}")
    return "\n".join(lines).rstrip() + "\n"


def _finish(
    *,
    ports: ShadowPorts,
    run_dir: Path,
    content_date: date,
    coverage: CoverageLevel,
    evidence_level: CoverageLevel = CoverageLevel.A,
    evidence_healthy_sources: int = 0,
    candidate_count: int,
    story_count: int,
    request_count: int,
    status: str,
    exit_code: int,
    markdown: str | None = None,
    error_code: str | None = None,
    model_metrics: Mapping[str, int] | None = None,
    personalization_refs: tuple[str, ...] = (),
    audit: Mapping[str, object] | None = None,
) -> ShadowResult:
    run_dir.mkdir(parents=True, exist_ok=False)
    markdown_path: Path | None = None
    planned_markdown_path = (
        run_dir / f"AI-Daily-{content_date.isoformat()}-shadow.md"
        if markdown is not None
        else None
    )
    report_path = run_dir / "run-report.json"
    report = {
        "schema": "pkm.ai-daily-shadow.run.v1",
        "git_head": _git_head(),
        "strategy_hash": deepseek_mvp_strategy_binding()["strategy_hash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "READY_FOR_SHADOW_LINK" if markdown is not None else status,
        "content_date": content_date.isoformat(),
        "coverage": coverage.value,
        "evidence_level": evidence_level.value,
        "evidence_healthy_sources": evidence_healthy_sources,
        "candidate_count": candidate_count,
        "story_count": story_count,
        "request_count": request_count,
        "model_metrics": dict(model_metrics or {}),
        "personalization_refs": list(personalization_refs),
        "audit": dict(audit or {}),
        "error_code": error_code,
        "markdown_expected": planned_markdown_path is not None,
        "vault_write": False,
        "production_activation": False,
    }
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(report).decode("utf-8") + "\n")
    if markdown is not None and planned_markdown_path is not None:
        backing_path = run_dir / "rendered-content.txt"
        with backing_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown)
        planned_markdown_path.hardlink_to(backing_path)
        markdown_path = planned_markdown_path
    return ShadowResult(
        exit_code=exit_code,
        status=status,
        content_date=content_date,
        coverage=coverage,
        evidence_level=evidence_level,
        candidate_count=candidate_count,
        story_count=story_count,
        request_count=request_count,
        markdown_path=markdown_path,
        report_path=report_path,
        error_code=error_code,
    )


def run_ai_daily_shadow(
    content_date: date | None = None,
    *,
    ports: ShadowPorts | None = None,
) -> ShadowResult:
    """Generate one reviewed AI Daily shadow without touching the Vault."""

    ports = ports or _default_ports()
    selected_date = content_date or date.today()
    collected = ports.collect(selected_date)
    run_dir = _new_run_dir(ports.runtime_root)
    if collected.coverage is CoverageLevel.INSUFFICIENT:
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=0,
            status="COVERAGE_INSUFFICIENT",
            exit_code=4,
            error_code="COVERAGE_INSUFFICIENT",
            audit=collected.audit,
        )
    if collected.evidence_level is CoverageLevel.INSUFFICIENT:
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=0,
            status="EVIDENCE_INSUFFICIENT",
            exit_code=4,
            error_code="EVIDENCE_INSUFFICIENT",
            audit=collected.audit,
        )
    if not collected.candidates:
        status, exit_code, error_code = _no_story_disposition(
            collected.coverage, collected.evidence_level
        )
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=0,
            story_count=0,
            request_count=0,
            status=status,
            exit_code=exit_code,
            error_code=error_code,
            audit=collected.audit,
        )
    if len(collected.candidates) > 8:
        raise ValueError("curation must provide no more than eight candidates")
    context = _validate_user_context(ports.load_user_context())
    collected = replace(
        collected,
        candidates=_attach_profile_refs(collected.candidates, context),
    )
    evidence = {item.evidence_id: item for item in collected.candidates}
    if len(evidence) != len(collected.candidates):
        raise ValueError("candidate evidence IDs must be unique")
    generator_request = _canonical(
        {
            "approved_user_context": context,
            "rubric": {
                "contract_version": MVP_CONTRACT_VERSION,
                "max_stories": 3,
                "top_cap": 3,
            },
            "sealed_evidence": {
                "coverage_level": collected.coverage.value,
                "evidence_hash": _digest(
                    [item.model_payload() for item in collected.candidates]
                ),
                "candidates": [item.model_payload() for item in collected.candidates],
            },
        }
    )
    budget = RequestBudget(limit=4)
    generator = ports.provider.invoke(
        ProviderRole.GENERATOR,
        generator_request,
        mvp_generator_output_schema(),
        budget,
        contract_version=MVP_CONTRACT_VERSION,
    )
    if generator.terminal_classification is not TerminalClassification.VALIDATED:
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=generator.request_count,
            status=generator.terminal_classification.value,
            exit_code=_provider_exit(generator),
            error_code=generator.error_code,
            model_metrics=_model_metrics(generator),
            audit=collected.audit,
        )
    try:
        stories = _validate_generator_draft(
            generator.validated_structured_result, evidence, context
        )
    except ValueError as error:
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=generator.request_count,
            status="SCHEMA_INVALID",
            exit_code=4,
            error_code=str(error),
            model_metrics=_model_metrics(generator),
            audit=collected.audit,
        )
    if not stories:
        status, exit_code, error_code = _no_story_disposition(
            collected.coverage, collected.evidence_level
        )
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=generator.request_count,
            status=status,
            exit_code=exit_code,
            error_code=error_code,
            model_metrics=_model_metrics(generator),
            audit=collected.audit,
        )
    requirements, draft_hash = _review_requirements(stories)
    referenced_ids = {
        evidence_id
        for story in stories
        for claim in story["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    reviewer_items = [
        item.model_payload() for item in collected.candidates if item.evidence_id in referenced_ids
    ]
    reviewer_request = _canonical(
        {
            "approved_context": context,
            "generator_result": {
                "draft": {"stories": list(stories)},
                "structured_result_hash": draft_hash,
            },
            "rubric": {
                "allowed_decisions": ["ACCEPT", "REJECT", "ABSTAIN"],
                "contract_version": MVP_CONTRACT_VERSION,
                "requirements": requirements,
            },
            "sealed_evidence": {
                "evidence_hash": _digest(reviewer_items),
                "items": reviewer_items,
            },
        }
    )
    reviewer = ports.provider.invoke(
        ProviderRole.REVIEWER,
        reviewer_request,
        mvp_reviewer_output_schema(),
        budget,
        contract_version=MVP_CONTRACT_VERSION,
    )
    total_requests = generator.request_count + reviewer.request_count
    if reviewer.terminal_classification is not TerminalClassification.VALIDATED:
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=total_requests,
            status=reviewer.terminal_classification.value,
            exit_code=_provider_exit(reviewer),
            error_code=reviewer.error_code,
            model_metrics=_model_metrics(generator, reviewer),
            audit=collected.audit,
        )
    accepted = _accepted_stories(stories, reviewer.validated_structured_result)
    if not accepted:
        status, exit_code, error_code = _no_story_disposition(
            collected.coverage, collected.evidence_level
        )
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=collected.coverage,
            evidence_level=collected.evidence_level,
            evidence_healthy_sources=collected.evidence_healthy_sources,
            candidate_count=len(collected.candidates),
            story_count=0,
            request_count=total_requests,
            status=status,
            exit_code=exit_code,
            error_code=error_code,
            model_metrics=_model_metrics(generator, reviewer),
            audit=collected.audit,
        )
    markdown = _render_markdown(selected_date, collected.coverage, accepted, evidence)
    personalized = tuple(
        sorted(
            {
                ref
                for story in accepted
                for claim in story["claims"]
                for ref in claim["context_refs"]
            }
        )
    )
    accepted_claim_ids = sorted(
        claim["claim_id"] for story in accepted for claim in story["claims"]
    )
    selected_urls = sorted(
        {
            evidence[evidence_id].link
            for story in accepted
            for claim in story["claims"]
            for evidence_id in claim["evidence_ids"]
        }
    )
    return _finish(
        ports=ports,
        run_dir=run_dir,
        content_date=selected_date,
        coverage=collected.coverage,
        evidence_level=collected.evidence_level,
        evidence_healthy_sources=collected.evidence_healthy_sources,
        candidate_count=len(collected.candidates),
        story_count=len(accepted),
        request_count=total_requests,
        status="PUBLISHED",
        exit_code=0,
        markdown=markdown,
        model_metrics=_model_metrics(generator, reviewer),
        personalization_refs=personalized,
        audit={
            **collected.audit,
            "evidence_hash": _digest(
                [item.model_payload() for item in collected.candidates]
            ),
            "generator_result_hash": generator.structured_result_hash,
            "review_decisions_hash": reviewer.structured_result_hash,
            "accepted_claim_ids": accepted_claim_ids,
            "selected_urls": selected_urls,
        },
    )
