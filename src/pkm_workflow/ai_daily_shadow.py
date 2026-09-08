"""One-shot AI Daily shadow pipeline over the existing collection and curation flow."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from .daily_content import (
    _CANDIDATE_EVIDENCE_MAX_CHARS as _CANDIDATE_EVIDENCE_MAX_CHARS,
)
from .daily_content import (
    _GENERATOR_EVIDENCE_CHARS as _GENERATOR_EVIDENCE_CHARS,
)
from .daily_content import (
    _GENERIC_ACTION_TERMS as _GENERIC_ACTION_TERMS,
)
from .daily_content import (
    _RATE_PATTERN as _RATE_PATTERN,
)
from .daily_content import (
    _SAMPLE_SCOPE_PATTERN as _SAMPLE_SCOPE_PATTERN,
)
from .daily_content import (
    _SECTION_TERMS as _SECTION_TERMS,
)
from .daily_content import (
    ShadowResult as ShadowResult,
)
from .daily_content import (
    _approved_context_paths as _approved_context_paths,
)
from .daily_content import (
    _attach_profile_refs as _attach_profile_refs,
)
from .daily_content import (
    _canonical as _canonical,
)
from .daily_content import (
    _digest as _digest,
)
from .daily_content import (
    _evidence_segments as _evidence_segments,
)
from .daily_content import (
    _git_head as _git_head,
)
from .daily_content import (
    _has_unscoped_rate as _has_unscoped_rate,
)
from .daily_content import (
    _model_candidate_payload as _model_candidate_payload,
)
from .daily_content import (
    _model_candidate_payloads as _model_candidate_payloads,
)
from .daily_content import (
    _model_safe_summary as _model_safe_summary,
)
from .daily_content import (
    _no_story_disposition as _no_story_disposition,
)
from .daily_content import (
    _repository_root as _repository_root,
)
from .daily_content import (
    _review_requirements as _review_requirements,
)
from .daily_content import (
    _safe_evidence_segment as _safe_evidence_segment,
)
from .daily_content import (
    _section_aware_excerpt as _section_aware_excerpt,
)
from .daily_content import (
    _semantic_terms as _semantic_terms,
)
from .daily_content import (
    _validate_user_context as _validate_user_context,
)
from .deepseek_mvp_strategy import (
    MVP_PIPELINE_TIMEOUT_SECONDS,
    deepseek_mvp_strategy_binding,
)
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
    runtime_runs = Path(r"D:\personal\obsidian_workflow-runtime\scratch\runs")
    successful_by_date: dict[date, tuple[tuple[int, str], set[str]]] = {}
    if runtime_runs.is_dir():
        for report_path in runtime_runs.glob("*/run-report.json"):
            try:
                report = _read_json_object(report_path)
                report_date = date.fromisoformat(str(report.get("content_date", "")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not content_date - timedelta(days=7) <= report_date < content_date:
                continue
            if (
                report.get("schema") != "pkm.ai-daily-shadow.run.v1"
                or report.get("status") != "READY_FOR_SHADOW_LINK"
                or report.get("markdown_expected") is not True
                or not (
                    report_path.parent
                    / f"AI-Daily-{report_date.isoformat()}-shadow.md"
                ).is_file()
            ):
                continue
            audit = report.get("audit")
            if not isinstance(audit, dict):
                continue
            selected = audit.get("selected_urls")
            candidate_audit = audit.get("candidate_audit")
            if not isinstance(selected, list) or not isinstance(candidate_audit, list):
                continue
            selected_urls = {url for url in selected if isinstance(url, str)}
            final_urls = {
                str(row["canonical_url"])
                for row in candidate_audit
                if isinstance(row, dict)
                and row.get("disposition") == "FINAL_SELECTED"
                and isinstance(row.get("canonical_url"), str)
            }
            if not selected_urls or selected_urls != final_urls:
                continue
            created_at = str(report.get("created_at", ""))
            story_count = report.get("story_count")
            if type(story_count) is not int or story_count <= 0:
                continue
            selection_key = (story_count, created_at)
            previous = successful_by_date.get(report_date)
            if previous is None or selection_key > previous[0]:
                successful_by_date[report_date] = (selection_key, selected_urls)
    for _, selected_urls in successful_by_date.values():
        used_urls.update(selected_urls)
    catalog = load_approved_source_catalog()
    return collect_v75_candidates(
        content_date,
        catalog=catalog,
        user_context=load_approved_user_context_v75(),
        used_urls=used_urls,
        ports=default_collection_ports(catalog),
    )


class LunaAutomationRequired(RuntimeError):
    """Legacy live entry is retired; explicit offline ports remain testable."""


def _default_ports() -> ShadowPorts:
    raise LunaAutomationRequired("LUNA_AUTOMATION_REQUIRED")




























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


def _has_type_evidence_for_diversity(candidate: Candidate) -> bool:
    if candidate.story_type != "builder":
        return True
    normalized = re.sub(r"\s+", " ", candidate.summary).casefold()
    observed_result = re.search(
        r"\b\d[\d,.]*\s+(?:active |paying |paid )?"
        r"(?:users?|customers?|signups?|downloads?|visitors?|sales)\b|"
        r"(?:\$|€|£)\s*\d[\d,.]*\s*(?:mrr|arr|revenue|sales)?\b|"
        r"\b(?:mrr|arr|revenue|conversion|retention)\b.{0,24}\b\d[\d,.]*%?",
        normalized,
    )
    return observed_result is not None


def _diversity_evidence_id(
    candidates: tuple[Candidate, ...], *, max_metadata_gap: int
) -> str | None:
    if len(candidates) < 2:
        return None
    strongest = max(candidates, key=lambda candidate: candidate.metadata_score)
    alternatives = [
        candidate
        for candidate in candidates
        if candidate.pillar_exposure
        and candidate.story_type != strongest.story_type
        and candidate.editorial_score + max_metadata_gap >= strongest.metadata_score
        and _has_type_evidence_for_diversity(candidate)
    ]
    if not alternatives:
        return None
    return max(alternatives, key=lambda candidate: candidate.editorial_score).evidence_id


def _generator_request_bytes(
    *,
    candidate_payloads: tuple[dict[str, object], ...],
    coverage: CoverageLevel,
    context: Mapping[str, object],
    diversity_evidence_id: str | None,
    semantic_correction: str | None,
) -> bytes:
    payloads = list(candidate_payloads)
    return _canonical(
        {
            "approved_user_context": context,
            "rubric": {
                "contract_version": MVP_CONTRACT_VERSION,
                "max_stories": 5,
                "top_cap": 2,
                "diversity_evidence_id": diversity_evidence_id,
                "semantic_correction": semantic_correction,
            },
            "sealed_evidence": {
                "coverage_level": coverage.value,
                "evidence_hash": _digest(payloads),
                "candidates": payloads,
            },
        }
    )


def _diversity_retry_candidates(
    candidates: tuple[Candidate, ...],
    diversity_evidence_id: str | None,
) -> tuple[Candidate, ...]:
    strongest = max(candidates, key=lambda candidate: candidate.editorial_score)
    if diversity_evidence_id is None:
        return tuple(
            sorted(candidates, key=lambda candidate: -candidate.editorial_score)[:2]
        )
    target = next(
        candidate for candidate in candidates if candidate.evidence_id == diversity_evidence_id
    )
    if strongest.evidence_id == target.evidence_id:
        return (target,)
    retry_candidates = [strongest, target]
    remaining = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.evidence_id
            not in {strongest.evidence_id, target.evidence_id}
        ),
        key=lambda candidate: -candidate.editorial_score,
    )
    if remaining:
        retry_candidates.append(remaining[0])
    return tuple(retry_candidates)


def _reader_task_groups(candidates: Mapping[str, Candidate]) -> set[str]:
    groups = {
        "research": "RESEARCH",
        "ai_practice": "BUILD",
        "builder": "BUILD",
        "vc": "STRATEGY",
        "cognition": "STRATEGY",
    }
    return {groups[candidate.story_type] for candidate in candidates.values()}


def _semantic_correction_instruction(error_code: str) -> str:
    instructions = {
        "GENERATOR_REQUIRED_EXPOSURE_MISSING": (
            "Regenerate from the full candidate set and select stories spanning at least "
            "two reader-task groups: research, build, or strategy."
        ),
        "GENERATOR_DETAIL_DEPTH_REQUIRED": (
            "For every emitted story, create exactly two distinct evidence-backed "
            "mechanism/detail claims and list both in mechanism_claim_ids."
        ),
        "GENERATOR_PERSONAL_IMPACT_REQUIRED": (
            "For every emitted story, create one cautious editorial_inference impact "
            "claim with an exact semantic approved context_ref and list it in "
            "impact_claim_ids."
        ),
        "GENERATOR_UNSCOPED_RATE": (
            "Remove every percentage or rate whose claim does not also state an exact "
            "sample scope such as 4/5 or 4 of 5 tests; summarize the finding "
            "qualitatively instead."
        ),
        "GENERATOR_MINIMUM_ITEMS_REQUIRED": (
            "Regenerate four or five stories from the full evidence set. Preserve the "
            "evidence-rich candidate order for ranks 1-2 and use concise lower-ranked "
            "briefs for thinner evidence. Do not weaken evidence requirements or add filler. "
            "Keep the total at or below 24 claims."
        ),
    }
    return instructions[error_code]




def _candidate_disposition_audit(
    collected: CollectionResult,
    *,
    generator_evidence_ids: set[str],
    final_evidence_ids: set[str],
    failure_disposition: str | None = None,
) -> list[dict[str, object]]:
    raw_rows = collected.audit.get("candidate_audit", [])
    rows = raw_rows if isinstance(raw_rows, list) else []
    if not rows:
        rows = [
            {
                "evidence_id": candidate.evidence_id,
                "source_id": candidate.source_id,
                "title": candidate.title,
                "canonical_url": candidate.link,
                "pillars": list(candidate.pillars),
                "metadata_score": candidate.metadata_score,
                "editorial_score": candidate.editorial_score,
                "pillar_exposure": candidate.pillar_exposure,
                "access_state": candidate.access_state.value,
                "evidence_kind": candidate.evidence_kind,
                "published": candidate.published,
                "story_type": candidate.story_type,
            }
            for candidate in collected.candidates
        ]
    return [
        {
            **row,
            "generator_selected": row["evidence_id"] in generator_evidence_ids,
            "final_selected": row["evidence_id"] in final_evidence_ids,
            "disposition": (
                failure_disposition
                if failure_disposition is not None
                and (not generator_evidence_ids or row["evidence_id"] in generator_evidence_ids)
                else "FINAL_SELECTED"
                if row["evidence_id"] in final_evidence_ids
                else "REVIEW_REJECTED_OR_STORY_DROPPED"
                if row["evidence_id"] in generator_evidence_ids
                else "GENERATOR_NOT_SELECTED"
            ),
        }
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("evidence_id"), str)
    ]


def _review_decision_audit(value: object) -> list[dict[str, object]]:
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        return []
    return [
        {
            "claim_id": row["claim_id"],
            "evidence_ids": list(row["evidence_ids"]),
            "decision": row["decision"],
            "reason_code": row["reason_code"],
        }
        for row in value["decisions"]
        if isinstance(row, dict)
        and isinstance(row.get("claim_id"), str)
        and isinstance(row.get("evidence_ids"), list)
        and isinstance(row.get("decision"), str)
        and isinstance(row.get("reason_code"), str)
    ]








def _strong_personal_context_ref(value: str) -> bool:
    return value.startswith(("projects[", "prior_knowledge[", "knowledge_anchors["))


def _strip_stock_editorial_leadin(value: str) -> str:
    stripped = re.sub(
        r"^(?:该|这个)(?:案例|项目|观点|演讲|观察)"
        r"(?:展示|显示|表明|说明|反映)(?:了)?[，,:：]?\s*",
        "",
        value.strip(),
    )
    stripped = re.sub(
        r"^这(?:表明|显示|说明)(?:了)?[，,:：]?\s*",
        "",
        stripped,
    )
    stripped = re.sub(
        r"^(?:值得(?:注意|关注|观察)|需要指出)的是[，,:：]?\s*",
        "",
        stripped,
    )
    return stripped or value.strip()


def _contains_reader_directed_copy(value: str) -> bool:
    return re.search(
        r"[你您]|\byou\b|\byour(?:s|self)?\b",
        value,
        re.IGNORECASE,
    ) is not None


def _needs_builder_self_report_attribution(candidate: Candidate, statement: str) -> bool:
    if candidate.evidence_role not in {
        "BUILDER_PRIMARY",
        "PRIMARY_BUILDER",
        "PRIMARY_OR_EXPERT",
    }:
        return False
    lowered = statement.casefold()
    allegation = re.search(
        r"(?:delete|remove|censor|ban|defam|manipulat|"
        r"删除|移除|屏蔽|封禁|诽谤|操纵|造假)",
        lowered,
    )
    attributed = _has_builder_self_report_attribution(candidate, lowered)
    return allegation is not None and not attributed


def _has_builder_self_report_attribution(
    candidate: Candidate, statement: str
) -> bool:
    source = candidate.source.strip()
    names = {source, *(part for part in source.split() if len(part) >= 3)}
    names.discard("")
    if not names:
        return False
    name_pattern = "(?:" + "|".join(re.escape(name) for name in sorted(names)) + ")"
    return re.search(
        rf"(?:据\s*{name_pattern}.{{0,16}}(?:自述|称|表示|认为)|"
        rf"{name_pattern}.{{0,12}}(?:自述|声称|表示|认为|称)|"
        rf"according to\s+{name_pattern}|"
        rf"{name_pattern}\s+(?:claims?|says?|writes?))",
        statement,
        re.IGNORECASE,
    ) is not None


def _normalize_builder_attribution(candidate: Candidate, statement: str) -> str:
    source = candidate.source.strip()
    if not source:
        return statement
    last_name = source.split()[-1]
    pattern = (
        rf"^据\s+{re.escape(source)}\s+自述，\s*"
        rf"(?:{re.escape(source)}|{re.escape(last_name)})\s*"
        r"(?:称|表示|认为)[，,]?\s*"
    )
    return re.sub(pattern, f"{source} 称，", statement)


def _normalize_publisher_authorship(candidate: Candidate, statement: str) -> str:
    path_parts = [part for part in urlsplit(candidate.link).path.split("/") if part]
    if (
        candidate.source == "Hugging Face Blog"
        and len(path_parts) >= 3
        and path_parts[0].casefold() == "blog"
    ):
        contributor = path_parts[1]
        normalized = re.sub(
            r"Hugging Face\s*的(?:论文|文章)",
            f"{contributor} 在 Hugging Face Blog 发表的文章",
            statement,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"^Hugging Face(?:\s+Blog|\s*博客)?"
            r"(?:发布(?:了)?(?:一篇)?(?:论文|文章)[，,]?)?\s*提出",
            f"Hugging Face Blog 上由 {contributor} 署名的文章提出",
            normalized,
            flags=re.IGNORECASE,
        )
    return statement


def _validate_generator_draft(
    draft: object,
    evidence: Mapping[str, Candidate],
    context: Mapping[str, object],
    diversity_evidence_id: str | None = None,
    *,
    enforce_editorial_target: bool = True,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(draft, dict) or set(draft) != {"stories"}:
        raise ValueError("GENERATOR_DRAFT_INVALID")
    stories = draft.get("stories")
    if not isinstance(stories, list) or len(stories) > 5:
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
    used_story_evidence_ids: set[str] = set()
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
            or not 1 <= rank <= 5
            or rank in ranks
            or not isinstance(claims, list)
        ):
            raise ValueError("GENERATOR_STORY_ID_OR_RANK_INVALID")
        story_ids.add(story_id)
        ranks.add(rank)
        local_claims: dict[str, dict[str, Any]] = {}
        unsafe_absence_ids: set[str] = set()
        context_ref_unknown = False
        ungrounded_reader_copy = False
        protected_personal_ids = {
            *(
                story.get("impact_claim_ids", [])
                if isinstance(story.get("impact_claim_ids"), list)
                else []
            ),
            *(
                story.get("action_claim_ids", [])
                if isinstance(story.get("action_claim_ids"), list)
                else []
            ),
        }
        for claim in claims:
            if not isinstance(claim, dict):
                raise ValueError("GENERATOR_CLAIM_INVALID")
            claim = dict(claim)
            if isinstance(claim.get("statement"), str):
                claim["statement"] = _strip_stock_editorial_leadin(
                    claim["statement"]
                )
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
                context_ref_unknown = True
            if (
                claim_id not in protected_personal_ids
                and _contains_reader_directed_copy(claim["statement"])
                and not any(
                    _strong_personal_context_ref(context_ref)
                    for context_ref in context_refs
                )
            ):
                ungrounded_reader_copy = True
            statement = str(claim["statement"]).casefold()
            chinese_absence = re.search(
                r"(?:报告|文档|来源|证据).{0,10}"
                r"(?:未|没有|无|尚未|省略(?:了)?|缺少|缺失|缺乏).{0,12}"
                r"(?:披露|提及|说明|明确|提供|公布|公开|给出|细节|信息|数据|描述)",
                statement,
            ) or re.search(
                r"(?:省略(?:了)?|缺少|缺失|缺乏|尚无|不详).{0,8}"
                r"(?:细节|信息|数据|说明|描述)",
                statement,
            ) or re.search(
                r"(?:细节|信息|数据|说明|描述).{0,8}"
                r"(?:未披露|未提及|未说明|不详|缺失)",
                statement,
            ) or re.search(
                r"(?:没有|无|未包含|未提供).{0,16}(?:细节|信息|数据|说明|描述)",
                statement,
            )
            english_absence = re.search(
                r"(?:report|source|evidence|document).{0,24}"
                r"(?:does not|did not|doesn't|didn't|omits?|lacks?|missing|"
                r"undisclosed|unspecified|unreported)",
                statement,
            ) or re.search(
                r"(?:details?|information|data).{0,16}"
                r"(?:remain(?:s)? )?(?:undisclosed|unspecified|unreported|missing)",
                statement,
            ) or re.search(
                r"\bno .{0,20}(?:details?|information|data) "
                r"(?:is|are|was|were)? ?(?:included|provided|reported|available)\b",
                statement,
            )
            if (chinese_absence or english_absence) and any(
                not evidence[evidence_id].source_complete for evidence_id in evidence_ids
            ):
                unsafe_absence_ids.add(claim_id)
            action_type = claim.get("action_type")
            action_object = claim.get("action_object")
            success_criterion = claim.get("success_criterion")
            if claim_kind == "action":
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
            if _has_unscoped_rate(statement):
                raise ValueError("GENERATOR_UNSCOPED_RATE")
            claim_ids.add(claim_id)
            local_claims[claim_id] = claim
        story = dict(story)
        story["claims"] = list(local_claims.values())
        if ungrounded_reader_copy:
            continue
        if context_ref_unknown:
            invalid_story_evidence_ids = {
                evidence_id
                for claim in local_claims.values()
                for evidence_id in claim["evidence_ids"]
            }
            if diversity_evidence_id in invalid_story_evidence_ids:
                diversity_evidence_id = None
            continue
        title_claim_id = story.get("title_claim_id")
        if title_claim_id not in local_claims:
            raise ValueError("GENERATOR_TITLE_CLAIM_INVALID")
        for field in role_fields:
            ids = story.get(field)
            if not isinstance(ids, list) or any(item not in local_claims for item in ids):
                raise ValueError(f"GENERATOR_{field.upper()}_BINDING_INVALID")
        if len(story["action_claim_ids"]) > 1:
            raise ValueError("GENERATOR_PER_STORY_ACTION_LIMIT_EXCEEDED")
        weak_personal_ids = {
            claim_id
            for claim_id, claim in local_claims.items()
            if (
                claim_id in story["impact_claim_ids"]
                or claim["claim_kind"] == "action"
            )
            and not any(
                _strong_personal_context_ref(context_ref)
                for context_ref in claim["context_refs"]
            )
        }
        if weak_personal_ids:
            projected = dict(story)
            projected["claims"] = [
                claim
                for claim in story["claims"]
                if claim["claim_id"] not in weak_personal_ids
            ]
            for field in role_fields:
                projected[field] = [
                    claim_id
                    for claim_id in story[field]
                    if claim_id not in weak_personal_ids
                ]
            story = projected
            local_claims = {
                claim["claim_id"]: claim for claim in story["claims"]
            }
        fields = context.get("fields")
        is_v75 = isinstance(fields, dict) and fields.get("schema_version") == "7.5"
        is_deep_read = rank <= 2
        required_mechanisms = 2 if is_deep_read else 1
        if is_v75 and len(story["mechanism_claim_ids"]) < required_mechanisms:
            if diversity_evidence_id in {
                evidence_id
                for claim in story["claims"]
                for evidence_id in claim["evidence_ids"]
            }:
                diversity_evidence_id = None
            continue
        if any(
            local_claims[claim_id]["claim_kind"] != "editorial_inference"
            or not any(
                _strong_personal_context_ref(context_ref)
                for context_ref in local_claims[claim_id]["context_refs"]
            )
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
        story_evidence_ids = {
            evidence_id
            for claim in local_claims.values()
            for evidence_id in claim["evidence_ids"]
        }
        if len(story_evidence_ids) != 1:
            raise ValueError("GENERATOR_MULTIPLE_CANDIDATES_PER_STORY")
        story_candidate = evidence[next(iter(story_evidence_ids))]
        attributed_claims: list[dict[str, Any]] = []
        for claim in story["claims"]:
            normalized_authorship = _normalize_publisher_authorship(
                story_candidate, claim["statement"]
            )
            if normalized_authorship != claim["statement"]:
                claim = dict(claim)
                claim["statement"] = normalized_authorship
            if _needs_builder_self_report_attribution(
                story_candidate, claim["statement"]
            ):
                claim = dict(claim)
                unattributed = re.sub(
                    r"^据称[，,]?\s*", "", claim["statement"]
                )
                claim["statement"] = (
                    f"据 {story_candidate.source} 自述，{unattributed}"
                )
            normalized_statement = _normalize_builder_attribution(
                story_candidate, claim["statement"]
            )
            if normalized_statement != claim["statement"]:
                claim = dict(claim)
                claim["statement"] = normalized_statement
            attributed_claims.append(claim)
        story = dict(story)
        story["claims"] = attributed_claims
        local_claims = {claim["claim_id"]: claim for claim in attributed_claims}
        security_evidence = "\n".join(
            f"{evidence[evidence_id].title}\n{evidence[evidence_id].summary}"
            for evidence_id in story_evidence_ids
        )
        security_story = re.search(
            r"\b(?:vulnerabilit(?:y|ies)|exploit|malware|path traversal|"
            r"prompt injection|cve-\d+)\b|漏洞|恶意代码|路径遍历|提示注入",
            security_evidence,
            re.IGNORECASE,
        ) is not None
        if security_story:
            removed_security_actions = set(story["action_claim_ids"])
            if removed_security_actions:
                projected = dict(story)
                projected["action_claim_ids"] = []
                projected["claims"] = [
                    claim
                    for claim in story["claims"]
                    if claim["claim_id"] not in removed_security_actions
                ]
                story = projected
        story_type = evidence[next(iter(story_evidence_ids))].story_type
        if not is_deep_read and story["action_claim_ids"]:
            projected = dict(story)
            removed_radar_actions = set(story["action_claim_ids"])
            projected["action_claim_ids"] = []
            projected["claims"] = [
                claim
                for claim in story["claims"]
                if claim["claim_id"] not in removed_radar_actions
            ]
            story = projected
        if (
            is_deep_read
            and story_type in {"builder", "cognition"}
            and story["action_claim_ids"]
        ):
            action_text = " ".join(
                " ".join(
                    (
                        str(local_claims[claim_id]["statement"]),
                        str(local_claims[claim_id]["action_object"]),
                        str(local_claims[claim_id]["success_criterion"]),
                    )
                )
                for claim_id in story["action_claim_ids"]
            )
            takeaway_text = " ".join(
                str(local_claims[claim_id]["statement"])
                for field in ("importance_claim_ids", "impact_claim_ids")
                for claim_id in story[field]
            )
            if not (_semantic_terms(action_text) & _semantic_terms(takeaway_text)):
                if diversity_evidence_id in story_evidence_ids:
                    diversity_evidence_id = None
                continue
        if not story["event_claim_ids"] or not story["importance_claim_ids"]:
            raise ValueError("GENERATOR_REQUIRED_CLAIMS_MISSING")
        core_claim_ids = {
            story["title_claim_id"],
            *story["event_claim_ids"],
            *story["importance_claim_ids"],
            *story["impact_claim_ids"],
        }
        if unsafe_absence_ids & core_claim_ids:
            if diversity_evidence_id in story_evidence_ids:
                diversity_evidence_id = None
            continue
        if unsafe_absence_ids:
            projected = dict(story)
            projected["claims"] = [
                claim
                for claim in story["claims"]
                if claim["claim_id"] not in unsafe_absence_ids
            ]
            for field in role_fields:
                projected[field] = [
                    claim_id
                    for claim_id in story[field]
                    if claim_id not in unsafe_absence_ids
                ]
            story = projected
        financial_scale_pattern = re.compile(
            r"(?:revenue|valuation|\barr\b|年化收入|营收|估值|公司规模)",
            re.IGNORECASE,
        )
        causal_trend_pattern = re.compile(
            r"(?:validat|prov|reflect|support.{0,8}trend|验证|证明|反映|"
            r"支持.{0,8}趋势|表明.{0,8}趋势)",
            re.IGNORECASE,
        )
        if any(
            financial_scale_pattern.search(local_claims[claim_id]["statement"])
            and causal_trend_pattern.search(local_claims[claim_id]["statement"])
            for claim_id in story["importance_claim_ids"]
        ):
            continue
        story_evidence_ids = {
            evidence_id
            for claim in story["claims"]
            for evidence_id in claim["evidence_ids"]
        }
        if story_evidence_ids & used_story_evidence_ids:
            raise ValueError("GENERATOR_EVIDENCE_REUSED_ACROSS_STORIES")
        used_story_evidence_ids.update(story_evidence_ids)
        validated.append(story)
    action_bounded: list[dict[str, Any]] = []
    remaining_actions = 1
    for story in sorted(validated, key=lambda item: item["rank"]):
        keep_action_ids = story["action_claim_ids"][:remaining_actions]
        remaining_actions -= len(keep_action_ids)
        removed_action_ids = set(story["action_claim_ids"]) - set(keep_action_ids)
        projected = dict(story)
        projected["action_claim_ids"] = keep_action_ids
        projected["claims"] = [
            claim
            for claim in story["claims"]
            if claim["claim_id"] not in removed_action_ids
        ]
        action_bounded.append(projected)

    if (
        enforce_editorial_target
        and len(evidence) >= 5
        and len(action_bounded) < 4
    ):
        raise ValueError("GENERATOR_MINIMUM_ITEMS_REQUIRED")

    total_claims = sum(len(story["claims"]) for story in action_bounded)
    if total_claims <= 24:
        return tuple(action_bounded)
    claim_bounded = [dict(story) for story in action_bounded]
    for story in reversed(claim_bounded):
        if total_claims <= 24:
            break
        optional_ids = [
            *story["uncertainty_claim_ids"],
            *story["watch_claim_ids"],
        ]
        remove_count = min(len(optional_ids), total_claims - 24)
        removed_ids = set(optional_ids[-remove_count:]) if remove_count else set()
        story["uncertainty_claim_ids"] = [
            claim_id
            for claim_id in story["uncertainty_claim_ids"]
            if claim_id not in removed_ids
        ]
        story["watch_claim_ids"] = [
            claim_id for claim_id in story["watch_claim_ids"] if claim_id not in removed_ids
        ]
        story["claims"] = [
            claim for claim in story["claims"] if claim["claim_id"] not in removed_ids
        ]
        total_claims -= len(removed_ids)
    if total_claims > 24:
        raise ValueError("GENERATOR_TOTAL_CLAIM_LIMIT_EXCEEDED")
    return tuple(claim_bounded)




def _accepted_stories(
    stories: tuple[dict[str, Any], ...],
    decisions: object,
    evidence: Mapping[str, Candidate],
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
        material_claim_ids = {
            story["title_claim_id"],
            *story["event_claim_ids"],
            *story["mechanism_claim_ids"],
            *story["importance_claim_ids"],
            *story["impact_claim_ids"],
        }
        if any(
            verdicts.get(claim_id) != "ACCEPT" for claim_id in material_claim_ids
        ):
            continue
        kept_claims = [
            claim
            for claim in story["claims"]
            if verdicts.get(claim["claim_id"]) == "ACCEPT"
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
        required_mechanisms = 2 if story["rank"] <= 2 else 1
        if len(projected["mechanism_claim_ids"]) < required_mechanisms:
            continue
        story_evidence_ids = {
            evidence_id
            for claim in kept_claims
            for evidence_id in claim["evidence_ids"]
        }
        if len(story_evidence_ids) != 1:
            continue
        accepted.append(projected)
    accepted.sort(key=lambda story: story["rank"])
    return tuple(accepted)


def _claim_texts(story: Mapping[str, Any], field: str) -> list[str]:
    claims = {claim["claim_id"]: claim for claim in story["claims"]}
    rendered: list[str] = []
    for claim_id in story[field]:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        rendered.append(claim["statement"])
    return rendered


def _brief_sentences(texts: list[str], *, skip: str | None = None) -> str:
    skip_key = re.sub(r"[\W_]+", "", skip or "").casefold()
    seen: set[str] = set()
    sentences: list[str] = []
    for value in texts:
        text = value.strip()
        key = re.sub(r"[\W_]+", "", text).casefold()
        if not text or key == skip_key or key in seen:
            continue
        seen.add(key)
        if text[-1] not in "。！？.!?":
            text += "。"
        sentences.append(text)
    return " ".join(sentences)


def _render_markdown(
    content_date: date,
    coverage: CoverageLevel,
    evidence_level: CoverageLevel,
    stories: tuple[dict[str, Any], ...],
    evidence: Mapping[str, Candidate],
) -> str:
    referenced_evidence_ids = {
        evidence_id
        for story in stories
        for claim in story["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    evidence_dates = sorted(
        published
        for evidence_id in referenced_evidence_ids
        if (published := evidence[evidence_id].published[:10])
    )
    evidence_start = evidence_dates[0] if evidence_dates else content_date.isoformat()
    evidence_end = evidence_dates[-1] if evidence_dates else content_date.isoformat()
    lines = [
        "---",
        f"date: {content_date.isoformat()}",
        "type: ai-daily-shadow",
        f"operational_coverage: {coverage.value}",
        f"evidence_availability: {evidence_level.value}",
        f"evidence_date_start: {evidence_start}",
        f"evidence_date_end: {evidence_end}",
        "production: false",
        "---",
        "",
        f"# AI Daily · {content_date.isoformat()}",
    ]
    layer_labels = {
        "research": "研究",
        "ai_practice": "AI 实践",
        "builder": "Builder",
        "vc": "创投",
        "cognition": "认知",
    }
    accepted_actions: list[str] = []
    for display_index, story in enumerate(stories, start=1):
        claims = {claim["claim_id"]: claim for claim in story["claims"]}
        title = claims[story["title_claim_id"]]["statement"]
        story_evidence_ids = {
            evidence_id
            for claim in story["claims"]
            for evidence_id in claim["evidence_ids"]
        }
        story_types = {evidence[evidence_id].story_type for evidence_id in story_evidence_ids}
        story_type = next(iter(story_types)) if len(story_types) == 1 else "ai_practice"
        layer_label = layer_labels[story_type]
        if story_type == "cognition" and any(
            "VC" in evidence[evidence_id].pillars
            for evidence_id in story_evidence_ids
        ):
            layer_label += " × 创投"
        lines.extend(("", f"### {display_index}｜{layer_label}｜{title}", ""))
        context = _brief_sentences(
            _claim_texts(story, "event_claim_ids")
            + _claim_texts(story, "mechanism_claim_ids"),
            skip=title,
        )
        if context:
            lines.extend((context, ""))
        editorial_judgment = _brief_sentences(
            _claim_texts(story, "importance_claim_ids")
            + _claim_texts(story, "uncertainty_claim_ids")
            + _claim_texts(story, "watch_claim_ids")
            + _claim_texts(story, "impact_claim_ids")
        )
        if editorial_judgment:
            if story["rank"] <= 2:
                lines.extend(("**简报判断**", editorial_judgment, ""))
            else:
                lines.extend((editorial_judgment, ""))
        action = _brief_sentences(_claim_texts(story, "action_claim_ids"))
        if action:
            accepted_actions.append(action)
        evidence_ids = []
        for claim in story["claims"]:
            for evidence_id in claim["evidence_ids"]:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        links = []
        for evidence_id in evidence_ids:
            item = evidence[evidence_id]
            links.append(f"[{item.source}]({item.link}) · {item.published}")
        source_label = (
            "引用页"
            if any(
                re.match(
                    r"^(?:quoting\b|a quote from\b)",
                    evidence[evidence_id].title,
                    re.IGNORECASE,
                )
                for evidence_id in evidence_ids
            )
            else "来源页"
            if any(
                re.search(
                    r"\b(?:this is a link post by|link blog)\b",
                    evidence[evidence_id].summary,
                    re.IGNORECASE,
                )
                for evidence_id in evidence_ids
            )
            else "原文"
        )
        lines.append(f"**{source_label}** {'；'.join(links)}")
    if accepted_actions:
        lines.extend(("", "## 今天可以做", ""))
        lines.extend(f"- {action}" for action in accepted_actions[:1])
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
    run_dir = _new_run_dir(ports.runtime_root)
    try:
        collected = ports.collect(selected_date)
    except (ValueError, OSError, TimeoutError) as error:
        raw_code = str(error)
        error_code = (
            raw_code
            if re.fullmatch(r"[A-Z][A-Z0-9_:.-]{2,80}", raw_code)
            else "COLLECTION_FAILED"
        )
        return _finish(
            ports=ports,
            run_dir=run_dir,
            content_date=selected_date,
            coverage=CoverageLevel.INSUFFICIENT,
            evidence_level=CoverageLevel.INSUFFICIENT,
            candidate_count=0,
            story_count=0,
            request_count=0,
            status="COLLECTION_FAILED",
            exit_code=4,
            error_code=error_code,
        )
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
        no_story_status, no_story_exit, no_story_error = _no_story_disposition(
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
            status=no_story_status,
            exit_code=no_story_exit,
            error_code=no_story_error,
            audit=collected.audit,
        )
    if len(collected.candidates) > 10:
        raise ValueError("curation must provide no more than ten candidates")
    context = _validate_user_context(ports.load_user_context())
    collected = replace(
        collected,
        candidates=_attach_profile_refs(collected.candidates, context),
    )
    evidence = {item.evidence_id: item for item in collected.candidates}
    if len(evidence) != len(collected.candidates):
        raise ValueError("candidate evidence IDs must be unique")
    candidate_payloads = _model_candidate_payloads(collected.candidates, context)
    payload_by_evidence_id = {
        str(payload["evidence_id"]): payload for payload in candidate_payloads
    }
    diversity_evidence_id = None
    generator_request = _generator_request_bytes(
        candidate_payloads=candidate_payloads,
        coverage=collected.coverage,
        context=context,
        diversity_evidence_id=diversity_evidence_id,
        semantic_correction=None,
    )
    budget = RequestBudget(limit=4)
    model_pipeline_started = monotonic()

    def remaining_model_timeout() -> float:
        remaining = MVP_PIPELINE_TIMEOUT_SECONDS - (
            monotonic() - model_pipeline_started
        )
        return max(0.001, remaining)

    generator = ports.provider.invoke(
        ProviderRole.GENERATOR,
        generator_request,
        mvp_generator_output_schema(),
        budget,
        contract_version=MVP_CONTRACT_VERSION,
        timeout_seconds=remaining_model_timeout(),
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
            audit={
                **collected.audit,
                "candidate_audit": _candidate_disposition_audit(
                    collected,
                    generator_evidence_ids=set(),
                    final_evidence_ids=set(),
                    failure_disposition="GENERATOR_FAILED",
                ),
            },
        )
    generator_results = [generator]
    try:
        stories = _validate_generator_draft(
            generator.validated_structured_result,
            evidence,
            context,
            diversity_evidence_id=diversity_evidence_id,
        )
    except ValueError as error:
        validation_error: ValueError | None = error
        retryable_semantic_errors = {"GENERATOR_MINIMUM_ITEMS_REQUIRED"}
        if str(error) in retryable_semantic_errors and budget.remaining >= 2:
            retry_candidates = collected.candidates
            retry_evidence = {
                candidate.evidence_id: candidate for candidate in retry_candidates
            }
            retry_request = _generator_request_bytes(
                candidate_payloads=candidate_payloads,
                coverage=collected.coverage,
                context=context,
                diversity_evidence_id=diversity_evidence_id,
                semantic_correction=_semantic_correction_instruction(str(error)),
            )
            retry_generator = ports.provider.invoke(
                ProviderRole.GENERATOR,
                retry_request,
                mvp_generator_output_schema(),
                budget,
                contract_version=MVP_CONTRACT_VERSION,
                allow_schema_repair=False,
                timeout_seconds=remaining_model_timeout(),
            )
            generator_results.append(retry_generator)
            if (
                retry_generator.terminal_classification
                is not TerminalClassification.VALIDATED
            ):
                return _finish(
                    ports=ports,
                    run_dir=run_dir,
                    content_date=selected_date,
                    coverage=collected.coverage,
                    evidence_level=collected.evidence_level,
                    evidence_healthy_sources=collected.evidence_healthy_sources,
                    candidate_count=len(collected.candidates),
                    story_count=0,
                    request_count=sum(result.request_count for result in generator_results),
                    status=retry_generator.terminal_classification.value,
                    exit_code=_provider_exit(retry_generator),
                    error_code=retry_generator.error_code,
                    model_metrics=_model_metrics(*generator_results),
                    audit={
                        **collected.audit,
                        "candidate_audit": _candidate_disposition_audit(
                            collected,
                            generator_evidence_ids=set(),
                            final_evidence_ids=set(),
                            failure_disposition="GENERATOR_FAILED",
                        ),
                    },
                )
            generator = retry_generator
            try:
                stories = _validate_generator_draft(
                    generator.validated_structured_result,
                    retry_evidence,
                    context,
                    diversity_evidence_id=diversity_evidence_id,
                    enforce_editorial_target=False,
                )
            except ValueError as retry_error:
                validation_error = retry_error
            else:
                validation_error = None
        if validation_error is None:
            pass
        else:
            return _finish(
                ports=ports,
                run_dir=run_dir,
                content_date=selected_date,
                coverage=collected.coverage,
                evidence_level=collected.evidence_level,
                evidence_healthy_sources=collected.evidence_healthy_sources,
                candidate_count=len(collected.candidates),
                story_count=0,
                request_count=sum(result.request_count for result in generator_results),
                status="SCHEMA_INVALID",
                exit_code=4,
                error_code=str(validation_error),
                model_metrics=_model_metrics(*generator_results),
                audit={
                    **collected.audit,
                    "candidate_audit": _candidate_disposition_audit(
                        collected,
                        generator_evidence_ids=set(),
                        final_evidence_ids=set(),
                        failure_disposition="GENERATOR_SCHEMA_REJECTED",
                    ),
                },
            )
    generator_request_count = sum(result.request_count for result in generator_results)
    if not stories:
        no_story_status, no_story_exit, no_story_error = _no_story_disposition(
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
            request_count=generator_request_count,
            status=no_story_status,
            exit_code=no_story_exit,
            error_code=no_story_error,
            model_metrics=_model_metrics(*generator_results),
            audit={
                **collected.audit,
                "candidate_audit": _candidate_disposition_audit(
                    collected,
                    generator_evidence_ids=set(),
                    final_evidence_ids=set(),
                ),
            },
        )
    requirements, draft_hash = _review_requirements(stories)
    referenced_ids = {
        evidence_id
        for story in stories
        for claim in story["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    reviewer_items = [
        payload_by_evidence_id[item.evidence_id]
        for item in collected.candidates
        if item.evidence_id in referenced_ids
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
        timeout_seconds=remaining_model_timeout(),
    )
    total_requests = generator_request_count + reviewer.request_count
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
            model_metrics=_model_metrics(*generator_results, reviewer),
            audit={
                **collected.audit,
                "candidate_audit": _candidate_disposition_audit(
                    collected,
                    generator_evidence_ids=referenced_ids,
                    final_evidence_ids=set(),
                    failure_disposition="REVIEW_FAILED",
                ),
                "review_decisions": [],
            },
        )
    accepted = _accepted_stories(stories, reviewer.validated_structured_result, evidence)
    if not accepted:
        generator_evidence_ids = {
            evidence_id
            for story in stories
            for claim in story["claims"]
            for evidence_id in claim["evidence_ids"]
        }
        no_story_status, no_story_exit, no_story_error = _no_story_disposition(
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
            status=no_story_status,
            exit_code=no_story_exit,
            error_code=no_story_error,
            model_metrics=_model_metrics(*generator_results, reviewer),
            audit={
                **collected.audit,
                "candidate_audit": _candidate_disposition_audit(
                    collected,
                    generator_evidence_ids=generator_evidence_ids,
                    final_evidence_ids=set(),
                ),
                "review_decisions": _review_decision_audit(
                    reviewer.validated_structured_result
                ),
            },
        )
    markdown = _render_markdown(
        selected_date,
        collected.coverage,
        collected.evidence_level,
        accepted,
        evidence,
    )
    personalized = tuple(
        sorted(
            {
                ref
                for story in accepted
                for claim in story["claims"]
                for ref in claim["context_refs"]
                if _strong_personal_context_ref(ref)
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
    generator_evidence_ids = {
        evidence_id
        for story in stories
        for claim in story["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    final_evidence_ids = {
        evidence_id
        for story in accepted
        for claim in story["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    final_candidate_audit = _candidate_disposition_audit(
        collected,
        generator_evidence_ids=generator_evidence_ids,
        final_evidence_ids=final_evidence_ids,
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
        model_metrics=_model_metrics(*generator_results, reviewer),
        personalization_refs=personalized,
        audit={
            **collected.audit,
            "candidate_audit": final_candidate_audit,
            "review_decisions": _review_decision_audit(
                reviewer.validated_structured_result
            ),
            "evidence_hash": _digest(
                list(candidate_payloads)
            ),
            "generator_result_hash": generator.structured_result_hash,
            "review_decisions_hash": reviewer.structured_result_hash,
            "accepted_claim_ids": accepted_claim_ids,
            "selected_urls": selected_urls,
        },
    )
