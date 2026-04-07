#!/usr/bin/env python3
"""
pkm_bridge.py - Antigravity -> Obsidian PKM Bridge

Writes markdown notes to Obsidian via Local REST API.
Supports single-note and JSON batch mode.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

import formatter
import writer

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"

# Force UTF-8 output on Windows terminals
stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
if callable(stdout_reconfigure):
    stdout_reconfigure(encoding="utf-8", errors="replace")
if callable(stderr_reconfigure):
    stderr_reconfigure(encoding="utf-8", errors="replace")

load_dotenv(SCRIPT_DIR / ".env")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve_runtime_config(config: dict) -> tuple[str, str, str]:
    """Resolve (vault_path, api_base, api_key) from env/config/plugin fallback."""
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH", config.get("vault_path", "D:/personal/Obsidian"))
    obsidian_api = config.get("obsidian_api", {})

    api_base = os.getenv(
        "OBSIDIAN_API_BASE", obsidian_api.get("base_url", "http://localhost:27123")
    )
    api_key = os.getenv("OBSIDIAN_API_KEY", obsidian_api.get("api_key", ""))

    plugin_data = (
        Path(vault_path) / ".obsidian" / "plugins" / "obsidian-local-rest-api" / "data.json"
    )
    if plugin_data.exists():
        try:
            plugin_cfg = json.loads(plugin_data.read_text(encoding="utf-8"))
            if not os.getenv("OBSIDIAN_API_KEY"):
                api_key = plugin_cfg.get("apiKey", api_key)
            if not os.getenv("OBSIDIAN_API_BASE"):
                insecure = plugin_cfg.get("enableInsecureServer", False)
                if insecure and plugin_cfg.get("insecurePort"):
                    api_base = f"http://localhost:{plugin_cfg['insecurePort']}"
                elif plugin_cfg.get("port"):
                    api_base = f"https://localhost:{plugin_cfg['port']}"
        except Exception:
            pass

    return vault_path, api_base, api_key


CONFIG = load_config()
VAULT_PATH, API_BASE, API_KEY = resolve_runtime_config(CONFIG)
DOMAIN_MAP = CONFIG.get("domain_mapping", {})
VALID_ENTITY_TYPES = {"concept", "paper", "tool", "person", "synthesis"}


def api_headers() -> dict:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "text/markdown",
    }


def resolve_folder(domain: str, status: str) -> str:
    if status == "unreviewed":
        return "00-Inbox"
    domain_lower = domain.lower()
    for key, folder in DOMAIN_MAP.items():
        if key in domain_lower:
            return folder
    return "00-Inbox"


def append_to_moc(domain: str, note_title: str, note_path: str) -> None:
    moc_map = {
        "programming": "40-MOC/MOC-Programming.md",
        "math-modeling": "40-MOC/MOC-Math-Modeling.md",
        "data-science": "40-MOC/MOC-Data-Science.md",
        "research": "40-MOC/MOC-Research.md",
        "ielts": "40-MOC/MOC-IELTS.md",
        "ai-stack": "40-MOC/MOC-Programming.md",
    }

    target = None
    domain_lower = domain.lower()
    for key, moc_path in moc_map.items():
        if key in domain_lower:
            target = moc_path
            break
    if not target:
        return

    today = formatter.today_str()
    append_content = f"\n- [[{note_title}]] - {today}"
    writer.append_via_api(API_BASE, target, append_content, api_headers())


def append_log(log_entry: str) -> None:
    log_path = "40-MOC/log.md"
    today = formatter.today_str()
    content = f"\n\n## [{today}] {log_entry}"
    writer.append_via_api(API_BASE, log_path, content, api_headers())


def check_api_connection() -> tuple[bool, str]:
    """Validate auth against a protected endpoint to avoid root false positives."""
    if not API_KEY:
        return False, "OBSIDIAN_API_KEY is not set."

    probe_path = "00-Inbox/Raw-Daily-Feeds.md"
    endpoint = writer._vault_endpoint(probe_path)
    url = f"{API_BASE.rstrip('/')}{endpoint}"
    try:
        r = requests.get(url, headers=api_headers(), timeout=5)
    except requests.exceptions.RequestException as exc:
        return False, f"request failed: {exc}"

    if r.status_code in (200, 404):
        return True, f"auth ok (status={r.status_code})"
    if r.status_code in (401, 403):
        return False, f"auth failed (status={r.status_code})"
    return False, f"unexpected status={r.status_code}"


def process_note(
    title: str,
    content: str,
    domain: str,
    tags: list,
    source: str = "User Content",
    language: str = "cn",
    note_type: str = "permanent-note",
    related: list | None = None,
    status: str = "unreviewed",
    core_concept: str = "",
    entity_type: str = "concept",
    overwrite: bool = False,
    update_index: bool = False,
    update_log: str = "",
    source_count: int = 1,
    sources: list[str] | None = None,
) -> str:
    if entity_type not in VALID_ENTITY_TYPES:
        print(f"[WARN] Unknown entity_type '{entity_type}', fallback to 'concept'.")
        entity_type = "concept"

    # Placeholder for backward-compatible CLI flag. Index update can be
    # delegated to external Dataview workflows.
    _ = update_index

    folder = resolve_folder(domain, status)
    filename = formatter.slugify(title) + ".md"
    filepath = f"{folder}/{filename}"

    note_md = formatter.build_note(
        title=title,
        content=content,
        tags=tags,
        domain=domain,
        source=source,
        language=language,
        note_type=note_type,
        related=related,
        status=status,
        core_concept=core_concept,
        entity_type=entity_type,
        source_count=source_count,
        sources=sources,
    )

    success = writer.write_via_api(
        API_BASE,
        filepath,
        note_md,
        api_headers(),
        overwrite=overwrite,
    )
    if success:
        append_to_moc(domain, title, filepath)
        if update_log:
            append_log(update_log)
        print(f"[INFO] entity_type={entity_type}, domain={domain}, sources={source_count}")
    return filepath


def process_json_file(json_path: str) -> None:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        for item in data:
            process_note(**item)
    else:
        process_note(**data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Antigravity -> Obsidian PKM Bridge")
    parser.add_argument("--title", help="Note title")
    parser.add_argument("--content", help="Note body content")
    parser.add_argument("--domain", default="general", help="Knowledge domain")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--source", default="User Content", help="Source citation")
    parser.add_argument("--language", default="cn", help="Language (cn/en)")
    parser.add_argument("--type", default="permanent-note", help="Note type")
    parser.add_argument("--status", default="unreviewed", help="Status")
    parser.add_argument("--core-concept", default="", help="Core concept summary")
    parser.add_argument("--entity-type", default="concept", help="Entity type")
    parser.add_argument("--related", default="", help="Comma-separated internal links")
    parser.add_argument("--sources", default="", help="Comma-separated source list")
    parser.add_argument("--source-count", type=int, default=1, help="Source count")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing note")
    parser.add_argument(
        "--update-index", action="store_true", help="Keep CLI parity for index updates"
    )
    parser.add_argument("--update-log", default="", help="Append an operation log entry")
    parser.add_argument("--json", help="Batch import from JSON")
    parser.add_argument("--test", action="store_true", help="Test API connectivity")

    args = parser.parse_args()

    if args.test:
        ok, message = check_api_connection()
        if ok:
            print(f"[OK] API test passed: {message}")
            raise SystemExit(0)
        print(f"[ERR] API test failed: {message}")
        raise SystemExit(1)

    if args.json:
        process_json_file(args.json)
        return

    if not args.title or not args.content:
        parser.print_help()
        raise SystemExit(1)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    related = [r.strip() for r in args.related.split(",") if r.strip()]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    process_note(
        title=args.title,
        content=args.content,
        domain=args.domain,
        tags=tags,
        source=args.source,
        language=args.language,
        note_type=args.type,
        related=related,
        status=args.status,
        core_concept=getattr(args, "core_concept", ""),
        entity_type=getattr(args, "entity_type", "concept"),
        overwrite=args.overwrite,
        update_index=args.update_index,
        update_log=args.update_log,
        source_count=args.source_count,
        sources=sources,
    )


if __name__ == "__main__":
    main()
