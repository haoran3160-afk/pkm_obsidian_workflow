#!/usr/bin/env python3
"""
main.py — Obsidian PKM Workflow Orchestrator
Coordinates fetching, formatting, and writing of daily digests and feeds.
Usage:
  python main.py
  python main.py --test
  python main.py --raw-only
  python main.py --schedule
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Load config tools
from dotenv import load_dotenv

import fetcher
import formatter
import writer

# ── Logging Setup ─────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"
CACHE_PATH  = SCRIPT_DIR / "feed_cache.json"
LOG_PATH    = SCRIPT_DIR / "fetch.log"

# Force UTF-8 stdout
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pkm")

# ── Load Config + Environment ─────────────────────────────────────────────────

load_dotenv(SCRIPT_DIR / ".env")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "YOUR_VAULT_PATH")
CACHE_EXPIRY_DAYS = int(os.getenv("FEED_CACHE_EXPIRY_DAYS", "7"))
MAX_PAPERS = CONFIG.get("max_papers_per_day", 10)
MAX_VIDEOS = CONFIG.get("max_videos_per_channel", 3)

if VAULT_PATH == "YOUR_VAULT_PATH":
    log.warning("OBSIDIAN_VAULT_PATH not set in .env. Files won't be saved correctly!")

# ── Cache Management ──────────────────────────────────────────────────────────

def load_feed_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        cutoff = (datetime.now() - timedelta(days=CACHE_EXPIRY_DAYS)).strftime("%Y-%m-%d")
        return {guid: date for guid, date in data.items() if date >= cutoff}
    except Exception as e:
        log.error(f"Failed to load cache: {e}")
        return {}

def save_feed_cache(cache: dict):
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"Cache save failed: {e}")

# ── Execution Workflow ────────────────────────────────────────────────────────

def run_daily_fetch(test_mode: bool = False, raw_only: bool = False):
    log.info("=" * 60)
    log.info(f"PKM Daily Fetch — {formatter.today_str()}")
    log.info(f"Mode: {'TEST' if test_mode else 'LIVE'} | RAW_ONLY: {raw_only}")
    log.info("=" * 60)

    feed_cache = load_feed_cache()
    log.info(f"[cache] Loaded {len(feed_cache)} valid GUIDs")
    today = formatter.today_str()

    news_items = defaultdict(list)
    papers = []

    # 1. Fetch RSS Feeds
    for feed in CONFIG["rss_feeds"]:
        items = fetcher.fetch_rss_feed(feed, feed_cache, today, MAX_PAPERS, raw_only)
        if "arxiv" in feed["url"] or "paperswithcode" in feed["url"]:
            papers.extend([{**i, "source": feed["name"]} for i in items])
            if raw_only:
                news_items[feed["name"]].extend(items)
            elif not test_mode:
                # Format & Write Paper Notes
                for paper in items:
                    path, content = formatter.format_paper_note(paper, feed["name"])
                    writer.write_to_obsidian_disk(VAULT_PATH, path, content)
        else:
            news_items[feed["name"]].extend(items)

    # 2. Daily Digest
    if news_items:
        log.info("\n[生成] 资讯摘要 / Raw Feeds...")
        path, content = formatter.format_daily_digest(dict(news_items), raw_only)
        if test_mode:
            log.info(f"\n[TEST] File: {path}\n{content[:200]}...")
        else:
            writer.write_to_obsidian_disk(VAULT_PATH, path, content)

    # 3. YouTube Processing
    if raw_only:
        log.info("\n[YouTube] 收集最新视频 (Raw Mode)...")
        yt_raw_list = []
        for channel in CONFIG["youtube_channels"]:
            yt_raw_list.extend(fetcher.fetch_youtube_channel_raw(channel))
        raw_block = formatter.format_youtube_raw_block(yt_raw_list)

        if raw_block and not test_mode:
            writer.append_to_obsidian_disk(VAULT_PATH, "00-Inbox/Raw-Daily-Feeds.md", raw_block)
    else:
        log.info("\n[YouTube] 收集最新视频 (Note Mode)...")
        for channel in CONFIG["youtube_channels"]:
            videos = fetcher.fetch_youtube_channel(channel, feed_cache, today, MAX_VIDEOS)
            if not test_mode:
                for v in videos:
                    path, content = formatter.format_video_note(v)
                    writer.write_to_obsidian_disk(VAULT_PATH, path, content)

    # 4. IELTS Reminder
    if not raw_only:
        log.info("\n[生成] IELTS 学习日志...")
        path, content = formatter.format_ielts_reminder()
        if test_mode:
            log.info(f"\n[TEST] IELTS 日志: {path}")
        else:
            writer.write_to_obsidian_disk(VAULT_PATH, path, content)
    else:
        log.info("\n[跳过] IELTS 日志 (Agent 代理模式)...")

    # Finalize
    save_feed_cache(feed_cache)
    log.info(f"[cache] Saved {len(feed_cache)} GUIDs")
    log.info("=" * 60)
    log.info("每日拉取完成！")
    log.info("=" * 60)

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PKM Daily Workflow Orchestrator")
    parser.add_argument("--test",     action="store_true", help="Test mode (no writing to Vault)")
    parser.add_argument("--schedule", action="store_true", help="Schedule mode (daemon)")
    parser.add_argument("--raw-only", action="store_true", help="Generate raw feeds for AI Agent curation")
    args = parser.parse_args()

    if args.schedule:
        fetch_time = os.getenv("DAILY_FETCH_TIME", CONFIG.get("daily_fetch_time", "07:00"))
        print(f"[Scheduled] Running daily at {fetch_time}. Press Ctrl+C to stop.")
        import schedule
        schedule.every().day.at(fetch_time).do(run_daily_fetch, raw_only=args.raw_only)
        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        run_daily_fetch(test_mode=args.test, raw_only=args.raw_only)

if __name__ == "__main__":
    main()
