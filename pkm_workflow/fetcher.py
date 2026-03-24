#!/usr/bin/env python3
"""
fetcher.py — Feed Fetching Layer
Handles all data retrieval: RSS feeds and YouTube channels.
Includes automatic retry with exponential backoff via tenacity.
"""

import logging
import re
from typing import Optional

import feedparser
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

log = logging.getLogger("pkm.fetcher")


# ── AI Keyword Filter ─────────────────────────────────────────────────────────

AI_KEYWORDS = [
    "ai", "llm", "gpt", "machine learning", "neural", "deep learning",
    "openai", "anthropic", "gemini", "transformer", "model", "inference",
    "fine-tun", "rag", "agent", "benchmark", "arxiv",
]


def ai_filter(text: str) -> bool:
    """Return True if text contains any AI-related keyword."""
    text_lower = text.lower()
    return any(k in text_lower for k in AI_KEYWORDS)


# ── RSS Fetcher ───────────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=False,
)
def _parse_feed(url: str) -> Optional[object]:
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
    filter_kw = feed_config.get("filter_keywords", [])

    log.info(f"[rss] Fetching: {name}")
    d = _parse_feed(url)
    if d is None:
        log.error(f"[rss-fail] {name}: failed after retries")
        return []

    is_paper_feed = "arxiv" in url.lower() or "paperswithcode" in url.lower()
    entries = d.entries[:max_papers] if is_paper_feed else d.entries[:20]

    results = []
    skipped = 0

    for entry in entries:
        title   = entry.get("title", "Untitled")
        link    = entry.get("link", "")
        guid    = entry.get("id", link).strip()
        summary = entry.get("summary", entry.get("description", ""))
        summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]

        # Cache dedup — only in full mode (not raw_only)
        if not raw_only and guid and guid in feed_cache:
            skipped += 1
            continue

        # HackerNews AI keyword gate (applies in all modes)
        if filter_kw and not ai_filter(title + " " + summary):
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
    reraise=False,
)
def _parse_youtube_feed(channel_id: str) -> Optional[object]:
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
    d = _parse_youtube_feed(channel_id)
    if d is None:
        log.error(f"[yt-fail] {name}: failed after retries")
        return []

    results = []
    for entry in d.entries[:max_videos]:
        title     = entry.get("title", "Untitled")
        link      = entry.get("link", "")
        guid      = entry.get("id", link).strip()
        published = entry.get("published", today)[:10]
        summary   = entry.get("summary", "")
        summary   = re.sub(r"<[^>]+>", "", summary).strip()[:400]

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

    d = _parse_youtube_feed(channel_id)
    if d is None:
        log.error(f"[yt-fail] {name}: failed after retries (raw mode)")
        return []

    results = []
    for entry in d.entries[:max_videos]:
        title     = entry.get("title", "Untitled")
        link      = entry.get("link", "")
        published = entry.get("published", "")[:10]
        summary   = entry.get("summary", "")
        summary   = re.sub(r"<[^>]+>", "", summary).strip()[:300]
        results.append({
            "channel_name": name,
            "title":        title,
            "link":         link,
            "published":    published,
            "summary":      summary,
        })

    log.info(f"  [✓] {name}: {len(results)} videos (raw)")
    return results
