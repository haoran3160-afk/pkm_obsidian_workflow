#!/usr/bin/env python3
"""
fetcher.py - Feed Fetching Layer
Handles all data retrieval: RSS feeds and YouTube channels.
Includes retry with exponential backoff via tenacity.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger("pkm.fetcher")

AI_KEYWORDS = [
    "ai",
    "llm",
    "gpt",
    "machine learning",
    "neural",
    "deep learning",
    "openai",
    "anthropic",
    "gemini",
    "transformer",
    "model",
    "inference",
    "fine-tun",
    "rag",
    "agent",
    "benchmark",
    "arxiv",
]

AI_NEWS_DOMAINS = {"ai-news", "solopreneur"}

DEFAULT_AI_INTEREST_TOPICS = [
    "skill",
    "skills",
    "harness engineering",
    "context engineering",
    "agent engineering",
    "context window",
    "context management",
    "prompt engineering",
    "eval",
    "evaluation",
    "vibe coding",
    "ai coding",
    "tool calling",
    "workflow",
]

DEFAULT_AI_PRIORITY_TOPICS = [
    "harness engineering",
    "context engineering",
    "vibe coding",
    "agent engineering",
    "evaluation",
    "tool calling",
    "memory",
    "long context",
    "coding agent",
    "best practices",
]

DEFAULT_AI_EXCLUDE_KEYWORDS = [
    "funding round",
    "acquisition",
    "job posting",
    "hiring",
    "press release",
    "sponsored",
    "webinar",
    "event recap",
    "conference tickets",
    "coupon",
]

_URL_ACCESS_CACHE: dict[str, tuple[bool, str]] = {}


def _contains_keywords(text: str, keywords: Iterable[str]) -> bool:
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)


def ai_filter(text: str) -> bool:
    return _contains_keywords(text, AI_KEYWORDS)


def _clean_summary(raw_text: str, max_len: int) -> str:
    return re.sub(r"<[^>]+>", "", raw_text).strip()[:max_len]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _host_from_url(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True

    host = _host_from_url(url)
    if not host:
        return False

    for domain in allowed_domains:
        d = (domain or "").strip().lower()
        if d.startswith("www."):
            d = d[4:]
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


def _is_url_accessible(url: str, timeout_sec: int = 8) -> tuple[bool, str]:
    headers = {"User-Agent": "obsidian-pkm-workflow/3.0"}
    try:
        head = requests.head(url, allow_redirects=True, timeout=timeout_sec, headers=headers)
        if 200 <= head.status_code < 400:
            return True, f"HEAD {head.status_code}"
        if head.status_code not in (403, 405):
            return False, f"HEAD {head.status_code}"
    except Exception:
        pass

    try:
        resp = requests.get(
            url, allow_redirects=True, timeout=timeout_sec, headers=headers, stream=True
        )
        status = resp.status_code
        resp.close()
        if 200 <= status < 400:
            return True, f"GET {status}"
        return False, f"GET {status}"
    except Exception as exc:
        return False, str(exc)


def _is_url_accessible_cached(url: str, timeout_sec: int) -> tuple[bool, str]:
    key = normalize_url(url) or url.strip()
    if key in _URL_ACCESS_CACHE:
        return _URL_ACCESS_CACHE[key]
    result = _is_url_accessible(url, timeout_sec=timeout_sec)
    _URL_ACCESS_CACHE[key] = result
    return result


def _score_ai_interest(
    title: str,
    summary: str,
    source_name: str,
    ai_interest_topics: list[str],
    ai_priority_topics: list[str],
    ai_exclude_keywords: list[str],
) -> tuple[int, list[str]]:
    text = f"{title} {summary}".lower()
    score = 0
    reasons: list[str] = []

    if ai_filter(text):
        score += 1
        reasons.append("base-ai")

    for kw in ai_priority_topics:
        if kw and kw in text:
            score += 4
            reasons.append(f"priority:{kw}")

    for kw in ai_interest_topics:
        if kw and kw in text:
            score += 2
            reasons.append(f"interest:{kw}")

    for kw in ai_exclude_keywords:
        if kw and kw in text:
            score -= 3
            reasons.append(f"exclude:{kw}")

    practical_tokens = [
        "tutorial",
        "guide",
        "playbook",
        "workflow",
        "implementation",
        "code",
        "open source",
        "github",
        "benchmark",
        "agent",
        "tooling",
        "eval",
        "production",
        "deployment",
        "stack",
        "prompt",
    ]
    practical_hits = sum(1 for token in practical_tokens if token in text)
    if practical_hits:
        practical_bonus = min(practical_hits, 3)
        score += practical_bonus
        reasons.append(f"practical:+{practical_bonus}")

    if "hackernews" in source_name.lower() and "show hn" in text:
        score += 1
        reasons.append("show-hn")

    return score, reasons


def classify_ai_bucket(item: dict) -> str:
    """Classify an AI item into one of fixed curation buckets."""
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    signals = " ".join(item.get("score_reasons", [])).lower()

    frontier_keywords = [
        "harness engineering",
        "context engineering",
        "vibe coding",
        "agent engineering",
        "prompt engineering",
        "eval",
        "evaluation",
        "context window",
        "memory",
        "skill",
        "workflow pattern",
        "best practice",
        "reasoning",
    ]
    engineering_keywords = [
        "production",
        "deployment",
        "architecture",
        "latency",
        "cost",
        "benchmark",
        "inference",
        "rag",
        "observability",
        "reliability",
        "security",
        "pipeline",
        "stack",
        "implementation",
        "scaling",
    ]
    tooling_keywords = [
        "sdk",
        "api",
        "release",
        "launched",
        "open source",
        "github",
        "framework",
        "langchain",
        "hugging face",
        "openai",
        "anthropic",
        "gemini",
        "cursor",
        "windsurf",
        "plugin",
        "extension",
        "tooling",
        "cli",
    ]

    if any(k in text or k in signals for k in frontier_keywords):
        return "前沿技巧"
    if any(k in text or k in signals for k in engineering_keywords):
        return "工程实践"
    if any(k in text or k in signals for k in tooling_keywords):
        return "工具链更新"
    return "工程实践"


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _parse_feed(url: str) -> object | None:
    return feedparser.parse(url)


def fetch_rss_feed(
    feed_config: dict,
    feed_cache: dict,
    today: str,
    max_papers: int = 10,
    raw_only: bool = False,
    quality_config: dict | None = None,
) -> list:
    """Fetch a single RSS feed and return item dicts."""
    quality = quality_config or {}

    max_ai_items_per_feed = int(quality.get("max_ai_items_per_feed", 8))
    min_ai_interest_score = int(quality.get("min_ai_interest_score", 0))
    validate_ielts_urls = bool(quality.get("validate_ielts_urls", True))
    ielts_request_timeout_sec = int(quality.get("ielts_request_timeout_sec", 8))

    ai_interest_topics = [
        x.lower() for x in quality.get("ai_interest_topics", DEFAULT_AI_INTEREST_TOPICS)
    ]
    ai_priority_topics = [
        x.lower() for x in quality.get("ai_priority_topics", DEFAULT_AI_PRIORITY_TOPICS)
    ]
    ai_exclude_keywords = [
        x.lower() for x in quality.get("ai_exclude_keywords", DEFAULT_AI_EXCLUDE_KEYWORDS)
    ]
    ielts_accessible_domains = [x.lower() for x in quality.get("ielts_accessible_domains", [])]

    name = feed_config["name"]
    url = feed_config["url"]
    folder = feed_config["note_folder"]
    filter_kw = [kw for kw in feed_config.get("filter_keywords", []) if kw]
    domain = str(feed_config.get("domain", "")).lower()
    is_ai_feed = domain in AI_NEWS_DOMAINS
    is_ielts_feed = domain == "ielts" or "ielts" in name.lower()

    log.info(f"[rss] Fetching: {name}")
    try:
        d = _parse_feed(url)
    except Exception as exc:
        log.error(f"[rss-fail] {name}: failed after retries ({exc})")
        return []

    is_paper_feed = "arxiv" in url.lower() or "paperswithcode" in url.lower()
    parsed_entries: list[Any] = list(getattr(d, "entries", []))
    entries = parsed_entries[:max_papers] if is_paper_feed else parsed_entries[:20]
    if not entries:
        log.info("  -> 0 items")
        return []

    results = []
    skipped_by_cache = 0
    skipped_by_ai_relevance = 0
    skipped_by_ielts_domain = 0
    skipped_by_ielts_access = 0
    pruned_by_cap = 0
    seen_urls: set[str] = set()

    for entry in entries:
        title = entry.get("title") or "Untitled"
        link = (entry.get("link") or "").strip()
        norm_link = normalize_url(link)
        if norm_link and norm_link in seen_urls:
            continue
        if norm_link:
            seen_urls.add(norm_link)

        guid = (entry.get("id") or entry.get("guid") or link or "").strip()
        summary = entry.get("summary", entry.get("description", "")) or ""
        summary = _clean_summary(summary, max_len=500)

        if not raw_only and guid and guid in feed_cache:
            skipped_by_cache += 1
            continue

        if filter_kw and not _contains_keywords(f"{title} {summary}", filter_kw):
            continue

        if is_ielts_feed and link:
            if not _is_domain_allowed(link, ielts_accessible_domains):
                skipped_by_ielts_domain += 1
                continue
            if validate_ielts_urls:
                ok, reason = _is_url_accessible_cached(link, timeout_sec=ielts_request_timeout_sec)
                if not ok:
                    skipped_by_ielts_access += 1
                    log.info(f"  [ielts-skip] inaccessible: {title[:60]} ({reason})")
                    continue

        item = {
            "title": title,
            "link": link,
            "guid": guid,
            "summary": summary,
            "folder": folder,
        }

        if is_ai_feed:
            score, score_reasons = _score_ai_interest(
                title,
                summary,
                source_name=name,
                ai_interest_topics=ai_interest_topics,
                ai_priority_topics=ai_priority_topics,
                ai_exclude_keywords=ai_exclude_keywords,
            )
            if score < min_ai_interest_score:
                skipped_by_ai_relevance += 1
                continue
            item["score"] = score
            item["score_reasons"] = score_reasons[:6]
            item["ai_bucket"] = classify_ai_bucket(item)

        results.append(item)

    if is_ai_feed and results:
        results.sort(key=lambda x: (x.get("score", 0), x.get("title", "")), reverse=True)
        if max_ai_items_per_feed > 0 and len(results) > max_ai_items_per_feed:
            pruned_by_cap = len(results) - max_ai_items_per_feed
            results = results[:max_ai_items_per_feed]

    if not raw_only:
        for item in results:
            final_guid = (item.get("guid") or "").strip()
            if final_guid:
                feed_cache[final_guid] = today

    log.info(
        "  -> %s items (cache=%s, low-score=%s, ielts-domain=%s, ielts-access=%s, pruned=%s)",
        len(results),
        skipped_by_cache,
        skipped_by_ai_relevance,
        skipped_by_ielts_domain,
        skipped_by_ielts_access,
        pruned_by_cap,
    )
    return results


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _parse_youtube_feed(channel_id: str) -> object | None:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    return feedparser.parse(url)


def fetch_youtube_channel(
    channel: dict,
    feed_cache: dict,
    today: str,
    max_videos: int = 3,
) -> list:
    """Fetch latest videos from a YouTube channel via RSS."""
    name = channel["name"]
    channel_id = channel["channel_id"]
    folder = channel["note_folder"]
    domain = channel["domain"]

    log.info(f"[YouTube] Fetching: {name}")
    try:
        d = _parse_youtube_feed(channel_id)
    except Exception as exc:
        log.error(f"[yt-fail] {name}: failed after retries ({exc})")
        return []

    parsed_entries: list[Any] = list(getattr(d, "entries", []))
    results = []
    for entry in parsed_entries[:max_videos]:
        title = entry.get("title") or "Untitled"
        link = (entry.get("link") or "").strip()
        guid = (entry.get("id") or entry.get("guid") or link or "").strip()
        published = (entry.get("published") or today)[:10]
        summary = entry.get("summary", "") or ""
        summary = _clean_summary(summary, max_len=400)

        if guid and guid in feed_cache:
            log.info(f"  [yt-cached] {title[:50]}")
            continue
        if guid:
            feed_cache[guid] = today

        results.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "published": published,
                "summary": summary,
                "channel_name": name,
                "domain": domain,
                "folder": folder,
            }
        )

    log.info(f"  -> {len(results)} new videos from {name}")
    return results


def fetch_youtube_channel_raw(channel: dict, max_videos: int = 2) -> list:
    """Fetch YouTube videos in raw mode (no cache dedup) for Agent curation."""
    name = channel["name"]
    channel_id = channel["channel_id"]

    try:
        d = _parse_youtube_feed(channel_id)
    except Exception as exc:
        log.error(f"[yt-fail] {name}: failed after retries (raw mode) ({exc})")
        return []

    parsed_entries: list[Any] = list(getattr(d, "entries", []))
    results = []
    for entry in parsed_entries[:max_videos]:
        title = entry.get("title") or "Untitled"
        link = (entry.get("link") or "").strip()
        published = (entry.get("published") or "")[:10]
        summary = entry.get("summary", "") or ""
        summary = _clean_summary(summary, max_len=300)
        results.append(
            {
                "channel_name": name,
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
            }
        )

    log.info(f"  [ok] {name}: {len(results)} videos (raw)")
    return results
