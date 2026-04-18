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
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests
from requests.adapters import HTTPAdapter
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from urllib3.util.retry import Retry

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

AI_SCORING_DOMAINS = {"ai-news", "solopreneur", "ai-company", "research"}
AI_SCORING_CONTENT_TYPES = {"tweet", "engineering", "tooling", "community", "paper"}

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

TWEET_FALLBACK_HOSTS = (
    "nitter.net",
    "nitter.poast.org",
    "nitter.privacydev.net",
)
FULLTEXT_FETCH_TIMEOUT_SECONDS = 5
FULLTEXT_FETCH_MAX_CHARS = 2400
FULLTEXT_MIN_CHARS = 280
FEED_REQUEST_TIMEOUT_SECONDS = 12
FEED_HTTP_RETRIES = 2
FEED_HTTP_BACKOFF_SECONDS = 0.4
FULLTEXT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
FEED_HEADERS = {
    "User-Agent": FULLTEXT_HEADERS["User-Agent"],
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
}


def _build_http_session() -> requests.Session:
    """Create a shared HTTP session with retry for flaky endpoints."""
    retry = Retry(
        total=max(FEED_HTTP_RETRIES, 0),
        connect=max(FEED_HTTP_RETRIES, 0),
        read=max(FEED_HTTP_RETRIES, 0),
        status=max(FEED_HTTP_RETRIES, 0),
        backoff_factor=max(FEED_HTTP_BACKOFF_SECONDS, 0.0),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(FEED_HEADERS)
    return session


HTTP_SESSION = _build_http_session()


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
    text = re.sub(r"<[^>]+>", " ", raw_text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _extract_primary_text_from_html(raw_html: str, max_chars: int) -> str:
    """Best-effort extraction without external parser dependencies."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    article_matches = re.findall(r"(?is)<(article|main)[^>]*>(.*?)</\1>", html)
    if article_matches:
        body = max((match[1] for match in article_matches), key=len)
    else:
        body_match = re.search(r"(?is)<body[^>]*>(.*?)</body>", html)
        body = body_match.group(1) if body_match else html
    body = re.sub(r"(?is)<br\s*/?>", "\n", body)
    body = re.sub(r"(?is)</(p|div|li|h[1-6]|blockquote)>", "\n", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{2,}", "\n", body)
    return body.strip()[:max_chars]


def _fetch_article_fulltext(url: str, max_chars: int = FULLTEXT_FETCH_MAX_CHARS) -> str:
    """Fetch article HTML and extract readable text for higher-fidelity summarization."""
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        response = HTTP_SESSION.get(
            url,
            timeout=FULLTEXT_FETCH_TIMEOUT_SECONDS,
            headers=FULLTEXT_HEADERS,
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and "xml" not in content_type:
            return ""
        extracted = _extract_primary_text_from_html(response.text, max_chars=max_chars)
        if len(extracted) < FULLTEXT_MIN_CHARS:
            return ""
        return extracted
    except Exception as exc:
        log.debug(f"fulltext.fetch.fail | url={url} error={exc}")
        return ""


def _tweet_feed_urls_with_fallback(
    url: str, content_type: str, configured_fallback_urls: list[str] | None = None
) -> list[str]:
    if content_type != "tweet":
        return [url]
    normalized = url.strip()
    lower = normalized.lower()
    urls = [normalized]
    for configured in configured_fallback_urls or []:
        if configured:
            urls.append(configured.strip())
    marker = "rsshub.app/twitter/user/"
    if marker in lower:
        user = normalized.split(marker, 1)[1].split("?")[0].strip("/ ")
        if user:
            for host in TWEET_FALLBACK_HOSTS:
                urls.append(f"https://{host}/{user}/rss")
    # keep order while deduping
    deduped: list[str] = []
    for candidate in urls:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _parse_feed_with_fallback(
    name: str, candidate_urls: list[str]
) -> tuple[object | None, str, dict[str, Any]]:
    last_parsed: object | None = None
    last_url = candidate_urls[0] if candidate_urls else ""
    parse_notes: list[str] = []

    def _meta(mode: str, status: str = "ok") -> dict[str, Any]:
        return {
            "mode": mode,
            "status": status,
            "notes": "; ".join(parse_notes)[:320],
        }

    if not candidate_urls:
        return None, "", _meta("none", status="error")

    for idx, candidate_url in enumerate(candidate_urls):
        direct_warning = ""
        try:
            parsed = _parse_feed(candidate_url)
        except Exception as exc:
            parse_notes.append(f"direct-exception={candidate_url}:{exc}")
            log.warning(f"rss.parse.fail | feed={name} url={candidate_url} error={exc}")
            continue

        last_parsed = parsed
        last_url = candidate_url
        if getattr(parsed, "bozo", False):
            direct_warning = str(getattr(parsed, "bozo_exception", "feed parse warning"))
            parse_notes.append(f"bozo={candidate_url}:{direct_warning}")
        entries = list(getattr(parsed, "entries", []))
        if entries:
            mode = "direct" if idx == 0 else "fallback-url"
            status = "warn" if direct_warning or idx > 0 else "ok"
            return parsed, candidate_url, _meta(mode, status=status)

        # HTTP-content fallback for feeds that parse empty directly.
        try:
            response = HTTP_SESSION.get(
                candidate_url,
                allow_redirects=True,
                timeout=FEED_REQUEST_TIMEOUT_SECONDS,
            )
            status_code = int(response.status_code)
            if status_code >= 400:
                parse_notes.append(f"http-status={candidate_url}:{status_code}")
            else:
                parsed_http = feedparser.parse(response.content)
                if getattr(parsed_http, "bozo", False):
                    bozo = str(getattr(parsed_http, "bozo_exception", "feed parse warning"))
                    parse_notes.append(f"http-bozo={candidate_url}:{bozo}")
                http_entries = list(getattr(parsed_http, "entries", []))
                if http_entries:
                    mode = f"fallback-http-{status_code}"
                    status = "warn" if idx > 0 or direct_warning else "ok"
                    return parsed_http, candidate_url, _meta(mode, status=status)
        except Exception as exc:
            parse_notes.append(f"http-exception={candidate_url}:{exc}")

        if idx < len(candidate_urls) - 1:
            log.info(f"rss.empty.fallback | feed={name} url={candidate_url}")

    status = "warn" if last_parsed is not None else "error"
    return last_parsed, last_url, _meta("direct-empty", status=status)


def _freshness_adjustment(published: str, today: str) -> tuple[int, str]:
    try:
        published_date = datetime.strptime(published[:10], "%Y-%m-%d").date()
        today_date = datetime.strptime(today[:10], "%Y-%m-%d").date()
    except Exception:
        return 0, "fresh:unknown"
    delta = (today_date - published_date).days
    if delta <= 2:
        return 2, "fresh:d0-2"
    if delta <= 7:
        return 1, "fresh:d3-7"
    if delta <= 14:
        return 0, "fresh:d8-14"
    if delta <= 30:
        return -1, "fresh:d15-30"
    return -3, "fresh:stale"


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
    return_meta: bool = False,
) -> list | tuple[list, dict[str, Any]]:
    """Fetch a single RSS feed and return items (and optional health metadata)."""
    quality = quality_config or {}

    max_ai_items_per_feed = int(quality.get("max_ai_items_per_feed", 8))
    min_ai_interest_score = int(quality.get("min_ai_interest_score", 0))
    enable_fulltext_enrichment = bool(quality.get("enable_fulltext_enrichment", True))
    fulltext_enrichment_per_feed = int(quality.get("fulltext_enrichment_per_feed", 5))

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
    is_ai_feed = domain in AI_SCORING_DOMAINS or content_type in AI_SCORING_CONTENT_TYPES

    feed_candidate_urls = _tweet_feed_urls_with_fallback(
        url,
        content_type,
        configured_fallback_urls=feed_config.get("fallback_urls", []),
    )
    log.info(f"[rss] Fetching: {name}")
    d, resolved_url, fetch_meta = _parse_feed_with_fallback(name, feed_candidate_urls)
    fetch_mode = str(fetch_meta.get("mode", "direct"))
    fetch_status = str(fetch_meta.get("status", "ok"))
    fetch_notes = str(fetch_meta.get("notes", ""))
    if fetch_mode != "direct":
        log.info(f"rss.fallback.used | feed={name} mode={fetch_mode} resolved_url={resolved_url}")
    if d is None:
        log.error(f"[rss-fail] {name}: all feed endpoints failed")
        detail = "; ".join(
            x
            for x in [
                f"mode={fetch_mode}",
                f"resolved={resolved_url}",
                fetch_notes,
            ]
            if x
        )
        meta = {"status": "error", "detail": detail[:320], "mode": fetch_mode}
        return ([], meta) if return_meta else []

    is_paper_feed = "arxiv" in resolved_url.lower() or "paperswithcode" in resolved_url.lower()
    parsed_entries: list[Any] = list(getattr(d, "entries", []))
    entries = parsed_entries[:max_papers] if is_paper_feed else parsed_entries[:20]
    if not entries:
        log.info("  -> 0 items")
        detail = "; ".join(
            x
            for x in [
                f"mode={fetch_mode}",
                f"resolved={resolved_url}",
                f"content_type={content_type}",
                fetch_notes,
            ]
            if x
        )
        meta = {"status": "warn", "detail": detail[:320], "mode": fetch_mode}
        return ([], meta) if return_meta else []

    results = []
    skipped_by_cache = 0
    skipped_by_ai_relevance = 0
    skipped_by_filter = 0
    pruned_by_cap = 0
    enriched_by_fulltext = 0
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
        summary = _clean_summary(summary, max_len=700)
        published = _extract_entry_date(entry)

        if not raw_only and guid and guid in feed_cache:
            skipped_by_cache += 1
            continue

        if filter_kw and not _contains_keywords(f"{title} {summary}", filter_kw):
            skipped_by_filter += 1
            continue

        item = {
            "title": title,
            "link": link,
            "guid": guid,
            "published": published,
            "summary": summary,
            "folder": folder,
            "domain": domain,
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
            freshness_delta, freshness_reason = _freshness_adjustment(published, today)
            score += freshness_delta
            score_reasons.append(freshness_reason)
            if score < min_ai_interest_score:
                skipped_by_ai_relevance += 1
                continue

            can_enrich = (
                enable_fulltext_enrichment
                and not raw_only
                and content_type in {"news", "engineering", "tooling", "community", "paper"}
                and enriched_by_fulltext < fulltext_enrichment_per_feed
            )
            if can_enrich:
                full_text = _fetch_article_fulltext(link)
                if full_text:
                    summary = _clean_summary(
                        f"{summary}\n{full_text}", max_len=FULLTEXT_FETCH_MAX_CHARS
                    )
                    enriched_by_fulltext += 1
                    score, score_reasons = _score_ai_interest(
                        title,
                        summary,
                        source_name=name,
                        ai_interest_topics=ai_interest_topics,
                        ai_priority_topics=ai_priority_topics,
                        ai_exclude_keywords=ai_exclude_keywords,
                    )
                    freshness_delta, freshness_reason = _freshness_adjustment(published, today)
                    score += freshness_delta
                    score_reasons.append(freshness_reason)
                    if score < min_ai_interest_score:
                        skipped_by_ai_relevance += 1
                        continue

            item["summary"] = summary
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
        "  -> %s items (mode=%s, cache=%s, low-score=%s, filtered=%s, pruned=%s, enriched=%s)",
        len(results),
        fetch_mode,
        skipped_by_cache,
        skipped_by_ai_relevance,
        skipped_by_filter,
        pruned_by_cap,
        enriched_by_fulltext,
    )
    status = fetch_status
    if status == "ok" and (fetch_mode != "direct" or skipped_by_ai_relevance > 0):
        status = "warn"
    detail = "; ".join(
        x
        for x in [
            f"mode={fetch_mode}",
            f"resolved={resolved_url}",
            f"cache={skipped_by_cache}",
            f"low_score={skipped_by_ai_relevance}",
            f"filtered={skipped_by_filter}",
            f"pruned={pruned_by_cap}",
            f"enriched={enriched_by_fulltext}",
            fetch_notes,
        ]
        if x
    )
    meta = {"status": status, "detail": detail[:320], "mode": fetch_mode}
    if return_meta:
        return results, meta
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
