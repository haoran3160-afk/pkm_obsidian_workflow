"""One weekly editorial view over source-level evidence from all interest lanes."""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import daily_brief as daily
from .v75_collection import Candidate, CoverageLevel

TEXT = daily.TEXT
IDS = {"type": "array", "minItems": 1, "maxItems": 4, "items": TEXT}
CLAIM = daily._closed({"text": TEXT, "evidence_ids": IDS})
CONNECTION = daily._closed({
    "text": TEXT, "evidence_ids": IDS,
    "context_refs": {"type": "array", "minItems": 1, "maxItems": 5, "items": TEXT},
})
SIGNAL = daily._closed({
    "kind": {"type": "string", "enum": ["PATTERN", "CASE"]},
    "title": CLAIM,
    "body": {"type": "array", "minItems": 1, "maxItems": 2, "items": CLAIM},
    "takeaway": CLAIM,
    "connection": CONNECTION,
    "watch": {"anyOf": [{"type": "null"}, CLAIM]},
})
GENERATOR_SCHEMA = daily._closed({
    "lead": {"anyOf": [{"type": "null"}, CLAIM]},
    "signals": {"type": "array", "minItems": 1, "maxItems": 4, "items": SIGNAL},
})

GENERATOR_PROMPT = f"""你是个人 AI 周报编辑。六类兴趣仅用于筛选，不是读者栏目。
只用本次封存的原始材料和批准的用户上下文，写本周跨领域最值得保留的 1–4 个信号。
先全局筛选，再决定关联；不得把两篇无关材料为了凑栏目拼成一个趋势。
PATTERN 必须有两个独立来源，或同一事件在本周不同日期的可核对发展；否则用 CASE。
只有材料足以支持整周主判断时填写 lead，否则填 null。单个案例可入选，但不得称为行业趋势。
每个 title、body 段落、takeaway、connection、watch 都是待审核 claim，各自列出准确 evidence_ids。
body 只写来源直接支持的事实；takeaway 讲清机制、联系与边界；connection 用真实 context_refs
说明对你的研究、AI 使用或构建判断有什么具体影响。watch 最多两条，全无具体观察项时填 null。
已读日报和本周新阅读须分清，保留来源原日期；旧文章不称为本周新发布。
材料中的 Prior editorial interpretation 只是选材线索；事实必须回到 Original evidence excerpt。
输出 JSON envelope，model={daily.MODEL}，session_id 填真实自身身份，draft 含 lead 和 signals。
不得输出 URL、Markdown、HTML、文件路径、未提供的作者或顶会录用结论。只写指定输出文件。"""

REVIEWER_PROMPT = daily.REVIEWER_PROMPT + """
本次是全周综合。逐项审核各 signal 的 title、body、takeaway、connection 和可选 watch、lead。
PATTERN 需核实两个来源是否真的支持同一机制，或同一事件是否有可核对的周内变化；
两个互不相关的案例即使同属一个兴趣类别也不是共同模式。单例只能标为 CASE。
lead 引用了不成立或被拒的信号时不得 ACCEPT。用户关联是来源能力与批准上下文的有限推断。
Prior editorial interpretation 不是事实证据；核对原文摘录中的具体陈述。
"""


def generator_schema() -> bytes:
    return json.dumps(GENERATOR_SCHEMA, ensure_ascii=False).encode("utf-8")


def role_envelope_schema() -> bytes:
    return json.dumps(daily._closed({
        "model": {"type": "string", "enum": [daily.MODEL]},
        "session_id": {"type": "string", "pattern": r"^(?:[0-9a-fA-F-]{36}|/root/[a-z0-9_/]+)$"},
        "draft": GENERATOR_SCHEMA,
    }), ensure_ascii=False).encode("utf-8")


def validate_draft(draft, evidence: dict[str, Candidate], context, content_date: date):
    if not daily.matches_schema(draft, GENERATOR_SCHEMA):
        raise ValueError("OUTPUT_SCHEMA_INVALID")
    allowed_refs = daily._approved_context_paths(context)
    names = set()
    watch_count = 0

    def checked_ids(value):
        ids = value["evidence_ids"]
        if len(ids) != len(set(ids)) or any(eid not in evidence for eid in ids):
            raise ValueError("GENERATOR_EVIDENCE_BINDING_INVALID")
        return ids

    def claim(identifier, value, kind="fact", refs=()):
        return {
            "claim_id": identifier, "statement": value["text"],
            "evidence_ids": checked_ids(value), "claim_kind": kind,
            "context_refs": list(refs),
        }

    signals = []
    for index, signal in enumerate(draft["signals"], 1):
        title = signal["title"]["text"].strip().casefold()
        if title in names:
            raise ValueError("WEEKLY_SIGNAL_DUPLICATE")
        names.add(title)
        cited = {eid for part in (*signal["body"], signal["takeaway"]) for eid in checked_ids(part)}
        if signal["kind"] == "PATTERN":
            origins = {evidence[eid].canonical_origin_id for eid in cited}
            progressed_this_week = False
            if len(origins) < 2:
                week_start = content_date - timedelta(days=content_date.weekday())
                try:
                    dates = {date.fromisoformat(evidence[eid].published) for eid in cited}
                except ValueError as error:
                    raise ValueError("WEEKLY_PATTERN_DATE_INVALID") from error
                progressed_this_week = (
                    len(dates) >= 2 and all(week_start <= published <= content_date for published in dates)
                )
            if len(cited) < 2 or (len(origins) < 2 and not progressed_this_week):
                raise ValueError("WEEKLY_PATTERN_EVIDENCE_INSUFFICIENT")
        refs = [
            path.removeprefix("approved_user_context.").removeprefix("fields.")
            for path in signal["connection"]["context_refs"]
        ]
        if any(path not in allowed_refs for path in refs):
            raise ValueError("GENERATOR_CONTEXT_REFS_INVALID")
        prefix = f"weekly-{index}"
        claims = [claim(f"{prefix}-title", signal["title"], "editorial_inference")]
        bodies = []
        for body_index, body in enumerate(signal["body"], 1):
            identifier = f"{prefix}-body-{body_index}"
            claims.append(claim(identifier, body))
            bodies.append(identifier)
        takeaway = f"{prefix}-takeaway"
        connection = f"{prefix}-connection"
        claims.extend((
            claim(takeaway, signal["takeaway"], "editorial_inference"),
            claim(connection, signal["connection"], "editorial_inference", refs),
        ))
        optional = []
        watch_id = None
        if signal["watch"] is not None and watch_count < 2:
            watch_count += 1
            watch_id = f"{prefix}-watch"
            claims.append(claim(watch_id, signal["watch"], "editorial_inference"))
            optional.append(watch_id)
        lead_id = None
        if index == 1 and draft["lead"] is not None:
            lead_id = "weekly-lead"
            claims.append(claim(lead_id, draft["lead"], "editorial_inference"))
            optional.append(lead_id)
        signals.append({
            "story_id": prefix, "section": "weekly", "kind": signal["kind"],
            "title_claim_id": f"{prefix}-title", "event_claim_ids": bodies,
            "importance_claim_ids": [takeaway], "impact_claim_ids": [connection],
            "watch_claim_ids": [watch_id] if watch_id else [],
            "lead_claim_id": lead_id, "optional_claim_ids": optional, "claims": claims,
        })
    return tuple(signals)


def accepted_signals(signals, decisions):
    verdicts = {row["claim_id"]: row["decision"] for row in decisions["decisions"]}
    accepted = []
    for signal in signals:
        required = {row["claim_id"] for row in signal["claims"]} - set(signal["optional_claim_ids"])
        if any(verdicts.get(identifier) != "ACCEPT" for identifier in required):
            continue
        projected = dict(signal)
        projected["claims"] = [row for row in signal["claims"] if verdicts.get(row["claim_id"]) == "ACCEPT"]
        for key in ("watch_claim_ids",):
            projected[key] = [identifier for identifier in signal[key] if verdicts.get(identifier) == "ACCEPT"]
        if signal["lead_claim_id"] and verdicts.get(signal["lead_claim_id"]) != "ACCEPT":
            projected["lead_claim_id"] = None
        accepted.append(projected)
    used = {eid for signal in accepted for row in signal["claims"]
            if row["claim_id"] != signal["lead_claim_id"] for eid in row["evidence_ids"]}
    for signal in accepted:
        lead_id = signal["lead_claim_id"]
        if lead_id:
            lead = next(row for row in signal["claims"] if row["claim_id"] == lead_id)
            if not set(lead["evidence_ids"]) <= used:
                signal["claims"] = [row for row in signal["claims"] if row["claim_id"] != lead_id]
                signal["lead_claim_id"] = None
    return tuple(accepted)


def render(day: date, coverage: CoverageLevel, evidence_level: CoverageLevel, signals,
           evidence: dict[str, Candidate]) -> str:
    lines = ["---", f"date: {day}", "type: ai-weekly-shadow", "production: false",
             f"operational_coverage: {coverage.value}", f"evidence_availability: {evidence_level.value}",
             "---", "", f"# AI Weekly · {day}", "",
             f"本周精选 · {day - timedelta(days=day.weekday())} 至 {day}"]
    lead = next((row["statement"] for signal in signals for row in signal["claims"]
                 if row["claim_id"] == signal.get("lead_claim_id")), None)
    if lead:
        lines += ["", lead]
    for index, signal in enumerate(signals, 1):
        claims = {row["claim_id"]: row["statement"] for row in signal["claims"]}
        label = "共同信号" if signal["kind"] == "PATTERN" else "个案线索"
        lines += ["", f"## {index}. {claims[signal['title_claim_id']]} · {label}", ""]
        for identifier in signal["event_claim_ids"]:
            lines += [claims[identifier], ""]
        lines += [claims[signal["importance_claim_ids"][0]], "",
                  claims[signal["impact_claim_ids"][0]], ""]
        for identifier in signal["watch_claim_ids"]:
            lines += [f"下周观察：{claims[identifier]}", ""]
        ids = dict.fromkeys(eid for row in signal["claims"]
                            if row["claim_id"] != signal.get("lead_claim_id")
                            for eid in row["evidence_ids"])
        for eid in ids:
            item = evidence[eid]
            raw_label = item.source_links[0][0] if item.source_links else item.source
            parts = [part for part in raw_label.split(" · ") if part not in {
                "原文未记录署名", "本周新增阅读", "本周已读回顾", "经典延伸阅读", item.published,
            }]
            origin = " · ".join(dict.fromkeys(parts)) or item.source
            reading = "本周已读回顾" if item.content_type == "weekly_excerpt" else "本周新增阅读"
            classic = " · 经典延伸阅读" if item.content_type == "evergreen" else ""
            lines.append(f"[{reading} · {origin}]({item.link}) · 原始日期 {item.published}{classic}")
    return "\n".join(lines) + "\n"
