#!/usr/bin/env python3
"""
daily_fetch.py — 每日自动拉取脚本
功能：
  1. 拉取 RSS 资讯（The Batch, HackerNews, arXiv, Papers With Code 等）
  2. 拉取 YouTube 频道最新视频（通过 YouTube RSS）
  3. 直写 Obsidian Vault（磁盘直写模式）

用法:
  python daily_fetch.py          # 立即执行今日拉取
  python daily_fetch.py --test   # 测试模式（只打印，不写入）
  python daily_fetch.py --raw-only  # 仅生成供 PKM Agent 策展的 Raw Feeds
  python daily_fetch.py --schedule  # 每日定时运行
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import feedparser

# ── 环境配置（.env + pkm_config.json）──────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"
CACHE_PATH  = SCRIPT_DIR / "feed_cache.json"
LOG_PATH    = SCRIPT_DIR / "fetch.log"

# 尝试加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")
except ImportError:
    pass  # python-dotenv 未安装时降级处理

# 强制 UTF-8 控制台输出，避免 Windows GBK 编码崩溃（Python 3.7+）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass  # Python < 3.7 降级跳过

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

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

# 优先从环境变量读取 Vault 路径和 API 配置
VAULT_PATH   = os.getenv("OBSIDIAN_VAULT_PATH", CONFIG.get("vault_path", "D:/personal/Obsidian"))
API_BASE     = os.getenv("OBSIDIAN_API_BASE",   CONFIG.get("obsidian_api", {}).get("base_url", ""))
API_KEY      = os.getenv("OBSIDIAN_API_KEY",    CONFIG.get("obsidian_api", {}).get("api_key", ""))
HEADERS      = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "text/markdown"}

CACHE_EXPIRY_DAYS = int(os.getenv("FEED_CACHE_EXPIRY_DAYS", "7"))
MAX_PAPERS   = CONFIG.get("max_papers_per_day", 10)
MAX_VIDEOS   = CONFIG.get("max_videos_per_channel", 3)

# ── Feed GUID 缓存（跳过已处理条目） ─────────────────────────────────────────────

def load_feed_cache() -> dict:
    """Load cached article GUIDs; purge entries older than CACHE_EXPIRY_DAYS"""
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        cutoff = (datetime.now() - timedelta(days=CACHE_EXPIRY_DAYS)).strftime("%Y-%m-%d")
        return {guid: date for guid, date in data.items() if date >= cutoff}
    except Exception as e:
        log.error(f"Failed to load feed cache: {e}")
        return {}

def save_feed_cache(cache: dict):
    """Persist the cache to disk"""
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"Cache save failed: {e}")

FEED_CACHE: dict = {}  # populated in run_daily_fetch

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def write_to_obsidian(filepath: str, content: str):
    """Writes directly to the local Obsidian Vault"""
    full_path = os.path.join(VAULT_PATH, filepath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info(f"[write] {filepath}")
    except Exception as e:
        log.error(f"[write-fail] {filepath} — {e}")

def ai_filter(text: str) -> bool:
    """过滤 HackerNews 条目，只保留 AI 相关"""
    keywords = ["ai", "llm", "gpt", "machine learning", "neural", "deep learning",
                "openai", "anthropic", "gemini", "transformer", "model", "inference",
                "fine-tun", "rag", "agent", "benchmark", "arxiv"]
    text_lower = text.lower()
    return any(k in text_lower for k in keywords)

# ── RSS 拉取 ───────────────────────────────────────────────────────────────────

def fetch_rss_feed(feed_config: dict, test_mode: bool = False, raw_only: bool = False) -> list:
    """Fetch a single RSS feed.
    raw_only=True: return ALL articles (no cache filter) — Agent curates from full set.
    raw_only=False: skip already-cached GUIDs to avoid writing duplicate permanent notes.
    """
    name = feed_config["name"]
    url = feed_config["url"]
    folder = feed_config["note_folder"]
    filter_kw = feed_config.get("filter_keywords", [])

    log.info(f"[rss] {name}")
    try:
        d = feedparser.parse(url)
    except Exception as e:
        log.error(f"[rss-fail] {name}: {e}")
        return []

    entries = d.entries[:MAX_PAPERS] if "arxiv" in url.lower() or "paperswithcode" in url.lower() else d.entries[:20]
    results = []
    today = today_str()
    skipped = 0

    for entry in entries:
        title   = entry.get("title", "无标题")
        link    = entry.get("link", "")
        guid    = entry.get("id", link).strip()
        summary = entry.get("summary", entry.get("description", ""))
        summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]

        # Cache filtering only applies in full (non-raw) mode to prevent duplicate notes
        if not raw_only and guid and guid in FEED_CACHE:
            skipped += 1
            continue

        # HackerNews AI keyword filter (applies in all modes)
        if filter_kw and not ai_filter(title + " " + summary):
            continue

        results.append({"title": title, "link": link, "guid": guid, "summary": summary, "folder": folder})
        # Only mark as seen when writing permanent notes
        if not raw_only and guid:
            FEED_CACHE[guid] = today

    log.info(f"  -> {len(results)} items ({skipped} skipped by cache)")
    return results

def write_daily_digest(items_by_source: dict, test_mode: bool = False, raw_only: bool = False):
    """生成每日 AI 资讯摘要笔记，若 raw_only 则生成原始数据供 Agent 策展"""
    today = today_str()

    if raw_only:
        filepath = "00-Inbox/Raw-Daily-Feeds.md"
        lines = [f"# Raw Daily Feeds - {today}\n\n*此文件由 daily_fetch.py 生成，供 PKM Agent 提取高价值资讯使用。*\n"]
        for source, items in items_by_source.items():
            lines.append(f"## {source}\n")
            for item in items:
                lines.append(f"- **Title**: {item['title']}")
                lines.append(f"  **URL**: {item['link']}")
                if item.get("summary"):
                    lines.append(f"  **Summary**: {item['summary'][:500]}")
                lines.append("")
        content = "\n".join(lines)
    else:
        filepath = f"30-Daily/AI-News/AI-Daily-{today}.md"
        frontmatter = (
            "---\n"
            f'title: "AI Daily Digest - {today}"\n'
            f"date: {today}\n"
            'tags: ["AI-News", "Daily", "Digest"]\n'
            "type: daily-digest\n"
            "status: unreviewed\n"
            f"created: {now_str()}\n"
            "---\n\n"
            f"# AI 资讯日报 — {today}\n\n"
        )
        lines = [frontmatter]
        for source, items in items_by_source.items():
            lines.append(f"## {source}\n")
            for item in items:
                lines.append(f"- **[{item['title']}]({item['link']})**")
                if item.get("summary"):
                    lines.append(f"  > {item['summary'][:200]}...")
                lines.append("")
            lines.append("")
        content = "\n".join(lines)

    if test_mode:
        log.info(f"\n[TEST] 将写入: {filepath}\n{content[:300]}...")
    else:
        write_to_obsidian(filepath, content)

def write_paper_notes(papers: list, source_name: str, test_mode: bool = False):
    """为每篇论文写入独立笔记"""
    today = today_str()
    for paper in papers:
        title = paper["title"]
        safe_title = re.sub(r"[^\w\s\-]", "", title)[:60].strip().replace(" ", "-")
        filepath = f"20-Sources/Papers/{today}-{safe_title}.md"
        content = f"""---
title: "{title}"
date: {today}
tags: ["Paper", "Research", "ArXiv"]
domain: Research
source: "{source_name}"
language: en
type: paper-note
status: unreviewed
url: "{paper['link']}"
created: {now_str()}
---

# {title}

## Abstract / Summary

{paper['summary']}

## Key Contributions

> *(待阅读后填写)*

## My Notes

> *(待填写)*

## Links

- [原文]({paper['link']})
"""
        if test_mode:
            log.info(f"  [TEST] Paper: {title[:50]}...")
        else:
            write_to_obsidian(filepath, content)

# ── YouTube RSS 拉取 ───────────────────────────────────────────────────────────

def fetch_youtube_channel(channel: dict, test_mode: bool = False):
    """通过 YouTube RSS 拉取频道最新视频"""
    name = channel["name"]
    channel_id = channel["channel_id"]
    folder = channel["note_folder"]
    domain = channel["domain"]

    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    log.info(f"\n[YouTube] {name}")

    try:
        d = feedparser.parse(rss_url)
    except Exception as e:
        log.error(f"[yt-fail] {name}: {e}")
        return

    today = today_str()
    entries = d.entries[:MAX_VIDEOS]

    for entry in entries:
        title     = entry.get("title", "无标题")
        link      = entry.get("link", "")
        guid      = entry.get("id", link).strip()
        published = entry.get("published", today)[:10]
        summary   = entry.get("summary", "")
        summary   = re.sub(r"<[^>]+>", "", summary).strip()[:400]

        # Skip cached
        if guid and guid in FEED_CACHE:
            log.info(f"  [yt-cached] {title[:50]}")
            continue
        if guid:
            FEED_CACHE[guid] = today

        safe_title = re.sub(r"[^\w\s\-\u4e00-\u9fff]", "", title)[:60].strip().replace(" ", "-")
        filepath = f"{folder}/{published}-{name.replace(' ','-')}-{safe_title}.md"

        content = f"""---
title: "{title}"
date: {published}
tags: ["YouTube", "{name}", "{domain}"]
domain: {domain}
source: "YouTube / {name}"
language: en
type: video-note
status: unreviewed
url: "{link}"
created: {now_str()}
---

# {title}

> 📺 **{name}** | {published}
> [→ 观看视频]({link})

## 视频简介

{summary}

## 关键要点

> *(观看后填写)*

## 学习笔记

> *(待填写)*

## 相关概念

> *(待填写)*
"""
        if test_mode:
            log.info(f"[test] Video: {title[:50]}")
        else:
            write_to_obsidian(filepath, content)
            log.info(f"[yt] {title[:50]}")

# ── IELTS 提醒 ─────────────────────────────────────────────────────────────────

def write_ielts_reminder(test_mode: bool = False):
    """每日 IELTS 学习提醒和资源索引"""
    today = today_str()
    filepath = f"10-Notes/IELTS/IELTS-Study-{today}.md"

    content = f"""---
title: "IELTS Study Log - {today}"
date: {today}
tags: ["IELTS", "Daily-Study"]
type: study-log
created: {now_str()}
---

# IELTS 学习日志 — {today}

## 今日练习

### 🎧 Listening
- [ ] 完成 1 套 Listening 练习（[IELTSonlinetests.com](https://ieltsonlinetests.com) 或剑桥真题）
- [ ] 得分: ______  错误题号: ______
- [ ] 错误分析: 

### 📖 Reading
- [ ] 完成 1 套 Reading 练习（[IELTS-up.com](https://ielts-up.com)）
- [ ] 得分: ______  错误题号: ______
- [ ] 错误分析:

### ✍️ Writing
- [ ] Task 1 / Task 2 练习

### 🗣️ Speaking
- [ ] Part 2 话题练习

## 新词汇

| 单词/短语 | 含义 | 例句 |
|----------|------|------|
|          |      |      |

## 资源链接

- [IELTS Liz](https://www.youtube.com/@ieltsliz)
- [E2 IELTS](https://www.youtube.com/@E2IELTS)
- [IELTSonlinetests.com](https://ieltsonlinetests.com)
- [British Council Practice](https://www.britishcouncil.org/exam/ielts/preparation)
"""
    if test_mode:
        log.info(f"\n[TEST] IELTS 日志: {filepath}")
    else:
        write_to_obsidian(filepath, content)

# ── 主流程 ─────────────────────────────────────────────────────────────────────

def run_daily_fetch(test_mode: bool = False, raw_only: bool = False):
    global FEED_CACHE

    log.info("=" * 60)
    log.info(f"PKM Daily Fetch — {today_str()}")
    log.info(f"模式: {'TEST' if test_mode else 'LIVE'} | RAW_ONLY: {raw_only}")
    log.info("=" * 60)

    # Load feed GUID cache
    FEED_CACHE = load_feed_cache()
    log.info(f"[cache] Loaded {len(FEED_CACHE)} cached GUIDs")

    # 1. 拉取 RSS 资讯
    news_items = defaultdict(list)
    papers = []

    for feed in CONFIG["rss_feeds"]:
        items = fetch_rss_feed(feed, test_mode, raw_only=raw_only)
        if "arxiv" in feed["url"] or "paperswithcode" in feed["url"]:
            papers.extend([{**i, "source": feed["name"]} for i in items])
            if raw_only:
                # raw_only 模式：将论文也加入 news_items 供 Agent 策展
                news_items[feed["name"]].extend(items)
            elif not test_mode:
                write_paper_notes(items, feed["name"], test_mode)
        else:
            news_items[feed["name"]].extend(items)

    # 2. 生成资讯摘要 (若 raw_only=True 则生成 Raw Feeds 供 Agent 策展)
    if news_items:
        log.info("\n[生成] 资讯摘要 / Raw Feeds...")
        write_daily_digest(dict(news_items), test_mode, raw_only)

    # 3. 拉取 YouTube 视频并追加到 Raw Feeds（raw_only 模式）
    if raw_only:
        log.info("\n[YouTube] 收集最新视频...")
        yt_lines = ["\n## YouTube 最新视频\n"]
        for channel in CONFIG["youtube_channels"]:
            name = channel["name"]
            channel_id = channel["channel_id"]
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            try:
                d = feedparser.parse(rss_url)
                entries = d.entries[:2]
                for entry in entries:
                    title = entry.get("title", "无标题")
                    link = entry.get("link", "")
                    published = entry.get("published", "")[:10]
                    summary = entry.get("summary", "")
                    summary = re.sub(r"<[^>]+>", "", summary).strip()[:300]
                    yt_lines.append(f"- **Channel**: {name}")
                    yt_lines.append(f"  **Title**: {title}")
                    yt_lines.append(f"  **URL**: {link}")
                    yt_lines.append(f"  **Published**: {published}")
                    if summary:
                        yt_lines.append(f"  **Summary**: {summary}")
                    yt_lines.append("")
                log.info(f"  [✓] {name}: {len(entries)} 条")
            except Exception as e:
                log.error(f"  [✗] {name}: {e}")

        # 追加 YouTube 数据到 Raw Feeds 文件
        if len(yt_lines) > 1:
            raw_path = os.path.join(VAULT_PATH, "00-Inbox/Raw-Daily-Feeds.md")
            if os.path.exists(raw_path):
                with open(raw_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(yt_lines))
                log.info("  [✓] YouTube 数据已追加到 Raw-Daily-Feeds.md")
    else:
        for channel in CONFIG["youtube_channels"]:
            fetch_youtube_channel(channel, test_mode)

    # 4. 生成 IELTS 学习日志 (若 raw_only 为 True，则由 Agent 在工作流中取代)
    if not raw_only:
        log.info("\n[生成] IELTS 学习日志 (标准模式)...")
        write_ielts_reminder(test_mode)
    else:
        log.info("\n[跳过] IELTS 日志生成被挂起，等待 SubAgent 智能策展填充...")

    # Save updated cache before exiting
    save_feed_cache(FEED_CACHE)
    log.info(f"[cache] Saved {len(FEED_CACHE)} GUIDs")

    log.info("=" * 60)
    log.info("每日拉取完成！")
    log.info("=" * 60)

# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PKM Daily Fetch Script")
    parser.add_argument("--test",     action="store_true", help="测试模式，不写入 Obsidian")
    parser.add_argument("--schedule", action="store_true", help="定时模式，每日 07:00 自动执行")
    parser.add_argument("--raw-only", action="store_true", help="仅生成供 PKM Agent 策展的 Raw Feeds (V2 优化)")
    args = parser.parse_args()

    if args.schedule:
        fetch_time = CONFIG.get("daily_fetch_time", "07:00")
        print(f"[定时模式] 每日 {fetch_time} 自动拉取。按 Ctrl+C 停止。")
        import schedule
        schedule.every().day.at(fetch_time).do(run_daily_fetch, raw_only=args.raw_only)
        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        run_daily_fetch(test_mode=args.test, raw_only=args.raw_only)

if __name__ == "__main__":
    main()
