"""Cross-domain weekly synthesis over verified source evidence.

The weekly edition is deliberately not a six-column digest. The six reader
interests remain collection labels; the reader sees a thesis, a small number
of explanatory themes, valuable material missed by the daily rotation, and a
bounded watchlist for next week.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import daily_brief as daily
from .v75_collection import Candidate, CoverageLevel

TEXT = daily.TEXT
IDS = {"type": "array", "minItems": 1, "maxItems": 6, "items": TEXT}
REFS = {"type": "array", "minItems": 1, "maxItems": 5, "items": TEXT}
CLAIM = daily._closed({"text": TEXT, "evidence_ids": IDS})
CONNECTION = daily._closed({"text": TEXT, "evidence_ids": IDS, "context_refs": REFS})
IMPLICATIONS = daily._closed({
    "research": {"anyOf": [{"type": "null"}, CLAIM]},
    "practice": {"anyOf": [{"type": "null"}, CLAIM]},
    "venture": {"anyOf": [{"type": "null"}, CLAIM]},
})
THEME = daily._closed({
    "kind": {"type": "string", "enum": ["TREND", "EARLY_SIGNAL", "CASE"]},
    "title": CLAIM,
    "observation": {"type": "array", "minItems": 1, "maxItems": 3, "items": CLAIM},
    "why_now": CLAIM,
    "mechanism": CLAIM,
    "implications": IMPLICATIONS,
    "counter_evidence": {"anyOf": [{"type": "null"}, CLAIM]},
    "connection": CONNECTION,
    "watch_next": {"anyOf": [{"type": "null"}, CLAIM]},
})
MISSED = daily._closed({"title": CLAIM, "why": CLAIM})
WATCH = daily._closed({"subject": CLAIM, "why": CLAIM, "indicator": CLAIM})
GENERATOR_SCHEMA = daily._closed({
    "thesis": {"anyOf": [{"type": "null"}, CLAIM]},
    "themes": {"type": "array", "minItems": 1, "maxItems": 4, "items": THEME},
    "missed_but_valuable": {"type": "array", "maxItems": 2, "items": MISSED},
    "watchlist": {"type": "array", "maxItems": 4, "items": WATCH},
})

GENERATOR_PROMPT = f"""你是个人 AI 周报主编。日报回答“今天看什么”，本周报回答“这一周发生了什么结构性变化，为什么值得在更长时间尺度上理解”。
六类兴趣只是内部来源标签，绝不是六个必须填满的栏目。先阅读全部封存材料，再进行跨来源筛选；不要按栏目各写一段。

成稿要像一篇短而有判断力的编辑简报：
1. thesis 是一条可证伪的全周主判断；证据不足时填 null，不要为了完整而概括。
2. themes 是 1–4 个最重要的解释单元。TREND 只能在独立来源共同指向同一机制，或同一事件本周出现可核对的推进时使用；只有一个案例用 CASE；刚出现、证据还少但方向值得盯的结构变化用 EARLY_SIGNAL。
3. 每个 theme 依次讲清：本周观察到的事实、为什么现在值得看、背后的机制、对研究/实践/创业的不同影响、反方证据或未知边界、下周看什么信号。不要把“有新闻”当成趋势。
4. missed_but_valuable 只收录本周已抓取、证据合格但没有进入日报的材料；说明它为什么值得补读，不得暗示它在日报出现过。
5. watchlist 是实体或方向，不是泛泛的“继续关注”；必须给出下周可观察的具体变化。

所有 title、事实、机制、判断、反方、个人关联和观察项都是待审核 claim，必须列出真实 evidence_ids。connection 只能引用批准上下文中的真实 context_refs；不确定就少写。
原始日期、来源、署名由渲染器补上。不得输出 URL、Markdown、路径、未提供的作者或录用结论。只写指定 JSON 文件。输出 JSON envelope，model={daily.MODEL}，session_id 填真实自身身份，draft 使用上述 schema。"""

REVIEWER_PROMPT = daily.REVIEWER_PROMPT + """
本次是全周综合审核，不是六栏逐条校对。先判断每个 theme 是否值得占用周报注意力，再逐 claim 审核。
TREND 必须由独立来源支持同一机制，或有同一事件的可核对周内推进；仅“同属 Agent/AI”不能算共同趋势。EARLY_SIGNAL 允许证据较少，但必须明确是早期信号，不能写成已证实的行业变化。CASE 只能保持个案边界。
why_now、mechanism、counter_evidence 和 connection 必须比事实多一层解释，但不能超出证据。若关键事实、机制或边界被拒，整条 theme DROP；可选的 thesis、missed、watchlist 项逐项拒绝即可。Reviewer 不得用“看起来合理”替代 evidence mapping。
"""


def generator_schema() -> bytes:
    return json.dumps(GENERATOR_SCHEMA, ensure_ascii=False).encode("utf-8")


def role_envelope_schema() -> bytes:
    return json.dumps(daily._closed({
        "model": {"type": "string", "enum": [daily.MODEL]},
        "session_id": {"type": "string", "pattern": r"^(?:[0-9a-fA-F-]{36}|/root/[a-z0-9_/]+)$"},
        "draft": GENERATOR_SCHEMA,
    }), ensure_ascii=False).encode("utf-8")


def _checked_refs(connection, allowed_refs):
    refs = [path.removeprefix("approved_user_context.").removeprefix("fields.")
            for path in connection["context_refs"]]
    if any(path not in allowed_refs for path in refs):
        raise ValueError("GENERATOR_CONTEXT_REFS_INVALID")
    return refs


def validate_draft(draft, evidence: dict[str, Candidate], context, content_date: date):
    if not daily.matches_schema(draft, GENERATOR_SCHEMA):
        raise ValueError("OUTPUT_SCHEMA_INVALID")
    allowed_refs = daily._approved_context_paths(context)
    names = set()

    def checked_ids(value):
        ids = value["evidence_ids"]
        if len(ids) != len(set(ids)) or any(eid not in evidence for eid in ids):
            raise ValueError("GENERATOR_EVIDENCE_BINDING_INVALID")
        return ids

    def claim(identifier, value, kind="fact", refs=()):
        return {"claim_id": identifier, "statement": value["text"],
                "evidence_ids": checked_ids(value), "claim_kind": kind,
                "context_refs": list(refs)}

    def cited(theme):
        parts = [theme["title"], *theme["observation"], theme["why_now"],
                 theme["mechanism"], theme["connection"]]
        parts.extend(value for value in theme["implications"].values() if value is not None)
        if theme["counter_evidence"] is not None:
            parts.append(theme["counter_evidence"])
        if theme["watch_next"] is not None:
            parts.append(theme["watch_next"])
        return {eid for part in parts for eid in checked_ids(part)}

    def ensure_kind(theme, ids):
        if theme["kind"] == "CASE":
            return
        origins = {evidence[eid].canonical_origin_id for eid in ids}
        week_start = content_date - timedelta(days=content_date.weekday())
        try:
            dates = {date.fromisoformat(evidence[eid].published) for eid in ids}
        except ValueError as error:
            raise ValueError("WEEKLY_THEME_DATE_INVALID") from error
        progressed = len(dates) >= 2 and all(week_start <= value <= content_date for value in dates)
        if theme["kind"] == "TREND" and len(origins) < 2 and not progressed:
            raise ValueError("WEEKLY_TREND_EVIDENCE_INSUFFICIENT")
        if theme["kind"] == "EARLY_SIGNAL" and len(ids) < 2 and theme["counter_evidence"] is None:
            raise ValueError("WEEKLY_EARLY_SIGNAL_BOUNDARY_MISSING")

    def normalize_theme(index, theme):
        title = theme["title"]["text"].strip().casefold()
        if title in names:
            raise ValueError("WEEKLY_THEME_DUPLICATE")
        names.add(title)
        ids = cited(theme)
        ensure_kind(theme, ids)
        refs = _checked_refs(theme["connection"], allowed_refs)
        prefix = f"weekly-{index}"
        claims = [claim(f"{prefix}-title", theme["title"], "editorial_inference")]
        observation_ids = []
        for part_index, part in enumerate(theme["observation"], 1):
            ident = f"{prefix}-observation-{part_index}"
            observation_ids.append(ident)
            claims.append(claim(ident, part))
        why = f"{prefix}-why-now"
        mechanism = f"{prefix}-mechanism"
        connection = f"{prefix}-connection"
        claims.extend((claim(why, theme["why_now"], "editorial_inference"),
                       claim(mechanism, theme["mechanism"], "editorial_inference"),
                       claim(connection, theme["connection"], "editorial_inference", refs)))
        implication_ids = []
        for label, value in theme["implications"].items():
            if value is not None:
                ident = f"{prefix}-implication-{label}"
                implication_ids.append(ident)
                claims.append(claim(ident, value, "editorial_inference"))
        optional = []
        counter_id = None
        if theme["counter_evidence"] is not None:
            counter_id = f"{prefix}-counter-evidence"
            claims.append(claim(counter_id, theme["counter_evidence"], "uncertainty"))
            optional.append(counter_id)
        watch_id = None
        if theme["watch_next"] is not None:
            watch_id = f"{prefix}-watch-next"
            claims.append(claim(watch_id, theme["watch_next"], "editorial_inference"))
            optional.append(watch_id)
        return {
            "story_id": prefix, "section": "weekly", "kind": theme["kind"],
            "title_claim_id": f"{prefix}-title", "event_claim_ids": observation_ids,
            "importance_claim_ids": [why], "mechanism_claim_ids": [mechanism],
            "impact_claim_ids": [connection, *implication_ids],
            "uncertainty_claim_ids": [counter_id] if counter_id else [],
            "watch_claim_ids": [watch_id] if watch_id else [],
            "optional_claim_ids": optional, "claims": claims,
        }

    themes = [normalize_theme(index, theme) for index, theme in enumerate(draft["themes"], 1)]
    if draft["thesis"] is not None:
        themes[0]["claims"].append(claim("weekly-thesis", draft["thesis"], "editorial_inference"))
        themes[0]["lead_claim_id"] = "weekly-thesis"
        themes[0]["optional_claim_ids"].append("weekly-thesis")
    else:
        themes[0]["lead_claim_id"] = None

    extras = []
    for index, item in enumerate(draft["missed_but_valuable"], 1):
        prefix = f"weekly-missed-{index}"
        extras.append({
            "story_id": prefix, "section": "weekly_missed", "kind": "MISSED",
            "title_claim_id": f"{prefix}-title", "event_claim_ids": [],
            "importance_claim_ids": [f"{prefix}-why"], "mechanism_claim_ids": [],
            "impact_claim_ids": [], "uncertainty_claim_ids": [], "watch_claim_ids": [],
            "optional_claim_ids": [], "claims": [
                claim(f"{prefix}-title", item["title"], "fact"),
                claim(f"{prefix}-why", item["why"], "editorial_inference"),
            ], "lead_claim_id": None,
        })
    for index, item in enumerate(draft["watchlist"], 1):
        prefix = f"weekly-watch-{index}"
        extras.append({
            "story_id": prefix, "section": "weekly_watch", "kind": "WATCH",
            "title_claim_id": f"{prefix}-subject", "event_claim_ids": [],
            "importance_claim_ids": [f"{prefix}-why"], "mechanism_claim_ids": [],
            "impact_claim_ids": [f"{prefix}-indicator"], "uncertainty_claim_ids": [],
            "watch_claim_ids": [], "optional_claim_ids": [], "claims": [
                claim(f"{prefix}-subject", item["subject"], "fact"),
                claim(f"{prefix}-why", item["why"], "editorial_inference"),
                claim(f"{prefix}-indicator", item["indicator"], "editorial_inference"),
            ], "lead_claim_id": None,
        })
    return tuple(themes + extras)


def accepted_signals(signals, decisions):
    verdicts = {row["claim_id"]: row["decision"] for row in decisions["decisions"]}
    accepted = []
    for signal in signals:
        required = {row["claim_id"] for row in signal["claims"]} - set(signal["optional_claim_ids"])
        if any(verdicts.get(identifier) != "ACCEPT" for identifier in required):
            continue
        projected = dict(signal)
        projected["claims"] = [row for row in signal["claims"]
                                if verdicts.get(row["claim_id"]) == "ACCEPT"]
        for key in ("watch_claim_ids", "uncertainty_claim_ids"):
            projected[key] = [identifier for identifier in signal[key]
                              if verdicts.get(identifier) == "ACCEPT"]
        lead_id = signal.get("lead_claim_id")
        projected["lead_claim_id"] = lead_id if lead_id and verdicts.get(lead_id) == "ACCEPT" else None
        accepted.append(projected)
    if accepted and accepted[0].get("lead_claim_id"):
        lead = next(row for row in accepted[0]["claims"]
                    if row["claim_id"] == accepted[0]["lead_claim_id"])
        used = {eid for signal in accepted for row in signal["claims"]
                if row["claim_id"] != accepted[0].get("lead_claim_id")
                for eid in row["evidence_ids"]}
        if not set(lead["evidence_ids"]) <= used:
            accepted[0]["claims"] = [row for row in accepted[0]["claims"]
                                      if row["claim_id"] != accepted[0]["lead_claim_id"]]
            accepted[0]["lead_claim_id"] = None
    return tuple(accepted)


def _claims(signal):
    return {row["claim_id"]: row["statement"] for row in signal["claims"]}


def _links(signal, evidence):
    ids = dict.fromkeys(eid for row in signal["claims"] for eid in row["evidence_ids"])
    rows = []
    for eid in ids:
        item = evidence[eid]
        raw_label = item.source_links[0][0] if item.source_links else item.source
        parts = [part for part in raw_label.split(" · ") if part not in {
            "原文未记录署名", "本周新增阅读", "本周已读回顾", "经典延伸阅读", item.published,
        }]
        origin = " · ".join(dict.fromkeys(parts)) or item.source
        reading = "本周已读回顾" if item.content_type == "weekly_excerpt" else "本周新增阅读"
        classic = " · 经典延伸阅读" if item.content_type == "evergreen" else ""
        rows.append(f"[{reading} · {origin}]({item.link}) · 原始日期 {item.published}{classic}")
    return rows


def render(day: date, coverage: CoverageLevel, evidence_level: CoverageLevel, signals,
           evidence: dict[str, Candidate]) -> str:
    lines = ["---", f"date: {day}", "type: ai-weekly-shadow", "production: false",
             f"operational_coverage: {coverage.value}", f"evidence_availability: {evidence_level.value}",
             "---", "", f"# AI Weekly · {day}", "",
             f"本周综合 · {day - timedelta(days=day.weekday())} 至 {day}"]
    lead = next((claim["statement"] for signal in signals for claim in signal["claims"]
                 if claim["claim_id"] == signal.get("lead_claim_id")), None)
    if lead:
        lines += ["", "## 本周主判断", "", lead]
    themes = [signal for signal in signals if signal["kind"] in {"TREND", "EARLY_SIGNAL", "CASE"}]
    for index, signal in enumerate(themes, 1):
        claims = _claims(signal)
        labels = {"TREND": "共同趋势", "EARLY_SIGNAL": "早期信号", "CASE": "关键个案"}
        lines += ["", f"## {index}. {claims[signal['title_claim_id']]} · {labels[signal['kind']]}", "",
                  "本周看到："]
        lines += [claims[ident] for ident in signal["event_claim_ids"]] + [""]
        lines += [f"为什么现在：{claims[signal['importance_claim_ids'][0]]}", "",
                  f"背后机制：{claims[signal['mechanism_claim_ids'][0]]}", ""]
        implication_labels = {"research": "对研究", "practice": "对实践", "venture": "对创业判断"}
        for ident in signal["impact_claim_ids"][1:]:
            label = next((name for name in implication_labels if f"-implication-{name}" in ident), "practice")
            lines += [f"{implication_labels[label]}：{claims[ident]}", ""]
        for ident in signal["uncertainty_claim_ids"]:
            lines += [f"边界与反方：{claims[ident]}", ""]
        for ident in signal["watch_claim_ids"]:
            lines += [f"下周看什么：{claims[ident]}", ""]
        lines.extend(_links(signal, evidence))
    missed = [signal for signal in signals if signal["kind"] == "MISSED"]
    if missed:
        lines += ["", "## 日报未选但值得补读", ""]
        for signal in missed:
            claims = _claims(signal)
            lines += [f"- {claims[signal['title_claim_id']]}：{claims[signal['importance_claim_ids'][0]]}"]
            lines.extend(_links(signal, evidence))
    watch = [signal for signal in signals if signal["kind"] == "WATCH"]
    if watch:
        lines += ["", "## 下周观察", ""]
        for signal in watch:
            claims = _claims(signal)
            lines += [f"- {claims[signal['title_claim_id']]}：{claims[signal['importance_claim_ids'][0]]}；指标：{claims[signal['impact_claim_ids'][0]]}"]
            lines.extend(_links(signal, evidence))
    return "\n".join(lines) + "\n"
