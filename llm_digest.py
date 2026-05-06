#!/usr/bin/env python3
"""
Optional LLM refinement for the final daily digest.

The workflow must remain useful without any external model. This module is an
opt-in enhancer: when explicitly enabled and API quota is available, it can
rewrite the already curated digest copy into more editorial Chinese. When it
fails, the deterministic local copy should still be good enough to publish.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

import daily_curation

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_CURATION_MODELS = ("gpt-5.4-mini", "gpt-4.1-mini", "gpt-4o-mini")
DEFAULT_REASONING_EFFORT = "medium"
REQUEST_TIMEOUT_SECONDS = 90


def can_generate_digest_copy() -> bool:
    enabled = os.getenv("PKM_ENABLE_LLM_DIGEST_COPY", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def generate_digest_copy(
    plan: daily_curation.DailyDigestPlan,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any] | None:
    if not can_generate_digest_copy():
        return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip().rstrip("/")
    endpoint = f"{base_url}/responses" if base_url else OPENAI_RESPONSES_URL

    requested_model = (model or os.getenv("PKM_CURATION_MODEL") or "").strip()
    model_candidates = [requested_model] if requested_model else list(DEFAULT_CURATION_MODELS)
    last_error: Exception | None = None

    for idx, model_name in enumerate(model_candidates):
        effective_reasoning_effort = (
            reasoning_effort
            or os.getenv("PKM_CURATION_REASONING_EFFORT")
            or DEFAULT_REASONING_EFFORT
        )
        payload = _build_request_payload(
            plan,
            model=model_name,
            reasoning_effort=effective_reasoning_effort,
        )
        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            body = response.json()
            response_text = _extract_output_text(body)
            if not response_text:
                return None

            parsed = json.loads(response_text)
            return _normalize_copy_payload(parsed)
        except requests.HTTPError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429 and idx + 1 < len(model_candidates):
                time.sleep(2 + idx)
                continue
            raise
        except Exception as exc:
            last_error = exc
            raise

    if last_error:
        raise last_error
    return None


def _build_request_payload(
    plan: daily_curation.DailyDigestPlan,
    *,
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "store": False,
        "max_output_tokens": 2500,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "你是顶级中文科技编辑。任务不是重新选题，而是在给定证据基础上，"
                            "把已选条目改写成一份信息密度高、判断明确、没有空话的 AI 日报。"
                            "禁止编造未提供的事实、指标、时间或来源。允许写“未披露”，"
                            "但优先提炼真实可执行结论。输出必须是严格 JSON，并符合给定 schema。"
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "date": plan.date,
                                "selected_sections": _build_plan_evidence(plan),
                                "style_contract": {
                                    "top_sections": ["Top 1", "Top 2", "Top 3"],
                                    "deep_labels": ["创投洞见"],
                                    "brief_labels": ["洞见"],
                                    "video_label": "今日视频",
                                    "language": "zh-CN",
                                },
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "daily_digest_copy",
                "strict": True,
                "schema": _digest_copy_schema(),
            }
        },
    }
    if model.startswith("gpt-5") or model.startswith("o"):
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def _build_plan_evidence(plan: daily_curation.DailyDigestPlan) -> dict[str, Any]:
    return {
        "top_stories": [
            _story_evidence(slot=f"Top {idx}", source=source, item=item)
            for idx, (source, item) in enumerate(plan.top_stories, start=1)
        ],
        "venture_story": _optional_story_evidence("创投洞见", plan.venture_story),
        "insight_story": _optional_story_evidence("洞见", plan.growth_story),
        "video_story": _optional_story_evidence("今日视频", plan.video_story),
        "ai_company_story": _optional_story_evidence("洞见", plan.solopreneur_story),
    }


def _optional_story_evidence(
    label: str, story: tuple[str, dict[str, Any]] | None
) -> dict[str, Any] | None:
    if not story:
        return None
    source, item = story
    return _story_evidence(slot=label, source=source, item=item)


def _story_evidence(*, slot: str, source: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "slot": slot,
        "source": source,
        "title_en": str(item.get("title", "")).strip(),
        "url": str(item.get("link", "")).strip(),
        "summary_evidence": str(item.get("summary", "")).strip()[:900],
        "published": str(item.get("published", "")).strip(),
        "content_type": str(item.get("content_type", "")).strip(),
        "domain": str(item.get("domain", "")).strip(),
    }


def _digest_copy_schema() -> dict[str, Any]:
    deep_section = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline_cn": {"type": "string"},
            "core_concepts": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "core_finding": {"type": "string"},
            "key_details": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "actionable_insight": {"type": "string"},
        },
        "required": [
            "headline_cn",
            "core_concepts",
            "core_finding",
            "key_details",
            "actionable_insight",
        ],
    }
    brief_section = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline_cn": {"type": "string"},
            "core_concepts": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "one_line_summary": {"type": "string"},
            "key_points": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "actionable_insight": {"type": "string"},
        },
        "required": [
            "headline_cn",
            "core_concepts",
            "one_line_summary",
            "key_points",
            "actionable_insight",
        ],
    }
    video_section = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline_cn": {"type": "string"},
            "core_concepts": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "core_conclusion": {"type": "string"},
            "method_points": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "string"},
            },
            "actionable_insight": {"type": "string"},
        },
        "required": [
            "headline_cn",
            "core_concepts",
            "core_conclusion",
            "method_points",
            "actionable_insight",
        ],
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "top_stories": {"type": "array", "maxItems": 3, "items": deep_section},
            "venture_story": {"anyOf": [deep_section, {"type": "null"}]},
            "insight_story": {"anyOf": [brief_section, {"type": "null"}]},
            "video_story": {"anyOf": [video_section, {"type": "null"}]},
            "ai_company_story": {"anyOf": [brief_section, {"type": "null"}]},
        },
        "required": [
            "top_stories",
            "venture_story",
            "insight_story",
            "video_story",
            "ai_company_story",
        ],
    }


def _extract_output_text(body: dict[str, Any]) -> str:
    for output in body.get("output", []):
        if output.get("type") != "message":
            continue
        for part in output.get("content", []):
            if part.get("type") == "output_text":
                return str(part.get("text", "")).strip()
            if part.get("type") == "refusal":
                raise RuntimeError(str(part.get("refusal", "Model refused the digest request.")))
    return ""


def _normalize_copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload["top_stories"] = list(payload.get("top_stories", []))[:3]
    return payload
