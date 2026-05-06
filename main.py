#!/usr/bin/env python3
"""
main.py - Obsidian PKM Workflow Orchestrator
Coordinates fetching, formatting, and writing of daily digests and feeds.
Usage:
  python main.py
  python main.py --test
  python main.py --dry-run
  python main.py --raw-only
  python main.py --schedule
  python main.py --doctor
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import structlog
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

import daily_curation
import fetcher
import formatter
import llm_digest
import state_schema
import writer
from config_schema import PKMConfig, load_and_validate

# 鈹€鈹€ Console / Logging Setup 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

SCRIPT_DIR = Path(__file__).parent
LOG_PATH = SCRIPT_DIR / "fetch.log"
console = Console()

# Force UTF-8 stdout
stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(stdout_reconfigure):
    stdout_reconfigure(encoding="utf-8", errors="replace")

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(
        file=open(LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
    ),
)
log = structlog.get_logger("pkm")

# 鈹€鈹€ Load Config + Environment 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

load_dotenv(SCRIPT_DIR / ".env")

CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"
CACHE_PATH = SCRIPT_DIR / "feed_cache.json"
USED_ARTICLES_PATH = SCRIPT_DIR / "used_articles.json"
SOURCE_ROTATION_PATH = SCRIPT_DIR / "source_rotation.json"
SOURCE_HEALTH_PATH = SCRIPT_DIR / "source_health.json"

# Load and validate config via Pydantic schema
try:
    CONFIG: PKMConfig = load_and_validate(CONFIG_PATH)
except (FileNotFoundError, ValueError) as _cfg_err:
    console.print(f"[bold red]Configuration Error:[/] {_cfg_err}")
    sys.exit(1)

VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "YOUR_VAULT_PATH")
CACHE_EXPIRY_DAYS = int(os.getenv("FEED_CACHE_EXPIRY_DAYS", "7"))
RAW_FEED_KEEP_DAYS = int(os.getenv("RAW_FEED_KEEP_DAYS", "7"))
MAX_PAPERS = CONFIG.max_papers_per_day
MAX_VIDEOS = CONFIG.max_videos_per_channel
MAX_PAPER_NOTES_PER_DAY = CONFIG.max_paper_notes_per_day
MAX_VIDEO_NOTES_PER_DAY = CONFIG.max_video_notes_per_day
DAILY_DIGEST_ONLY_OUTPUT = CONFIG.daily_digest_only_output
DAILY_DIGEST_TOP_PICKS = CONFIG.daily_digest_top_picks
DAILY_DIGEST_MAX_ITEMS_PER_SOURCE = CONFIG.daily_digest_max_items_per_source
DAILY_DIGEST_ACTION_ITEMS = CONFIG.daily_digest_action_items
DAILY_DIGEST_MAX_DEFERRED_ITEMS = CONFIG.daily_digest_max_deferred_items
DAILY_DIGEST_INCLUDE_MINDMAP = CONFIG.daily_digest_include_mindmap
DAILY_DIGEST_INCLUDE_COGNITIVE_LENSES = CONFIG.daily_digest_include_cognitive_lenses
DAILY_DIGEST_COGNITIVE_QUESTIONS = CONFIG.daily_digest_cognitive_questions
DAILY_DIGEST_QUALITY_GATE_ENABLED = CONFIG.daily_digest_quality_gate_enabled
DAILY_DIGEST_TLDR_MIN_QUALITY_SCORE = CONFIG.daily_digest_tldr_min_quality_score
DAILY_DIGEST_TLDR_MAX_UNDISCLOSED = CONFIG.daily_digest_tldr_max_undisclosed
DAILY_DIGEST_TLDR_MIN_ITEMS = CONFIG.daily_digest_tldr_min_items
DAILY_DIGEST_TLDR_MIN_HARD_SIGNAL_RATIO = CONFIG.daily_digest_tldr_min_hard_signal_ratio
DAILY_DIGEST_TLDR_MAX_UNDISCLOSED_RATIO = CONFIG.daily_digest_tldr_max_undisclosed_ratio
DAILY_DIGEST_MIN_TOP_NONPAPER = CONFIG.daily_digest_min_top_nonpaper
DAILY_DIGEST_MIN_TOP_CONTENT_TYPES = CONFIG.daily_digest_min_top_content_types
DAILY_DIGEST_MAX_PAPER_IN_TOP = CONFIG.daily_digest_max_paper_in_top
WRITE_MODE = os.getenv("PKM_WRITE_MODE", CONFIG.write_mode)

QUALITY_CONFIG = {
    "max_ai_items_per_feed": CONFIG.max_ai_items_per_feed,
    "min_ai_interest_score": CONFIG.min_ai_interest_score,
    "enable_fulltext_enrichment": CONFIG.enable_fulltext_enrichment,
    "fulltext_enrichment_per_feed": CONFIG.fulltext_enrichment_per_feed,
    "ai_interest_topics": CONFIG.ai_interest_topics,
    "ai_priority_topics": CONFIG.ai_priority_topics,
    "ai_exclude_keywords": CONFIG.ai_exclude_keywords,
}

if VAULT_PATH == "YOUR_VAULT_PATH":
    log.warning(
        "event", message="OBSIDIAN_VAULT_PATH not set in .env. Files won't be saved correctly!"
    )

# 鈹€鈹€ Cache Management 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def load_feed_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(_read_json_text(CACHE_PATH))
        cutoff = (datetime.now() - timedelta(days=CACHE_EXPIRY_DAYS)).strftime("%Y-%m-%d")
        return {guid: date for guid, date in data.items() if date >= cutoff}
    except Exception as e:
        log.error("cache.load.fail", error=str(e))
        return {}


def save_feed_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("cache.save.fail", error=str(e))


def _load_json_file(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(_read_json_text(path))
    except Exception:
        return default


def _read_json_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        text = path.read_text(encoding="utf-8-sig")
    return text.lstrip("\ufeff")


def _write_json_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _compact_used_articles(path: Path, retention_days: int) -> None:
    compacted = state_schema.compact_used_articles_state(path, retention_days)
    log.info("used_articles.compacted", size=len(compacted.articles), retention_days=retention_days)


def _refresh_source_rotation_week(path: Path) -> None:
    today = formatter.today_str()
    previous = state_schema.load_source_rotation_state(path)
    previous_week_start = previous.weekly_summary.week_start
    state = state_schema.refresh_source_rotation_week(path, today)
    current_week_start = state_schema.week_start_for_date(today)
    if (
        previous_week_start != current_week_start
        and state.weekly_summary.week_start == current_week_start
    ):
        log.info("source_rotation.week_reset", week_start=state.weekly_summary.week_start)


def _record_source_health(
    report: dict[str, Any], source: str, kind: str, status: str, item_count: int, detail: str = ""
) -> None:
    report["source_health_entries"].append(
        {
            "timestamp": formatter.now_str(),
            "source": source,
            "kind": kind,
            "status": status,
            "item_count": item_count,
            "detail": detail[:320],
        }
    )


def _save_source_health(report: dict[str, Any]) -> None:
    state_schema.append_source_health_run(
        SOURCE_HEALTH_PATH,
        run_date=formatter.today_str(),
        run_at=formatter.now_str(),
        entries=report.get("source_health_entries", []),
        keep_runs=CONFIG.source_health_keep_runs,
    )
    log.info("source_health.saved", entries=len(report.get("source_health_entries", [])))


def _collect_items(
    *,
    report: dict[str, Any],
    feed_cache: dict[str, str],
    today: str,
    raw_only: bool,
) -> dict[str, Any]:
    news_items: dict[str, list[dict]] = defaultdict(list)
    paper_candidates: list[dict] = []
    video_candidates: list[dict] = []

    for feed in CONFIG.rss_feeds:
        if not feed.enabled:
            log.info("rss.skipped", feed=feed.name, reason="enabled=false")
            continue

        t0 = time.monotonic()
        rss_meta: dict[str, Any] = {"status": "ok", "detail": "", "mode": "direct"}
        try:
            fetched = fetcher.fetch_rss_feed(
                feed.model_dump(),
                feed_cache,
                today,
                MAX_PAPERS,
                raw_only,
                quality_config=QUALITY_CONFIG,
                return_meta=True,
            )
            if isinstance(fetched, tuple):
                items, rss_meta = fetched
            else:
                items = fetched
            ok = True
        except Exception as exc:
            log.error("rss.fail", feed=feed.name, error=str(exc))
            report["rss_sources"].append(
                {"name": feed.name, "items": 0, "ok": False, "elapsed": time.monotonic() - t0}
            )
            _record_source_health(report, feed.name, "rss", "error", 0, str(exc))
            continue

        report["rss_sources"].append(
            {"name": feed.name, "items": len(items), "ok": ok, "elapsed": time.monotonic() - t0}
        )
        _record_source_health(
            report,
            feed.name,
            "rss",
            str(rss_meta.get("status", "ok")),
            len(items),
            str(rss_meta.get("detail", "")),
        )

        if _is_paper_feed(feed.url):
            if raw_only:
                news_items[feed.name].extend(items)
            else:
                for paper in items:
                    paper_candidates.append({**paper, "_source_name": feed.name})
        else:
            news_items[feed.name].extend(items)

    if raw_only:
        yt_raw_list: list[dict] = []
        for channel in CONFIG.youtube_channels:
            if not channel.enabled:
                log.info("youtube.skipped", channel=channel.name, reason="enabled=false")
                continue

            t0 = time.monotonic()
            try:
                videos = fetcher.fetch_youtube_channel_raw(channel.model_dump())
                ok = True
            except Exception as exc:
                log.error("yt.fail", channel=channel.name, error=str(exc))
                report["yt_sources"].append(
                    {
                        "name": channel.name,
                        "items": 0,
                        "ok": False,
                        "elapsed": time.monotonic() - t0,
                    }
                )
                _record_source_health(report, channel.name, "youtube", "error", 0, str(exc))
                continue

            report["yt_sources"].append(
                {
                    "name": channel.name,
                    "items": len(videos),
                    "ok": ok,
                    "elapsed": time.monotonic() - t0,
                }
            )
            _record_source_health(report, channel.name, "youtube", "ok", len(videos))
            yt_raw_list.extend(videos)

        if yt_raw_list:
            news_items["YouTube"].extend(yt_raw_list)
    else:
        for channel in CONFIG.youtube_channels:
            if not channel.enabled:
                log.info("youtube.skipped", channel=channel.name, reason="enabled=false")
                continue

            t0 = time.monotonic()
            try:
                videos = fetcher.fetch_youtube_channel(
                    channel.model_dump(), feed_cache, today, MAX_VIDEOS
                )
                ok = True
            except Exception as exc:
                log.error("yt.fail", channel=channel.name, error=str(exc))
                report["yt_sources"].append(
                    {
                        "name": channel.name,
                        "items": 0,
                        "ok": False,
                        "elapsed": time.monotonic() - t0,
                    }
                )
                _record_source_health(report, channel.name, "youtube", "error", 0, str(exc))
                continue

            report["yt_sources"].append(
                {
                    "name": channel.name,
                    "items": len(videos),
                    "ok": ok,
                    "elapsed": time.monotonic() - t0,
                }
            )
            _record_source_health(report, channel.name, "youtube", "ok", len(videos))
            for video in videos:
                video_candidates.append({**video, "_source_name": channel.name})

    return {
        "news_items": news_items,
        "paper_candidates": paper_candidates,
        "video_candidates": video_candidates,
    }


def _flush_digest(
    *,
    report: dict[str, Any],
    collection: dict[str, Any],
    feed_cache: dict[str, str],
    today: str,
    raw_only: bool,
    test_mode: bool,
    dry_run: bool,
    used_urls: set[str],
    rotation_state: dict[str, Any],
) -> None:
    news_items: dict[str, list[dict]] = collection["news_items"]
    paper_candidates: list[dict] = collection["paper_candidates"]
    video_candidates: list[dict] = collection["video_candidates"]

    if raw_only:
        deduped: dict[str, list[dict]] = {}
        for src, items in news_items.items():
            dedup = _dedupe_items(items)
            if dedup:
                deduped[src] = dedup

        if deduped:
            path, content = formatter.format_daily_digest(deduped, raw_only=True)
            if test_mode:
                log.info("test.digest.preview", path=path, preview=content[:200])
            else:
                ok_write = _write(path, content, dry_run)
                _record_write(report, path, ok_write)
                if ok_write and not dry_run:
                    report["archived_raw_files"] = _archive_old_raw_feeds(
                        VAULT_PATH, RAW_FEED_KEEP_DAYS
                    )
        else:
            _record_skip(
                report,
                f"00-Inbox/Raw-Feeds/Raw-Daily-Feeds-{today}.md",
                "No raw feed items were fetched.",
            )
            log.warning("raw_digest.skip.empty", date=today)
        return

    paper_written_refs: list[dict] = []
    video_written_refs: list[dict] = []
    selected_papers: list[dict[str, Any]]
    deferred_papers: list[dict[str, Any]]
    selected_videos: list[dict[str, Any]]
    deferred_videos: list[dict[str, Any]]

    if DAILY_DIGEST_ONLY_OUTPUT:
        selected_papers, deferred_papers = list(paper_candidates), []
        selected_videos, deferred_videos = list(video_candidates), []
    else:
        selected_papers, deferred_papers = _select_with_global_limit(
            paper_candidates, "_source_name", MAX_PAPER_NOTES_PER_DAY
        )
        selected_videos, deferred_videos = _select_with_global_limit(
            video_candidates, "_source_name", MAX_VIDEO_NOTES_PER_DAY
        )

    _remove_deferred_from_cache(feed_cache, deferred_papers, today)
    _remove_deferred_from_cache(feed_cache, deferred_videos, today)

    report["paper_candidates"] = len(paper_candidates)
    report["paper_written"] = len(selected_papers)
    report["paper_deferred"] = len(deferred_papers)
    report["video_candidates"] = len(video_candidates)
    report["video_written"] = len(selected_videos)
    report["video_deferred"] = len(deferred_videos)

    for paper in selected_papers:
        source_name = str(paper.get("_source_name") or "paper-feed")
        news_items[source_name].append(
            {**paper, "content_type": paper.get("content_type", "paper")}
        )
        if DAILY_DIGEST_ONLY_OUTPUT:
            paper_written_refs.append(
                {
                    "title": paper.get("title", "Untitled"),
                    "link": paper.get("link", ""),
                    "source": source_name,
                    "summary": paper.get("summary", ""),
                }
            )
            continue

        path, content = formatter.format_paper_note(paper, source_name)
        if test_mode:
            log.info("test.paper.preview", path=path)
        else:
            _record_write(report, path, _write(path, content, dry_run))
        paper_written_refs.append(
            {
                "title": paper.get("title", "Untitled"),
                "link": paper.get("link", ""),
                "source": source_name,
                "note_path": path,
                "summary": paper.get("summary", ""),
            }
        )

    for video in selected_videos:
        source_name = str(video.get("channel_name") or "YouTube")
        news_items[source_name].append(
            {**video, "content_type": video.get("content_type", "video")}
        )
        if DAILY_DIGEST_ONLY_OUTPUT:
            video_written_refs.append(
                {
                    "title": video.get("title", "Untitled"),
                    "link": video.get("link", ""),
                    "source": source_name,
                    "summary": video.get("summary", ""),
                }
            )
            continue

        path, content = formatter.format_video_note(video)
        if test_mode:
            log.info("test.video.preview", path=path)
        else:
            _record_write(report, path, _write(path, content, dry_run))
        video_written_refs.append(
            {
                "title": video.get("title", "Untitled"),
                "link": video.get("link", ""),
                "source": source_name,
                "note_path": path,
                "summary": video.get("summary", ""),
            }
        )

    paper_queue_refs = [
        {
            "title": p.get("title", "Untitled"),
            "link": p.get("link", ""),
            "source": p.get("_source_name", "paper-feed"),
            "summary": p.get("summary", ""),
        }
        for p in deferred_papers[:DAILY_DIGEST_MAX_DEFERRED_ITEMS]
    ]
    video_queue_refs = [
        {
            "title": v.get("title", "Untitled"),
            "link": v.get("link", ""),
            "source": v.get("channel_name", "YouTube"),
            "summary": v.get("summary", ""),
        }
        for v in deferred_videos[:DAILY_DIGEST_MAX_DEFERRED_ITEMS]
    ]

    news_items = _nonempty_item_buckets(news_items)
    if not (
        news_items
        or paper_written_refs
        or video_written_refs
        or paper_queue_refs
        or video_queue_refs
    ):
        _record_skip(
            report,
            f"30-Daily/AI-News/AI-Daily-{today}.md",
            "No digest candidates were fetched.",
        )
        log.warning("digest.skip.no_candidates", date=today)
        return

    digest_stats = {
        "sources_scanned": len(report["rss_sources"]) + len(report["yt_sources"]),
        "papers_written": len(paper_written_refs),
        "papers_deferred": len(deferred_papers),
        "videos_written": len(video_written_refs),
        "videos_deferred": len(deferred_videos),
        "daily_only_output": DAILY_DIGEST_ONLY_OUTPUT,
    }
    curation_plan = daily_curation.plan_daily_digest(
        news_items,
        today=today,
        top_picks=DAILY_DIGEST_TOP_PICKS,
        action_items=DAILY_DIGEST_ACTION_ITEMS,
        max_deferred_items=DAILY_DIGEST_MAX_DEFERRED_ITEMS,
        min_top_nonpaper=DAILY_DIGEST_MIN_TOP_NONPAPER,
        min_top_content_types=DAILY_DIGEST_MIN_TOP_CONTENT_TYPES,
        max_paper_in_top=DAILY_DIGEST_MAX_PAPER_IN_TOP,
        paper_written=paper_written_refs,
        video_written=video_written_refs,
        paper_queue=paper_queue_refs,
        video_queue=video_queue_refs,
        stats=digest_stats,
        used_urls=used_urls,
        rotation_state=rotation_state,
    )
    if not _has_renderable_digest_content(curation_plan):
        _record_skip(
            report,
            f"30-Daily/AI-News/AI-Daily-{today}.md",
            "No eligible stories remained after dedupe, used-article filtering, and curation.",
        )
        log.warning(
            "digest.skip.empty_selection",
            date=today,
            candidate_count=curation_plan.snapshot.get("candidate_count", 0),
            filtered_duplicates=curation_plan.snapshot.get("filtered_duplicates", 0),
        )
        return

    digest_copy = None
    if not test_mode and not dry_run and llm_digest.can_generate_digest_copy():
        try:
            digest_copy = llm_digest.generate_digest_copy(curation_plan)
        except Exception as exc:
            log.warning("digest.llm_copy.fail", error=str(exc))
    path, content = formatter.format_daily_digest(
        news_items,
        raw_only=False,
        top_picks=DAILY_DIGEST_TOP_PICKS,
        max_items_per_source=DAILY_DIGEST_MAX_ITEMS_PER_SOURCE,
        action_items=DAILY_DIGEST_ACTION_ITEMS,
        max_deferred_items=DAILY_DIGEST_MAX_DEFERRED_ITEMS,
        include_mindmap=DAILY_DIGEST_INCLUDE_MINDMAP,
        include_cognitive_lenses=DAILY_DIGEST_INCLUDE_COGNITIVE_LENSES,
        cognitive_questions=DAILY_DIGEST_COGNITIVE_QUESTIONS,
        quality_gate_enabled=DAILY_DIGEST_QUALITY_GATE_ENABLED,
        tldr_min_quality_score=DAILY_DIGEST_TLDR_MIN_QUALITY_SCORE,
        tldr_max_undisclosed=DAILY_DIGEST_TLDR_MAX_UNDISCLOSED,
        tldr_min_items=DAILY_DIGEST_TLDR_MIN_ITEMS,
        tldr_min_hard_signal_ratio=DAILY_DIGEST_TLDR_MIN_HARD_SIGNAL_RATIO,
        tldr_max_undisclosed_ratio=DAILY_DIGEST_TLDR_MAX_UNDISCLOSED_RATIO,
        min_top_nonpaper=DAILY_DIGEST_MIN_TOP_NONPAPER,
        min_top_content_types=DAILY_DIGEST_MIN_TOP_CONTENT_TYPES,
        max_paper_in_top=DAILY_DIGEST_MAX_PAPER_IN_TOP,
        paper_written=paper_written_refs,
        video_written=video_written_refs,
        paper_queue=paper_queue_refs,
        video_queue=video_queue_refs,
        stats=digest_stats,
        curation_plan=curation_plan,
        digest_copy=digest_copy,
    )
    if test_mode:
        log.info("test.digest.preview", path=path, preview=content[:200])
    else:
        ok_digest = _write(path, content, dry_run)
        _record_write(report, path, ok_digest)
        if ok_digest and not dry_run:
            daily_curation.persist_daily_digest_selection(
                curation_plan,
                used_articles_path=USED_ARTICLES_PATH,
                source_rotation_path=SOURCE_ROTATION_PATH,
                retention_days=CONFIG.used_articles_retention_days,
            )


# 鈹€鈹€ Writer Dispatch 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _write(filepath: str, content: str, dry_run: bool = False) -> bool:
    """
    Dispatch a write operation based on WRITE_MODE config.
    In dry_run mode, only print the target path - no actual I/O.
    """
    if dry_run:
        console.print(f"  [dim cyan][dry-run][/] would write -> [bold]{filepath}[/]")
        return True

    if WRITE_MODE == "disk":
        return writer.write_to_obsidian_disk(VAULT_PATH, filepath, content)

    if WRITE_MODE == "api":
        api_base = os.getenv("OBSIDIAN_API_BASE", CONFIG.obsidian_api.base_url)
        api_key = os.getenv("OBSIDIAN_API_KEY", CONFIG.obsidian_api.api_key)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "text/markdown"}
        return writer.write_via_api(api_base, filepath, content, headers, overwrite=True)

    if WRITE_MODE == "both":
        ok_disk = writer.write_to_obsidian_disk(VAULT_PATH, filepath, content)
        api_base = os.getenv("OBSIDIAN_API_BASE", CONFIG.obsidian_api.base_url)
        api_key = os.getenv("OBSIDIAN_API_KEY", CONFIG.obsidian_api.api_key)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "text/markdown"}
        ok_api = writer.write_via_api(api_base, filepath, content, headers, overwrite=True)
        return ok_disk and ok_api

    log.warning("write.dispatch.unknown_mode", mode=WRITE_MODE)
    return False


# 鈹€鈹€ Utility Helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _is_paper_feed(url: str) -> bool:
    url_lower = url.lower()
    return "arxiv" in url_lower or "paperswithcode" in url_lower


def _is_valid_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _looks_like_youtube_channel_id(channel_id: str) -> bool:
    return channel_id.startswith("UC") and len(channel_id) == 24


def _dedupe_items(items: list[dict]) -> list[dict]:
    seen_keys: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        key = (
            (item.get("guid") or "").strip()
            or (item.get("link") or "").strip()
            or f"{(item.get('title') or '').strip()}|{(item.get('summary') or '').strip()}"
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    return deduped


def _select_with_global_limit(
    items: list[dict],
    source_key: str,
    limit: int,
) -> tuple[list[dict], list[dict]]:
    """
    Select up to `limit` items globally while keeping source diversity.

    Uses a round-robin pass across sources to avoid one source dominating
    the daily write set.
    """
    if limit <= 0:
        return [], list(items)
    if len(items) <= limit:
        return list(items), []

    grouped: dict[str, list[dict]] = {}
    source_order: list[str] = []
    for item in items:
        source = str(item.get(source_key) or "unknown")
        if source not in grouped:
            grouped[source] = []
            source_order.append(source)
        grouped[source].append(item)

    selected: list[dict] = []
    while len(selected) < limit:
        progressed = False
        for source in source_order:
            queue = grouped.get(source, [])
            if not queue:
                continue
            selected.append(queue.pop(0))
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break

    deferred: list[dict] = []
    for source in source_order:
        deferred.extend(grouped.get(source, []))
    return selected, deferred


def _remove_deferred_from_cache(feed_cache: dict, deferred_items: list[dict], today: str) -> None:
    """
    Deferred items should remain eligible in a future run.
    Remove today's cache marks for deferred GUIDs.
    """
    for item in deferred_items:
        guid = (item.get("guid") or "").strip()
        if not guid:
            continue
        if feed_cache.get(guid) == today:
            feed_cache.pop(guid, None)


def _extract_digest_quality_score(content: str) -> float | None:
    match = re.search(r"综合评分：\*\*(\d+(?:\.\d+)?)\/10\*\*", content)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_item_date(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _backfill_digest_items(
    news_items: dict[str, list[dict]],
    *,
    today: str,
    score_threshold: float = 9.0,
) -> int:
    """
    Low-quality daily runs often happen when only video shorts or sparse-paper entries arrive.
    Backfill from high-signal RSS sources (recent 7 days, ignore cache) to rescue digest quality.
    """
    backfill_keywords = (
        "openai",
        "anthropic",
        "langchain",
        "simon",
        "hackernews",
        "batch",
        "rundown",
        "changelog",
    )
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    local_cache: dict[str, str] = {}
    added = 0
    backfill_quality = dict(QUALITY_CONFIG)
    min_interest_score = QUALITY_CONFIG.get("min_ai_interest_score", 6)
    fulltext_limit = QUALITY_CONFIG.get("fulltext_enrichment_per_feed", 2)
    backfill_quality["min_ai_interest_score"] = max(4, int(cast(int, min_interest_score)) - 2)
    backfill_quality["max_ai_items_per_feed"] = 4
    backfill_quality["fulltext_enrichment_per_feed"] = max(2, int(cast(int, fulltext_limit)))

    for feed in CONFIG.rss_feeds:
        if not feed.enabled:
            continue
        name_lower = feed.name.lower()
        if not any(token in name_lower for token in backfill_keywords):
            continue
        inferred_type = fetcher.infer_content_type(
            source_name=feed.name,
            url=feed.url,
            domain=str(feed.domain or ""),
            fallback=str(getattr(feed, "content_type", "") or ""),
        )
        if inferred_type in {"paper", "video"}:
            continue
        try:
            items = cast(
                list[dict[str, Any]],
                fetcher.fetch_rss_feed(
                    feed.model_dump(),
                    local_cache,
                    today,
                    raw_only=False,
                    quality_config=backfill_quality,
                ),
            )
        except Exception as exc:
            log.warning("digest.backfill.feed_fail", feed=feed.name, error=str(exc))
            continue
        if not items:
            continue

        for item in items:
            published = _parse_item_date(str(item.get("published", "")))
            if published is None:
                continue
            if (today_dt - published).days > 7:
                continue
            enriched = dict(item)
            enriched["backfill"] = True
            news_items[feed.name].append(enriched)
            added += 1

    if added > 0:
        for src, items in list(news_items.items()):
            news_items[src] = _dedupe_items(items)
        log.info("digest.backfill.applied", items_added=added, threshold=score_threshold)
    return added


def _archive_old_raw_feeds(vault_path: str, keep_days: int = 7) -> int:
    if keep_days < 1:
        return 0
    raw_dir = Path(vault_path) / "00-Inbox" / "Raw-Feeds"
    if not raw_dir.exists():
        return 0
    archive_dir = raw_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    cutoff_date = (datetime.now() - timedelta(days=keep_days)).date()
    archived_count = 0
    for raw_file in raw_dir.glob("Raw-Daily-Feeds-*.md"):
        suffix = raw_file.stem.replace("Raw-Daily-Feeds-", "", 1)
        try:
            file_date = datetime.strptime(suffix, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff_date:
            continue
        target = archive_dir / raw_file.name
        if target.exists():
            ts = datetime.now().strftime("%H%M%S")
            target = archive_dir / f"{raw_file.stem}-{ts}{raw_file.suffix}"
        raw_file.replace(target)
        archived_count += 1
    return archived_count


# 鈹€鈹€ Run Report 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _build_run_report(test_mode: bool, raw_only: bool, dry_run: bool) -> dict[str, Any]:
    return {
        "mode": "TEST" if test_mode else ("DRY-RUN" if dry_run else "LIVE"),
        "raw_only": raw_only,
        "rss_sources": [],  # list of {name, items, ok, elapsed}
        "yt_sources": [],  # list of {name, items, ok, elapsed}
        "writes_ok": 0,
        "writes_failed": 0,
        "written_files": [],
        "skipped_outputs": [],
        "last_skip_reason": "",
        "paper_candidates": 0,
        "paper_written": 0,
        "paper_deferred": 0,
        "video_candidates": 0,
        "video_written": 0,
        "video_deferred": 0,
        "archived_raw_files": 0,
        "source_health_entries": [],
        "start_time": time.monotonic(),
    }


def _record_write(report: dict[str, Any], filepath: str, ok: bool) -> None:
    if ok:
        report["writes_ok"] += 1
        report["written_files"].append(filepath)
    else:
        report["writes_failed"] += 1


def _record_skip(report: dict[str, Any], filepath: str, reason: str) -> None:
    report.setdefault("skipped_outputs", []).append({"path": filepath, "reason": reason})
    report["last_skip_reason"] = reason


def _nonempty_item_buckets(items_by_source: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {source: list(items) for source, items in items_by_source.items() if items}


def _has_renderable_digest_content(plan: daily_curation.DailyDigestPlan) -> bool:
    return bool(
        plan.top_stories
        or plan.venture_story
        or plan.growth_story
        or plan.video_story
        or plan.solopreneur_story
    )


def _print_rich_summary(report: dict[str, Any]) -> None:
    """Render a Rich table summarizing the run results to stdout."""
    duration = time.monotonic() - report["start_time"]
    mode_label = report["mode"]

    console.rule(f"[bold green]PKM Run Summary - {mode_label}[/]")

    # Sources table
    table = Table(title="Feed Sources", border_style="dim", show_lines=True)
    table.add_column("Source", style="cyan", no_wrap=True)
    table.add_column("Type", justify="center")
    table.add_column("Items", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Time (s)", justify="right")

    for src in report["rss_sources"]:
        status = "[green]OK[/]" if src["ok"] else "[red]FAIL[/]"
        table.add_row(src["name"], "RSS", str(src["items"]), status, f"{src['elapsed']:.1f}")

    for src in report["yt_sources"]:
        status = "[green]OK[/]" if src["ok"] else "[red]FAIL[/]"
        table.add_row(src["name"], "YouTube", str(src["items"]), status, f"{src['elapsed']:.1f}")

    console.print(table)

    # Summary row
    console.print(
        f"[bold]Files written:[/] {report['writes_ok']}  "
        f"[red]Failed:[/] {report['writes_failed']}  "
        f"[dim]Archived raw files:[/] {report['archived_raw_files']}  "
        f"[dim]Duration:[/] {duration:.1f}s"
    )
    if not report.get("raw_only", False):
        action_label = "included" if DAILY_DIGEST_ONLY_OUTPUT else "written"
        console.print(
            f"[dim]Curation:[/] papers {action_label} {report['paper_written']}/{report['paper_candidates']} "
            f"(deferred {report['paper_deferred']}), videos {report['video_written']}/"
            f"{report['video_candidates']} (deferred {report['video_deferred']})"
        )
        if DAILY_DIGEST_ONLY_OUTPUT:
            console.print(
                "[dim]Output mode:[/] single core AI Daily (paper/video merged into digest)"
            )

    if report["written_files"]:
        preview = report["written_files"][:5]
        suffix = " ..." if len(report["written_files"]) > 5 else ""
        console.print(f"[dim]Output:[/] {', '.join(preview)}{suffix}")
    if report.get("skipped_outputs"):
        skipped = report["skipped_outputs"][:3]
        preview = ", ".join(f"{item['path']} ({item['reason']})" for item in skipped)
        suffix = " ..." if len(report["skipped_outputs"]) > 3 else ""
        console.print(f"[yellow]Skipped output:[/] {preview}{suffix}")


# 鈹€鈹€ Validation 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _validate_runtime_or_raise(test_mode: bool, dry_run: bool) -> None:
    if test_mode or dry_run:
        return
    if VAULT_PATH == "YOUR_VAULT_PATH":
        raise RuntimeError(
            "OBSIDIAN_VAULT_PATH is not configured. Run `python main.py --doctor` first."
        )
    vault_dir = Path(VAULT_PATH)
    if not vault_dir.exists():
        raise RuntimeError(f"OBSIDIAN_VAULT_PATH does not exist: {vault_dir}")
    if not vault_dir.is_dir():
        raise RuntimeError(f"OBSIDIAN_VAULT_PATH is not a directory: {vault_dir}")
    if not os.access(vault_dir, os.W_OK):
        raise RuntimeError(f"OBSIDIAN_VAULT_PATH is not writable: {vault_dir}")


# 鈹€鈹€ Doctor 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def run_doctor(check_network: bool = True) -> bool:
    errors: list[str] = []
    warnings: list[str] = []

    console.rule("[bold blue]=== PKM Doctor ===[/]")
    console.print(f"Config : {CONFIG_PATH}")
    console.print(f"Vault  : {VAULT_PATH}")
    console.print(f"Write Mode: {WRITE_MODE}")

    if VAULT_PATH == "YOUR_VAULT_PATH":
        errors.append("OBSIDIAN_VAULT_PATH is not set in .env")
    else:
        vault_dir = Path(VAULT_PATH)
        if not vault_dir.exists():
            errors.append(f"Vault path does not exist: {vault_dir}")
        elif not vault_dir.is_dir():
            errors.append(f"Vault path is not a directory: {vault_dir}")
        elif not os.access(vault_dir, os.W_OK):
            errors.append(f"Vault path is not writable: {vault_dir}")

    rss_feeds = CONFIG.rss_feeds
    if not rss_feeds:
        errors.append("No rss_feeds configured.")
    for feed in rss_feeds:
        if check_network:
            try:
                parsed = fetcher._parse_feed(feed.url)
                if len(getattr(parsed, "entries", [])) == 0:
                    warnings.append(f"[RSS] {feed.name}: reachable but returned 0 entries")
            except Exception as exc:
                errors.append(f"[RSS] {feed.name}: connectivity failed ({exc})")

    yt_channels = CONFIG.youtube_channels
    if not yt_channels:
        warnings.append("No youtube_channels configured.")
    for channel in yt_channels:
        if not _looks_like_youtube_channel_id(channel.channel_id):
            warnings.append(f"[YouTube] {channel.name}: unusual channel_id '{channel.channel_id}'")
        if check_network:
            try:
                parsed = fetcher._parse_youtube_feed(channel.channel_id)
                if len(getattr(parsed, "entries", [])) == 0:
                    warnings.append(f"[YouTube] {channel.name}: reachable but returned 0 entries")
            except Exception as exc:
                errors.append(f"[YouTube] {channel.name}: connectivity failed ({exc})")

    if warnings:
        console.print("\n[yellow]Warnings:[/]")
        for item in warnings:
            console.print(f"  [yellow]鈿燵/]  {item}")

    if errors:
        console.print("\n[red]Errors:[/]")
        for item in errors:
            console.print(f"  [red]-[/]  {item}")
        console.print("\n[bold red]Doctor result: FAILED[/]")
        return False

    console.print("\n[bold green]Doctor result: OK[/]")
    return True


# 鈹€鈹€ Core Workflow 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def run_daily_fetch(
    test_mode: bool = False,
    raw_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    _validate_runtime_or_raise(test_mode=test_mode, dry_run=dry_run)
    report = _build_run_report(test_mode=test_mode, raw_only=raw_only, dry_run=dry_run)

    console.rule(f"[bold]PKM Daily Fetch — {formatter.today_str()}[/]")
    mode_str = report["mode"]
    console.print(f"Mode: [bold]{mode_str}[/]  |  RAW_ONLY: {raw_only}  |  WRITE: {WRITE_MODE}")

    feed_cache = load_feed_cache()
    log.info("cache.loaded", guid_count=len(feed_cache))
    mutates_state = not (test_mode or dry_run)
    if mutates_state:
        _refresh_source_rotation_week(SOURCE_ROTATION_PATH)
        _compact_used_articles(USED_ARTICLES_PATH, CONFIG.used_articles_retention_days)
    used_urls = daily_curation.load_used_urls(USED_ARTICLES_PATH)
    rotation_state = daily_curation.load_rotation_state(SOURCE_ROTATION_PATH)
    today = formatter.today_str()
    collection = _collect_items(
        report=report,
        feed_cache=feed_cache,
        today=today,
        raw_only=raw_only,
    )
    _flush_digest(
        report=report,
        collection=collection,
        feed_cache=feed_cache,
        today=today,
        raw_only=raw_only,
        test_mode=test_mode,
        dry_run=dry_run,
        used_urls=used_urls,
        rotation_state=rotation_state,
    )

    # Finalize
    if mutates_state:
        save_feed_cache(feed_cache)
        log.info("cache.saved", guid_count=len(feed_cache))
        _save_source_health(report)

    _print_rich_summary(report)
    return report


# 鈹€鈹€ CLI 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PKM Daily Workflow Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--test", action="store_true", help="Test mode (no writing to Vault)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show all files that would be written without actually writing them",
    )
    parser.add_argument("--schedule", action="store_true", help="Schedule mode (daemon)")
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="Generate raw feeds for AI Agent curation",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run configuration and connectivity diagnostics",
    )
    parser.add_argument(
        "--doctor-skip-network",
        action="store_true",
        help="Doctor mode without remote connectivity checks",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run knowledge base lint report generation (10-Notes -> 40-MOC)",
    )
    args = parser.parse_args()

    if args.doctor:
        ok = run_doctor(check_network=not args.doctor_skip_network)
        raise SystemExit(0 if ok else 1)

    if args.health_check:
        import knowledge_health_check as khc

        report_path, summary = khc.generate_report(Path(VAULT_PATH), max_items=30, write_log=True)
        console.print(
            f"[green]Health check done:[/] score={summary['score']}/100 "
            f"critical={summary['critical']} warning={summary['warning']} "
            f"suggestion={summary['suggestion']}"
        )
        console.print(f"[green]Report:[/] {report_path}")
        raise SystemExit(0)

    if args.schedule:
        fetch_time = os.getenv("DAILY_FETCH_TIME", CONFIG.daily_fetch_time)
        console.print(f"[Scheduled] Running daily at [bold]{fetch_time}[/]. Press Ctrl+C to stop.")
        import schedule as sched

        sched.every().day.at(fetch_time).do(run_daily_fetch, raw_only=args.raw_only)
        while True:
            sched.run_pending()
            time.sleep(60)
    else:
        run_daily_fetch(
            test_mode=args.test,
            raw_only=args.raw_only,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
