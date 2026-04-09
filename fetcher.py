#!/usr/bin/env python3
"""
fetcher.py - Feed Fetching Layer
Handles all data retrieval: RSS feeds and YouTube channels.
Includes retry with exponential backoff via tenacity.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser
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
AI_SCORING_CONTENT_TYPES = {"news", "tweet", "engineering", "tooling", "community"}

AI_BUCKET_FRONTIER = "frontier"
AI_BUCKET_PRACTICE = "practice"
AI_BUCKET_TOOLING = "tooling"

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


def infer_content_type(source_name: str, url: str, domain: str, fallback: str = "news") -> str:
    source_lower = source_name.lower()
    url_lower = url.lower()
    domain_lower = domain.lower()

    # Hard signals override generic defaults.
    if "arxiv" in url_lower or "paperswithcode" in url_lower or domain_lower == "research":
        return "paper"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "video"
    if any(
        token in url_lower for token in ("twitter.com", "x.com", "nitter.net", "rsshub.app/twitter")
    ):
        return "tweet"

    value = (fallback or "").strip().lower()
    if value and value != "news":
        return value

    if any(
        token in source_lower
        for token in ("engineering", "hackernews", "hacker news", "github", "playbook", "dev")
    ):
        return "engineering"
    if "tool" in source_lower or domain_lower == "tooling":
        return "tooling"
    return value or "news"


def _contains_keywords(text: str, keywords: Iterable[str]) -> bool:
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)


def ai_filter(text: str) -> bool:
    return _contains_keywords(text, AI_KEYWORDS)


def _clean_summary(raw_text: str, max_len: int) -> str:
    return re.sub(r"<[^>]+>", "", raw_text).strip()[:max_len]


def _extract_entry_date(entry: Any) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return time.strftime("%Y-%m-%d", parsed)
        except Exception:
            pass

    for key in ("published", "updated", "pubDate"):
        raw = str(entry.get(key, "")).strip()
        if not raw:
            continue
        m = re.search(r"\d{4}-\d{2}-\d{2}", raw)
        if m:
            return m.group(0)
        m = re.search(r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", raw)
        if m:
            try:
                return time.strftime("%Y-%m-%d", time.strptime(m.group(0), "%d %b %Y"))
            except ValueError:
                continue
    return ""


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


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
        return AI_BUCKET_FRONTIER
    if any(k in text or k in signals for k in engineering_keywords):
        return AI_BUCKET_PRACTICE
    if any(k in text or k in signals for k in tooling_keywords):
        return AI_BUCKET_TOOLING
    return AI_BUCKET_PRACTICE


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

    ai_interest_topics = [
        x.lower() for x in quality.get("ai_interest_topics", DEFAULT_AI_INTEREST_TOPICS)
    ]
    ai_priority_topics = [
        x.lower() for x in quality.get("ai_priority_topics", DEFAULT_AI_PRIORITY_TOPICS)
    ]
    ai_exclude_keywords = [
        x.lower() for x in quality.get("ai_exclude_keywords", DEFAULT_AI_EXCLUDE_KEYWORDS)
    ]

    name = feed_config["name"]
    url = feed_config["url"]
    folder = feed_config["note_folder"]
    filter_kw = [kw for kw in feed_config.get("filter_keywords", []) if kw]
    domain = str(feed_config.get("domain", "")).lower()
    content_type = infer_content_type(
        source_name=name,
        url=url,
        domain=domain,
        fallback=str(feed_config.get("content_type", "")).lower(),
    )
    is_ai_feed = domain in AI_NEWS_DOMAINS or content_type in AI_SCORING_CONTENT_TYPES

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
        published = _extract_entry_date(entry)

        if not raw_only and guid and guid in feed_cache:
            skipped_by_cache += 1
            continue

        if filter_kw and not _contains_keywords(f"{title} {summary}", filter_kw):
            continue

        item = {
            "title": title,
            "link": link,
            "guid": guid,
            "published": published,
            "summary": summary,
            "folder": folder,
            "content_type": content_type,
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
        "  -> %s items (cache=%s, low-score=%s, pruned=%s)",
        len(results),
        skipped_by_cache,
        skipped_by_ai_relevance,
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
                "content_type": "video",
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
                "content_type": "video",
            }
        )

    log.info(f"  [ok] {name}: {len(results)} videos (raw)")
    return results
