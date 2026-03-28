#!/usr/bin/env python3
"""
main.py — Obsidian PKM Workflow Orchestrator
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
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

import fetcher
import formatter
import writer
from config_schema import PKMConfig, load_and_validate

# ── Console / Logging Setup ───────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
LOG_PATH = SCRIPT_DIR / "fetch.log"
console = Console()

# Force UTF-8 stdout
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except AttributeError:
    pass

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
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

# ── Load Config + Environment ─────────────────────────────────────────────────

load_dotenv(SCRIPT_DIR / ".env")

CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"
CACHE_PATH = SCRIPT_DIR / "feed_cache.json"

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
WRITE_MODE = os.getenv("PKM_WRITE_MODE", CONFIG.write_mode)

if VAULT_PATH == "YOUR_VAULT_PATH":
    log.warning("event", message="OBSIDIAN_VAULT_PATH not set in .env. Files won't be saved correctly!")

# ── Cache Management ──────────────────────────────────────────────────────────


def load_feed_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
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


# ── Writer Dispatch ───────────────────────────────────────────────────────────

def _write(filepath: str, content: str, dry_run: bool = False) -> bool:
    """
    Dispatch a write operation based on WRITE_MODE config.
    In dry_run mode, only print the target path — no actual I/O.
    """
    if dry_run:
        console.print(f"  [dim cyan][dry-run][/] would write → [bold]{filepath}[/]")
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


# ── Utility Helpers ───────────────────────────────────────────────────────────

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


# ── Run Report ────────────────────────────────────────────────────────────────

def _build_run_report(test_mode: bool, raw_only: bool, dry_run: bool) -> dict[str, Any]:
    return {
        "mode": "TEST" if test_mode else ("DRY-RUN" if dry_run else "LIVE"),
        "raw_only": raw_only,
        "rss_sources": [],     # list of {name, items, ok, elapsed}
        "yt_sources": [],      # list of {name, items, ok, elapsed}
        "writes_ok": 0,
        "writes_failed": 0,
        "written_files": [],
        "archived_raw_files": 0,
        "start_time": time.monotonic(),
    }


def _record_write(report: dict[str, Any], filepath: str, ok: bool) -> None:
    if ok:
        report["writes_ok"] += 1
        report["written_files"].append(filepath)
    else:
        report["writes_failed"] += 1


def _print_rich_summary(report: dict[str, Any]) -> None:
    """Render a Rich table summarizing the run results to stdout."""
    duration = time.monotonic() - report["start_time"]
    mode_label = report["mode"]

    console.rule(f"[bold green]PKM Run Summary — {mode_label}[/]")

    # Sources table
    table = Table(title="Feed Sources", border_style="dim", show_lines=True)
    table.add_column("Source", style="cyan", no_wrap=True)
    table.add_column("Type", justify="center")
    table.add_column("Items", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Time (s)", justify="right")

    for src in report["rss_sources"]:
        status = "[green]✅ OK[/]" if src["ok"] else "[red]❌ Fail[/]"
        table.add_row(src["name"], "RSS", str(src["items"]), status, f"{src['elapsed']:.1f}")

    for src in report["yt_sources"]:
        status = "[green]✅ OK[/]" if src["ok"] else "[red]❌ Fail[/]"
        table.add_row(src["name"], "YouTube", str(src["items"]), status, f"{src['elapsed']:.1f}")

    console.print(table)

    # Summary row
    console.print(
        f"[bold]Files written:[/] {report['writes_ok']}  "
        f"[red]Failed:[/] {report['writes_failed']}  "
        f"[dim]Archived raw files:[/] {report['archived_raw_files']}  "
        f"[dim]Duration:[/] {duration:.1f}s"
    )

    if report["written_files"]:
        preview = report["written_files"][:5]
        suffix = " ..." if len(report["written_files"]) > 5 else ""
        console.print(f"[dim]Output:[/] {', '.join(preview)}{suffix}")


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_runtime_or_raise(test_mode: bool, dry_run: bool) -> None:
    if test_mode or dry_run:
        return
    if VAULT_PATH == "YOUR_VAULT_PATH":
        raise RuntimeError("OBSIDIAN_VAULT_PATH is not configured. Run `python main.py --doctor` first.")
    vault_dir = Path(VAULT_PATH)
    if not vault_dir.exists():
        raise RuntimeError(f"OBSIDIAN_VAULT_PATH does not exist: {vault_dir}")
    if not vault_dir.is_dir():
        raise RuntimeError(f"OBSIDIAN_VAULT_PATH is not a directory: {vault_dir}")
    if not os.access(vault_dir, os.W_OK):
        raise RuntimeError(f"OBSIDIAN_VAULT_PATH is not writable: {vault_dir}")


# ── Doctor ────────────────────────────────────────────────────────────────────

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
            console.print(f"  [yellow]⚠[/]  {item}")

    if errors:
        console.print("\n[red]Errors:[/]")
        for item in errors:
            console.print(f"  [red]✗[/]  {item}")
        console.print("\n[bold red]Doctor result: FAILED[/]")
        return False

    console.print("\n[bold green]Doctor result: OK ✅[/]")
    return True


# ── Core Workflow ─────────────────────────────────────────────────────────────

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
    today = formatter.today_str()
    news_items: dict[str, list[dict]] = defaultdict(list)

    # ── 1. RSS Feeds ──────────────────────────────────────────────────────────
    for feed in CONFIG.rss_feeds:
        t0 = time.monotonic()
        try:
            items = fetcher.fetch_rss_feed(
                feed.model_dump(), feed_cache, today, MAX_PAPERS, raw_only
            )
            ok = True
        except Exception as exc:
            log.error("rss.fail", feed=feed.name, error=str(exc))
            report["rss_sources"].append(
                {"name": feed.name, "items": 0, "ok": False, "elapsed": time.monotonic() - t0}
            )
            continue

        report["rss_sources"].append(
            {"name": feed.name, "items": len(items), "ok": ok, "elapsed": time.monotonic() - t0}
        )

        if _is_paper_feed(feed.url):
            if raw_only:
                news_items[feed.name].extend(items)
            elif not test_mode:
                for paper in items:
                    path, content = formatter.format_paper_note(paper, feed.name)
                    _record_write(report, path, _write(path, content, dry_run))
        else:
            news_items[feed.name].extend(items)

    # ── 2. YouTube ────────────────────────────────────────────────────────────
    if raw_only:
        yt_raw_list: list[dict] = []
        for channel in CONFIG.youtube_channels:
            t0 = time.monotonic()
            try:
                videos = fetcher.fetch_youtube_channel_raw(channel.model_dump())
                ok = True
            except Exception as exc:
                log.error("yt.fail", channel=channel.name, error=str(exc))
                report["yt_sources"].append(
                    {"name": channel.name, "items": 0, "ok": False, "elapsed": time.monotonic() - t0}
                )
                continue
            report["yt_sources"].append(
                {"name": channel.name, "items": len(videos), "ok": ok, "elapsed": time.monotonic() - t0}
            )
            yt_raw_list.extend(videos)

        if yt_raw_list:
            news_items["YouTube"].extend(yt_raw_list)

        deduped: dict[str, list[dict]] = {
            src: _dedupe_items(itms) for src, itms in news_items.items() if _dedupe_items(itms)
        }
        if deduped:
            path, content = formatter.format_daily_digest(deduped, raw_only=True)
            if test_mode:
                log.info("test.digest.preview", path=path, preview=content[:200])
            else:
                ok_write = _write(path, content, dry_run)
                _record_write(report, path, ok_write)
                if ok_write and not dry_run:
                    report["archived_raw_files"] = _archive_old_raw_feeds(VAULT_PATH, RAW_FEED_KEEP_DAYS)
    else:
        for channel in CONFIG.youtube_channels:
            t0 = time.monotonic()
            try:
                videos = fetcher.fetch_youtube_channel(
                    channel.model_dump(), feed_cache, today, MAX_VIDEOS
                )
                ok = True
            except Exception as exc:
                log.error("yt.fail", channel=channel.name, error=str(exc))
                report["yt_sources"].append(
                    {"name": channel.name, "items": 0, "ok": False, "elapsed": time.monotonic() - t0}
                )
                continue
            report["yt_sources"].append(
                {"name": channel.name, "items": len(videos), "ok": ok, "elapsed": time.monotonic() - t0}
            )
            if not test_mode:
                for v in videos:
                    path, content = formatter.format_video_note(v)
                    _record_write(report, path, _write(path, content, dry_run))

        # ── 3. Daily Digest ───────────────────────────────────────────────────
        if news_items:
            path, content = formatter.format_daily_digest(dict(news_items), raw_only=False)
            if test_mode:
                log.info("test.digest.preview", path=path, preview=content[:200])
            else:
                _record_write(report, path, _write(path, content, dry_run))

    # ── 4. IELTS ──────────────────────────────────────────────────────────────
    if not raw_only:
        path, content = formatter.format_ielts_reminder()
        if test_mode:
            log.info("test.ielts.preview", path=path)
        else:
            _record_write(report, path, _write(path, content, dry_run))
    else:
        log.info("ielts.skipped", reason="raw_only / agent mode")

    # ── Finalize ──────────────────────────────────────────────────────────────
    if not dry_run:
        save_feed_cache(feed_cache)
        log.info("cache.saved", guid_count=len(feed_cache))

    _print_rich_summary(report)
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

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
    args = parser.parse_args()

    if args.doctor:
        ok = run_doctor(check_network=not args.doctor_skip_network)
        raise SystemExit(0 if ok else 1)

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
