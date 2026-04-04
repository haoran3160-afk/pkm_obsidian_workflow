#!/usr/bin/env python3
"""
fetcher.py — Feed Fetching Layer
Handles all data retrieval: RSS feeds and YouTube channels.
Includes automatic retry with exponential backoff via tenacity.
"""

import logging
import re
from collections.abc import Iterable

import feedparser
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("pkm.fetcher")


# ── AI Keyword Filter ─────────────────────────────────────────────────────────

AI_KEYWORDS = [
    "ai", "llm", "gpt", "machine learning", "neural", "deep learning",
    "openai", "anthropic", "gemini", "transformer", "model", "inference",
    "fine-tun", "rag", "agent", "benchmark", "arxiv",
]


def _contains_keywords(text: str, keywords: Iterable[str]) -> bool:
    """Return True if text contains at least one keyword (case-insensitive)."""
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)


def ai_filter(text: str) -> bool:
    """Return True if text contains any AI-related keyword."""
    return _contains_keywords(text, AI_KEYWORDS)


def _clean_summary(raw_text: str, max_len: int) -> str:
    """Strip HTML and trim long summaries for markdown readability."""
    return re.sub(r"<[^>]+>", "", raw_text).strip()[:max_len]


# ── RSS Fetcher ───────────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _parse_feed(url: str) -> object | None:
    """Parse an RSS/Atom feed URL. Retried up to 3 times on network errors."""
    return feedparser.parse(url)


def fetch_rss_feed(
    feed_config: dict,
    feed_cache: dict,
    today: str,
    max_papers: int = 10,
    raw_only: bool = False,
) -> list:
    """Fetch a single RSS feed and return a list of article dicts.

    Args:
        feed_config:  Feed configuration entry from pkm_config.json.
        feed_cache:   Mutable dict mapping GUID → date (dedup cache).
        today:        Current date string YYYY-MM-DD.
        max_papers:   Max number of items for arXiv/PapersWithCode feeds.
        raw_only:     If True, skip cache filtering (Agent curates from full set).

    Returns:
        List of dicts with keys: title, link, guid, summary, folder.
    """
    name = feed_config["name"]
    url = feed_config["url"]
    folder = feed_config["note_folder"]
    filter_kw = [kw for kw in feed_config.get("filter_keywords", []) if kw]

    log.info(f"[rss] Fetching: {name}")
    try:
        d = _parse_feed(url)
    except Exception as exc:
        log.error(f"[rss-fail] {name}: failed after retries ({exc})")
        return []

    is_paper_feed = "arxiv" in url.lower() or "paperswithcode" in url.lower()
    entries = d.entries[:max_papers] if is_paper_feed else d.entries[:20]
    if not entries:
        log.info("  → 0 items")
        return []

    results = []
    skipped = 0

    for entry in entries:
        title   = entry.get("title") or "Untitled"
        link    = (entry.get("link") or "").strip()
        guid    = (entry.get("id") or entry.get("guid") or link or "").strip()
        summary = entry.get("summary", entry.get("description", "")) or ""
        summary = _clean_summary(summary, max_len=500)

        # Cache dedup — only in full mode (not raw_only)
        if not raw_only and guid and guid in feed_cache:
            skipped += 1
            continue

        # HackerNews AI keyword gate (applies in all modes)
        if filter_kw and not _contains_keywords(f"{title} {summary}", filter_kw):
            continue

        results.append({
            "title":   title,
            "link":    link,
            "guid":    guid,
            "summary": summary,
            "folder":  folder,
        })

        # Mark as seen only when writing permanent notes
        if not raw_only and guid:
            feed_cache[guid] = today

    log.info(f"  → {len(results)} items ({skipped} skipped by cache)")
    return results


# ── YouTube Fetcher ───────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _parse_youtube_feed(channel_id: str) -> object | None:
    """Fetch a YouTube channel RSS feed. Retried up to 3 times."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    return feedparser.parse(url)


def fetch_youtube_channel(
    channel: dict,
    feed_cache: dict,
    today: str,
    max_videos: int = 3,
) -> list:
    """Fetch latest videos from a YouTube channel via RSS.

    Returns:
        List of dicts with keys: title, link, guid, published, summary,
        channel_name, domain, folder.
    """
    name       = channel["name"]
    channel_id = channel["channel_id"]
    folder     = channel["note_folder"]
    domain     = channel["domain"]

    log.info(f"[YouTube] Fetching: {name}")
    try:
        d = _parse_youtube_feed(channel_id)
    except Exception as exc:
        log.error(f"[yt-fail] {name}: failed after retries ({exc})")
        return []

    results = []
    for entry in d.entries[:max_videos]:
        title     = entry.get("title") or "Untitled"
        link      = (entry.get("link") or "").strip()
        guid      = (entry.get("id") or entry.get("guid") or link or "").strip()
        published = (entry.get("published") or today)[:10]
        summary   = entry.get("summary", "") or ""
        summary   = _clean_summary(summary, max_len=400)

        if guid and guid in feed_cache:
            log.info(f"  [yt-cached] {title[:50]}")
            continue
        if guid:
            feed_cache[guid] = today

        results.append({
            "title":        title,
            "link":         link,
            "guid":         guid,
            "published":    published,
            "summary":      summary,
            "channel_name": name,
            "domain":       domain,
            "folder":       folder,
        })

    log.info(f"  → {len(results)} new videos from {name}")
    return results


def fetch_youtube_channel_raw(channel: dict, max_videos: int = 2) -> list:
    """Fetch YouTube videos in raw mode (no cache dedup) for Agent curation.

    Returns a minimal list of dicts suitable for appending to Raw-Daily-Feeds.md.
    """
    name       = channel["name"]
    channel_id = channel["channel_id"]

    try:
        d = _parse_youtube_feed(channel_id)
    except Exception as exc:
        log.error(f"[yt-fail] {name}: failed after retries (raw mode) ({exc})")
        return []

    results = []
    for entry in d.entries[:max_videos]:
        title     = entry.get("title") or "Untitled"
        link      = (entry.get("link") or "").strip()
        published = (entry.get("published") or "")[:10]
        summary   = entry.get("summary", "") or ""
        summary   = _clean_summary(summary, max_len=300)
        results.append({
            "channel_name": name,
            "title":        title,
            "link":         link,
            "published":    published,
            "summary":      summary,
        })

    log.info(f"  [✓] {name}: {len(results)} videos (raw)")
    return results
