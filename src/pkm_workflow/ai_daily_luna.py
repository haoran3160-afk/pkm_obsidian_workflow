"""Three small file handoffs for Luna inside a Codex scheduled task.

Python never invokes a model here. It prepares evidence, validates the two
role outputs, and reuses the existing renderer and create-new publisher.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import ai_daily_production as publishing
from . import daily_brief as brief
from . import daily_content as editorial
from .daily_brief import (
    GENERATOR_PROMPT,
    REVIEWER_PROMPT,
    _strict_json_loads,
)
from .daily_brief import (
    contains_forbidden_text as _contains_forbidden_output_text,
)
from .daily_brief import (
    generator_schema as mvp_generator_output_schema,
)
from .daily_brief import (
    reviewer_schema as mvp_reviewer_output_schema,
)
from .module_collection import collect_modules
from .source_catalog import load_approved_source_catalog
from .user_context_v75 import load_approved_user_context_v75
from .v75_collection import (
    AccessState,
    Candidate,
    CollectionResult,
    CoverageLevel,
    default_collection_ports,
)

MODEL = "gpt-5.6-luna"
CONTRACT = "pkm.ai-daily-luna.v2"
RUNTIME = Path(r"D:\personal\obsidian_workflow-runtime")
SHANGHAI = timezone(timedelta(hours=8))
STRATEGY_HASH = editorial._digest({
    "selection_policy": "six_complete_modules_public_evidence_v1",
    "paper_evidence": "primary_paper_complete_sentences_v1",
    "contract": CONTRACT, "model": MODEL, "reasoning": "medium",
    "generator_prompt": GENERATOR_PROMPT, "reviewer_prompt": REVIEWER_PROMPT,
    "generator_schema": json.loads(mvp_generator_output_schema()),
    "reviewer_schema": json.loads(mvp_reviewer_output_schema()),
})


class StageError(ValueError):
    def __init__(self, code: str, *, repairable: bool = False):
        super().__init__(code)
        self.repairable = repairable


def _read(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 4_000_000:
        raise StageError("ARTIFACT_TOO_LARGE")
    value = _strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise StageError("JSON_OBJECT_REQUIRED")
    return value


def _write(path: Path, value: object) -> None:
    publishing._write_new(path, editorial._canonical(value) + b"\n")


def _file_hash(path: Path) -> str:
    return publishing._file_facts(path).sha256


def _sealed_read(path: Path) -> dict[str, Any]:
    value = _read(path)
    claimed = value.pop("payload_hash", None)
    if claimed != editorial._digest(value):
        raise StageError("ARTIFACT_BINDING_MISMATCH")
    return value


def _sealed_write(path: Path, value: dict[str, Any]) -> None:
    _write(path, value | {"payload_hash": editorial._digest(value)})


def _result(status: str, exit_code: int = 0, **kwargs: Any) -> dict[str, Any]:
    return {
        "engine": CONTRACT, "status": status, "exit_code": exit_code,
        "vault_write": False, "api_request_count": 0, **kwargs,
    }


def _published_urls(runtime: Path, vault: Path, day: date) -> set[str]:
    """Only verified published reports count as daily reading history."""
    urls: set[str] = set()
    receipt_root = runtime / "durable" / "receipts" / "ai-daily"
    for receipt in sorted(receipt_root.glob("*.published.json")):
        try:
            old_day = date.fromisoformat(receipt.name.removesuffix(".published.json"))
        except ValueError:
            continue
        if old_day >= day:
            continue
        prepared_path, published_path = publishing._receipt_paths(runtime, old_day)
        if not published_path.is_file():
            continue
        destination = vault / f"AI-Daily-{old_day}.md"
        try:
            prepared = publishing._load_prepared(prepared_path, runtime, old_day, destination)
            publishing._validate_published(published_path, prepared, prepared_path, destination)
            report = _read(prepared.shadow_report)
        except (OSError, ValueError):
            continue
        urls.update(report.get("audit", {}).get("selected_urls", []))
    return urls


def _collect(day: date, runtime: Path, vault: Path) -> CollectionResult:
    catalog = load_approved_source_catalog()
    return collect_modules(
        day, catalog=catalog, user_context=load_approved_user_context_v75(),
        used_urls=_published_urls(runtime, vault, day),
        ports=default_collection_ports(catalog),
    )


def _prepare(runtime, vault, day, mode, collect, context_loader):
    if mode == "production":
        prior = publishing._reconcile(runtime, day, vault / f"AI-Daily-{day}.md")
        if prior is not None:
            return _result(prior.status, prior.exit_code) | prior.payload()
    claim_path = runtime / "locks" / f"ai-daily-{STRATEGY_HASH[-12:]}-{day}.json"
    if claim_path.exists():
        return _result("PRODUCTION_BUSY", 4, **_read(claim_path))
    run_id = uuid4().hex
    run = runtime / "scratch" / "runs" / run_id
    run.mkdir(parents=True)
    _write(claim_path, {"run_id": run_id, "content_date": str(day)})
    try:
        collected = collect(day) if collect else _collect(day, runtime, vault)
    except (OSError, ValueError, TimeoutError):
        failure = _result(
            "COLLECTION_FAILED", 4, error_code="COLLECTION_FAILED", run_id=run_id,
            report_path=str(run / "collection-error.json"),
        )
        _write(run / "collection-error.json", failure)
        return failure
    context = editorial._validate_user_context(context_loader())
    candidates = editorial._attach_profile_refs(collected.candidates, context)
    packet = editorial._model_candidate_payloads(candidates, context)
    generator_input = {"approved_user_context": context, "candidates": list(packet)}
    _write(run / "generator-input.json", generator_input)
    publishing._write_new(run / "generator-instructions.txt", GENERATOR_PROMPT.encode("utf-8"))
    publishing._write_new(run / "generator-schema.json", mvp_generator_output_schema())
    state = {
        "run_id": run_id, "content_date": str(day), "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "contract": CONTRACT, "strategy_hash": STRATEGY_HASH,
        "coverage": collected.coverage.value, "evidence_level": collected.evidence_level.value,
        "evidence_healthy_sources": collected.evidence_healthy_sources,
        "candidates": [asdict(candidate) for candidate in candidates],
        "audit": dict(collected.audit), "context": context,
        "generator_input_hash": _file_hash(run / "generator-input.json"),
        "instructions_hash": _file_hash(run / "generator-instructions.txt"),
        "schema_hash": _file_hash(run / "generator-schema.json"),
    }
    _sealed_write(run / "state.json", state)
    if collected.coverage is CoverageLevel.INSUFFICIENT:
        return _report(run, state, (), {}, "COVERAGE_INSUFFICIENT", 4)
    if collected.evidence_level is CoverageLevel.INSUFFICIENT:
        return _report(run, state, (), {}, "EVIDENCE_INSUFFICIENT", 4)
    missing = set(brief.SECTIONS) - {candidate.story_type for candidate in candidates}
    if missing:
        return _report(run, state, (), {}, "MODULE_SOURCES_INCOMPLETE", 4)
    return _generator_ready(run, state)


def _generator_ready(run, state):
    return _result(
        "GENERATOR_READY", run_id=state["run_id"], content_date=state["content_date"],
        generator_input_path=str(run / "generator-input.json"),
        instructions_path=str(run / "generator-instructions.txt"),
        schema_path=str(run / "generator-schema.json"),
        draft_path=str(_output_path(run, "generator")),
        candidate_count=len(state["candidates"]),
    )


def _load(runtime: Path, run_id: str, day: date):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise StageError("RUN_ID_INVALID")
    run = runtime / "scratch" / "runs" / run_id
    if not run.resolve(strict=True).is_relative_to(runtime.resolve(strict=True)):
        raise StageError("RUNTIME_PATH_ESCAPE")
    state = _sealed_read(run / "state.json")
    if state["run_id"] != run_id or state["contract"] != CONTRACT:
        raise StageError("RUN_BINDING_MISMATCH")
    if state["content_date"] != day.isoformat():
        raise StageError("RUN_DATE_EXPIRED")
    if state["strategy_hash"] != STRATEGY_HASH:
        raise StageError("STRATEGY_CHANGED")
    for file, key in (
        ("generator-input.json", "generator_input_hash"),
        ("generator-instructions.txt", "instructions_hash"),
        ("generator-schema.json", "schema_hash"),
    ):
        if _file_hash(run / file) != state[key]:
            raise StageError("INPUT_CHANGED")
    claim = _read(runtime / "locks" / f"ai-daily-{STRATEGY_HASH[-12:]}-{day}.json")
    if claim["run_id"] != run_id:
        raise StageError("RUN_OWNERSHIP_MISMATCH")
    return run, state


def _role_output(path: Path, role: str):
    envelope = _read(path)
    expected = {"model", "session_id", "draft"} if role == "generator" else {
        "model", "session_id", "review_request_hash", "decisions",
    }
    if set(envelope) != expected:
        raise StageError("ROLE_ENVELOPE_INVALID")
    if envelope["model"] != MODEL:
        raise StageError("LUNA_MODEL_REQUIRED")
    if not isinstance(envelope["session_id"], str) or not re.fullmatch(
        r"(?:[0-9a-fA-F-]{36}|/root/[a-z0-9_/]+)", envelope["session_id"]
    ):
        raise StageError("ROLE_SESSION_ID_REQUIRED", repairable=True)
    structured = envelope["draft"] if role == "generator" else {"decisions": envelope["decisions"]}
    schema = mvp_generator_output_schema() if role == "generator" else mvp_reviewer_output_schema()
    if _contains_forbidden_output_text(structured):
        raise StageError("FORBIDDEN_OUTPUT_TEXT")
    # Model-independent validation; no provider credentials or transport.
    if not brief.matches_schema(structured, json.loads(schema)):
        unknown = brief.contains_unknown_fields(structured, json.loads(schema))
        raise StageError("OUTPUT_SCHEMA_INVALID", repairable=not unknown)
    return envelope, structured


def _candidates(state):
    result = []
    for row in state["candidates"]:
        fields = dict(row)
        fields["access_state"] = AccessState(fields["access_state"])
        fields["pillars"] = tuple(fields["pillars"])
        fields["profile_refs"] = tuple(fields["profile_refs"])
        result.append(Candidate(**fields))
    return {candidate.evidence_id: candidate for candidate in result}


def _output_path(run: Path, role: str) -> Path:
    repair = run / "repair.json"
    if repair.exists() and _read(repair)["role"] == role:
        return run / f"{role}-repair.json"
    return run / "draft.json" if role == "generator" else _review_file(run, "review", ".json")


def _review_file(run, stem, extension=".json"):
    repair = run / "repair.json"
    revision = repair.exists() and _read(repair).get("error_code") == "EDITORIAL_REVISION_REQUIRED"
    return run / f"{stem}{'-revision' if revision else ''}{extension}"


def _review(run, state):
    draft_path = _output_path(run, "generator")
    envelope, draft = _role_output(draft_path, "generator")
    try:
        stories = brief.validate_draft(
            draft, _candidates(state), state["context"], enforce_editorial_target=False,
        )
    except ValueError as error:
        raise StageError(
            str(error), repairable=str(error) == "GENERATOR_TOTAL_CLAIM_LIMIT_EXCEEDED",
        ) from error
    if not stories:
        status, code, _ = editorial._no_story_disposition(
            CoverageLevel(state["coverage"]), CoverageLevel(state["evidence_level"])
        )
        return _report(run, state, (), {}, status, code, generator=envelope)
    review_input = _review_file(run, "review-input")
    if _review_file(run, "review-state").exists():
        frozen = _sealed_read(_review_file(run, "review-state"))
        if _file_hash(draft_path) != frozen["draft_file_hash"]:
            raise StageError("DRAFT_CHANGED_AFTER_REVIEW")
        return _reviewer_ready(run, state, frozen)
    requirements, draft_hash = editorial._review_requirements(stories)
    referenced = {eid for story in stories for claim in story["claims"] for eid in claim["evidence_ids"]}
    model_packet = _read(run / "generator-input.json")["candidates"]
    review_packet = {
        "approved_context": state["context"],
        "generator_result": {"draft": {"stories": list(stories)}, "structured_result_hash": draft_hash},
        "rubric": {"requirements": requirements},
        "sealed_evidence": {"items": [row for row in model_packet if row["evidence_id"] in referenced]},
    }
    review_hash = editorial._digest(review_packet)
    publishing._write_or_verify(
        review_input, editorial._canonical(review_packet | {"review_request_hash": review_hash}) + b"\n",
    )
    publishing._write_or_verify(_review_file(run, "reviewer-instructions", ".txt"), REVIEWER_PROMPT.encode("utf-8"))
    publishing._write_or_verify(_review_file(run, "reviewer-schema"), mvp_reviewer_output_schema())
    frozen = {
        "draft_file_hash": _file_hash(draft_path), "draft_filename": draft_path.name,
        "generator_session_id": envelope["session_id"],
        "review_request_hash": review_hash,
        "review_input_hash": _file_hash(review_input),
    }
    _sealed_write(_review_file(run, "review-state"), frozen)
    return _reviewer_ready(run, state, frozen)


def _reviewer_ready(run, state, frozen):
    return _result(
        "REVIEWER_READY", run_id=state["run_id"],
        review_input_path=str(_review_file(run, "review-input")),
        instructions_path=str(_review_file(run, "reviewer-instructions", ".txt")),
        schema_path=str(_review_file(run, "reviewer-schema")),
        review_path=str(_output_path(run, "reviewer")),
        review_request_hash=frozen["review_request_hash"],
    )


def _check_review(run, state):
    frozen = _sealed_read(_review_file(run, "review-state"))
    if (
        _file_hash(run / frozen["draft_filename"]) != frozen["draft_file_hash"]
        or _file_hash(_review_file(run, "review-input")) != frozen["review_input_hash"]
    ):
        raise StageError("DRAFT_OR_REVIEW_INPUT_CHANGED")
    envelope, structured = _role_output(_output_path(run, "reviewer"), "reviewer")
    if envelope["session_id"] == frozen["generator_session_id"]:
        raise StageError("INDEPENDENT_REVIEWER_REQUIRED")
    if _review_file(run, "review") != run / "review.json" and (run / "review.json").exists():
        if envelope["session_id"] == _read(run / "review.json")["session_id"]:
            raise StageError("FRESH_REVISION_REVIEWER_REQUIRED")
    if envelope["review_request_hash"] != frozen["review_request_hash"]:
        raise StageError("REVIEW_REQUEST_MISMATCH")
    packet = _read(_review_file(run, "review-input"))
    requirements = {row["claim_id"]: row for row in packet["rubric"]["requirements"]}
    decisions = structured["decisions"]
    ids = [row["claim_id"] for row in decisions]
    if len(ids) != len(set(ids)) or set(ids) != set(requirements):
        raise StageError("REVIEW_DECISION_MISMATCH")
    reasons = {
        "ACCEPT": "SUPPORTED_BY_SEALED_EVIDENCE",
        "REJECT": "CONTRADICTED_BY_SEALED_EVIDENCE",
        "ABSTAIN": "INSUFFICIENT_EVIDENCE",
    }
    for row in decisions:
        requirement = requirements[row["claim_id"]]
        if (
            row["evidence_ids"] != requirement["evidence_ids"]
            or row["review_requirement_hash"] != requirement["review_requirement_hash"]
        ):
            raise StageError("REVIEW_DECISION_MISMATCH")
        if row["reason_code"] != reasons[row["decision"]]:
            raise StageError("REVIEW_REASON_MISMATCH", repairable=True)
    stories = tuple(packet["generator_result"]["draft"]["stories"])
    accepted = brief.accepted_stories(stories, structured, _candidates(state))
    return accepted, structured, frozen, envelope


def _report(run, state, accepted, decisions, status, code, *, generator=None, reviewer=None):
    evidence = _candidates(state)
    selected = sorted({evidence[eid].link for story in accepted for claim in story["claims"]
                       for eid in claim["evidence_ids"]})
    markdown_path = None
    markdown = None
    if status == "PUBLISHED" and accepted:
        markdown = brief.render(
            date.fromisoformat(state["content_date"]), CoverageLevel(state["coverage"]),
            CoverageLevel(state["evidence_level"]), accepted, evidence,
        )
        markdown_path = run / f"AI-Daily-{state['content_date']}-shadow.md"
    repair = run / "repair.json"
    raw_draft = _output_path(run, "generator")
    generated_ids = set()
    if raw_draft.exists():
        generated_ids = {
            story["evidence_id"] for story in _read(raw_draft)["draft"]["stories"]
        }
    reviewed_ids = {eid for row in decisions.get("decisions", []) for eid in row["evidence_ids"]}
    selected_ids = {eid for story in accepted for claim in story["claims"] for eid in claim["evidence_ids"]}
    candidate_audit = []
    for candidate in evidence.values():
        eid = candidate.evidence_id
        disposition = "GENERATOR_NOT_SELECTED"
        if eid in selected_ids:
            disposition = "FINAL_SELECTED"
        elif eid in reviewed_ids:
            disposition = "REVIEWER_DROPPED"
        elif eid in generated_ids:
            disposition = "GENERATOR_VALIDATION_DROPPED"
        candidate_audit.append({
            "evidence_id": eid, "source_id": candidate.source_id,
            "canonical_url": candidate.link, "title": candidate.title,
            "story_type": candidate.story_type, "pillars": list(candidate.pillars),
            "disposition": disposition,
        })
    report = {
        "schema": "pkm.ai-daily-luna.run.v2", "contract": CONTRACT,
        "git_head": editorial._git_head(), "strategy_hash": STRATEGY_HASH,
        "model": MODEL, "reasoning": "medium", "content_date": state["content_date"],
        "started_at": state["started_at"], "created_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": int((datetime.now(timezone.utc) -
                               datetime.fromisoformat(state["started_at"])).total_seconds()),
        "status": status, "exit_code": code, "coverage": state["coverage"],
        "evidence_level": state["evidence_level"], "candidate_count": len(evidence),
        "story_count": len(accepted), "markdown_path": str(markdown_path) if markdown_path else None,
        "markdown_sha256": publishing._digest(markdown.encode("utf-8")) if markdown else None,
        "vault_write": False, "api_request_count": 0, "model_metrics": None,
        "usage_status": "CODEX_USAGE_NOT_EXPOSED",
        "role_execution_count": int(generator is not None) + int(reviewer is not None)
        + (2 if repair.exists() and _read(repair).get("error_code") == "EDITORIAL_REVISION_REQUIRED" else int(repair.exists())),
        "repair_count": int(repair.exists()),
        "generator_session_id": generator["session_id"] if generator else None,
        "reviewer_session_id": reviewer["session_id"] if reviewer else None,
        "model_identity_source": "CODEX_SESSION_DECLARATION",
        "evidence_chars": sum(len(row["summary"]) for row in _read(run / "generator-input.json")["candidates"]),
        "underfilled": len(accepted) < 6,
        "missing_modules": [key for key in brief.SECTIONS if key not in (
            {candidate.story_type for candidate in evidence.values()}
            if status == "MODULE_SOURCES_INCOMPLETE" else {story["section"] for story in accepted}
        )],
        "audit": {**state["audit"], "candidate_audit": candidate_audit,
                  "selected_urls": selected, "review_decisions": decisions.get("decisions", [])},
    }
    _sealed_write(run / "run-report.json", report)
    if markdown_path is not None and markdown is not None:
        _complete_shadow(run, report, markdown)
    return _report_result(run, report)


def _complete_shadow(run, report, markdown):
    """Complete a verified render without rewriting the sealed report."""
    content = markdown.encode("utf-8")
    if publishing._digest(content) != report["markdown_sha256"]:
        raise StageError("RENDERED_CONTENT_CHANGED")
    backing = run / "rendered-content.txt"
    publishing._write_or_verify(backing, content)
    destination = run / f"AI-Daily-{report['content_date']}-shadow.md"
    try:
        destination.hardlink_to(backing)
    except FileExistsError:
        pass
    if _file_hash(destination) != report["markdown_sha256"]:
        raise StageError("RENDERED_CONTENT_CHANGED")


def _report_result(run, report):
    fields = (
        "content_date", "model", "reasoning", "coverage", "evidence_level",
        "candidate_count", "story_count", "markdown_path", "repair_count",
        "role_execution_count", "model_metrics", "usage_status", "evidence_chars",
        "generator_session_id", "reviewer_session_id", "missing_modules",
    )
    return _result(
        report["status"], report["exit_code"], run_id=run.name,
        report_path=str(run / "run-report.json"), **{key: report[key] for key in fields},
    )


def _finalize(run, state, mode, confirm, runtime, vault):
    report_path = run / "run-report.json"
    if report_path.exists():
        report = _sealed_read(report_path)
        if report["contract"] != CONTRACT or report["strategy_hash"] != STRATEGY_HASH:
            raise StageError("REPORT_BINDING_MISMATCH")
        if report["markdown_path"]:
            accepted, decisions, frozen, reviewer = _check_review(run, state)
            expected = brief.render(
                date.fromisoformat(state["content_date"]), CoverageLevel(state["coverage"]),
                CoverageLevel(state["evidence_level"]), accepted, _candidates(state),
            )
            _complete_shadow(run, report, expected)
        result = _report_result(run, report)
    else:
        accepted, decisions, frozen, reviewer = _check_review(run, state)
        status, code = "PUBLISHED", 0
        if len(accepted) != 6:
            if not (run / "repair.json").exists():
                _write(run / "editorial-feedback.json", {
                    "instructions": "修正被拒绝的泛化/归因，或使用同模块已提供备选。不虚构证据，不放宽判断。只写新的generator-repair.json。必须重新独立审核。",
                    "draft": _read(_review_file(run, "review-input"))["generator_result"]["draft"],
                    "decisions": decisions["decisions"],
                })
                _write(run / "repair.json", {"role": "generator", "error_code": "EDITORIAL_REVISION_REQUIRED"})
                return _result(
                    "EDITORIAL_REVISION_REQUIRED", 4, run_id=state["run_id"],
                    feedback_path=str(run / "editorial-feedback.json"),
                    output_path=str(_output_path(run, "generator")),
                )
            status, code = "MODULE_REVIEW_INCOMPLETE", 4
        result = _report(
            run, state, accepted, decisions, status, code,
            generator={"session_id": frozen["generator_session_id"]}, reviewer=reviewer,
        )
        report = _sealed_read(report_path)
    return result


def _publish_reviewed(run, state, result, runtime, vault):
    report_path = run / "run-report.json"
    report = _sealed_read(report_path)
    if not report["markdown_path"]:
        return result
    shadow = editorial.ShadowResult(
        0, "PUBLISHED", date.fromisoformat(state["content_date"]),
        CoverageLevel(state["coverage"]), report["candidate_count"], report["story_count"],
        0, Path(report["markdown_path"]), report_path, CoverageLevel(state["evidence_level"]),
    )
    # The publisher gets the already reviewed artifact, never its default model runner.
    published = publishing.run_ai_daily_production(
        shadow.content_date, vault_daily_dir=vault,
        coordination_path=runtime / "ai-daily-production.lock", shadow_runner=lambda _day: shadow,
    )
    return result | published.payload() | {"exit_code": published.exit_code}


def run_luna_stage(
    stage: str, *, mode: str = "shadow", run_id: str | None = None,
    confirm_vault_write: bool = False, runtime_root: Path = RUNTIME,
    vault_daily_dir: Path = publishing.VAULT_DAILY_DIR, today: date | None = None,
    collect=None, context_loader=None,
) -> dict[str, Any]:
    """The shared CLI/test boundary; no model or credential calls."""
    day = today or datetime.now(SHANGHAI).date()
    context_loader = context_loader or (lambda: load_approved_user_context_v75().model_payload())
    run = None
    lock = None
    try:
        runtime = runtime_root.resolve(strict=True)
        vault = vault_daily_dir.resolve(strict=True)
        if mode not in {"shadow", "production"} or stage not in {"prepare", "review", "finalize"}:
            raise StageError("STAGE_OR_MODE_INVALID")
        if stage == "finalize" and mode == "production" and not confirm_vault_write:
            raise StageError("CONFIRM_VAULT_WRITE_REQUIRED")
        try:
            lock = publishing.acquire_production_lock(runtime / "ai-daily-production.lock")
        except OSError:
            return _result("PRODUCTION_BUSY", 4, error_code="PRODUCTION_BUSY")
        if stage == "prepare" and run_id is None:
            return _prepare(runtime, vault, day, mode, collect, context_loader)
        if not run_id:
            raise StageError("RUN_ID_REQUIRED")
        run, state = _load(runtime, run_id, day)
        if stage == "prepare":
            if (run / "run-report.json").exists():
                return _finalize(run, state, "shadow", False, runtime, vault)
            if _review_file(run, "review-state").exists():
                return _reviewer_ready(run, state, _sealed_read(_review_file(run, "review-state")))
            return _generator_ready(run, state)
        if (run / "run-report.json").exists() and stage == "review":
            return _finalize(run, state, "shadow", False, runtime, vault)
        if stage == "review":
            return _review(run, state)
        result = _finalize(run, state, mode, confirm_vault_write, runtime, vault)
        if mode != "production" or not result.get("markdown_path"):
            return result
        publishing.release_production_lock(lock)
        lock = None
        return _publish_reviewed(run, state, result, runtime, vault)
    except (OSError, ValueError) as error:
        code = str(error)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", code):
            code = "ARTIFACT_IO_OR_JSON_ERROR"
        result = _result(code, 4, error_code=code, run_id=run_id)
        if run is not None and isinstance(error, StageError) and error.repairable:
            repair_path = run / "repair.json"
            if repair_path.exists():
                result = _result("SCHEMA_REPAIR_EXHAUSTED", 4, error_code=code, run_id=run_id)
            else:
                role = "generator" if stage == "review" else "reviewer"
                _write(repair_path, {"role": role, "error_code": code})
                result |= {"status": "SCHEMA_REPAIR_REQUIRED", "role": role,
                           "output_path": str(_output_path(run, role))}
        if run is not None:
            _write(run / f"stage-error-{uuid4().hex}.json", result)
        return result
    finally:
        if lock is not None:
            publishing.release_production_lock(lock)
