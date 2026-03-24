#!/usr/bin/env python3
"""
pkm_bridge.py — Antigravity → Obsidian PKM Bridge
Sends Markdown notes directly to your Obsidian Vault using the Local REST API.
Includes automatic retries via writer.py

Usage:
  python pkm_bridge.py --title "Note Title" --content "Body" --domain "python" --tags "python,code"
  python pkm_bridge.py --json note.json
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import formatter
import writer

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"

# ── Load Config ───────────────────────────────────────────────────────────────

load_dotenv(SCRIPT_DIR / ".env")

try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print("[Error] pkm_config.json not found.")
    sys.exit(1)

API_BASE = os.getenv("OBSIDIAN_API_BASE", CONFIG.get("obsidian_api", {}).get("base_url", ""))
API_KEY  = os.getenv("OBSIDIAN_API_KEY", "")
DOMAIN_MAP = CONFIG.get("domain_mapping", {})

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "text/markdown",
}

# ── Business Logic ────────────────────────────────────────────────────────────

def resolve_folder(domain: str, status: str) -> str:
    """Resolve target folder based on domain/status in config."""
    if status == "unreviewed":
        return "00-Inbox"
    domain_lower = domain.lower()
    for key, folder in DOMAIN_MAP.items():
        if key in domain_lower:
            return folder
    return "00-Inbox"


def append_to_moc(domain: str, note_title: str, note_path: str):
    """Append a link to the new note to the corresponding MOC (Map of Content)."""
    moc_map = {
        "Programming": "40-MOC/MOC-Programming.md",
        "Math-Modeling": "40-MOC/MOC-Math-Modeling.md",
        "Data-Science": "40-MOC/MOC-Data-Science.md",
        "Research": "40-MOC/MOC-Research.md",
        "IELTS": "40-MOC/MOC-IELTS.md",
        "AI-Stack": "40-MOC/MOC-Programming.md",
    }
    moc_file = None
    for key, path in moc_map.items():
        if key.lower() in domain.lower():
            moc_file = path
            break
    if not moc_file:
        return

    today = formatter.today_str()
    append_content = f"\n- [[{note_title}]] — {today}"
    writer.append_via_api(API_BASE, moc_file, append_content, HEADERS)


def process_note(title: str, content: str, domain: str, tags: list,
                 source: str = "User Content", language: str = "cn",
                 note_type: str = "permanent-note", related: list = None,
                 status: str = "unreviewed", core_concept: str = "",
                 overwrite: bool = False):
    folder = resolve_folder(domain, status)
    filename = formatter.slugify(title) + ".md"
    filepath = f"{folder}/{filename}"

    note_md = formatter.build_note(
        title=title, content=content, tags=tags, domain=domain,
        source=source, language=language, note_type=note_type, related=related,
        status=status, core_concept=core_concept
    )

    success = writer.write_via_api(API_BASE, filepath, note_md, HEADERS, overwrite=overwrite)
    if success:
        append_to_moc(domain, title, filepath)
    return filepath


def process_json_file(json_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        for item in data:
            process_note(**item)
    else:
        process_note(**data)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Antigravity → Obsidian PKM Bridge")
    parser.add_argument("--title",    help="Note title")
    parser.add_argument("--content",  help="Note body content")
    parser.add_argument("--domain",   help="Knowledge domain (python/ielts...)", default="general")
    parser.add_argument("--tags",     help="Comma-separated tags", default="")
    parser.add_argument("--source",   help="Source citation", default="User Content")
    parser.add_argument("--language", help="Language (cn/en)", default="cn")
    parser.add_argument("--type",     help="Note type", default="permanent-note")
    parser.add_argument("--status",   help="Status (unreviewed/permanent)", default="unreviewed")
    parser.add_argument("--core-concept", help="Core concept TLDR", default="")
    parser.add_argument("--related",  help="Comma-separated internal links", default="")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing note")
    parser.add_argument("--json",     help="Batch import from JSON")
    parser.add_argument("--test",     action="store_true", help="Test API connectivity")

    args = parser.parse_args()

    if args.test:
        if not API_KEY:
            print("[Error] OBSIDIAN_API_KEY is not set in .env")
            sys.exit(1)
        ok = writer.check_api_connection(API_BASE, HEADERS)
        if ok:
            print("[✓] API Connection OK")
        else:
            print("[✗] API Connection Failed")
        return

    if args.json:
        process_json_file(args.json)
        return

    if not args.title or not args.content:
        parser.print_help()
        sys.exit(1)

    tags    = [t.strip() for t in args.tags.split(",")    if t.strip()]
    related = [r.strip() for r in args.related.split(",") if r.strip()]

    process_note(
        title=args.title, content=args.content, domain=args.domain,
        tags=tags, source=args.source, language=args.language,
        note_type=args.type, related=related, status=args.status,
        core_concept=getattr(args, 'core_concept', ''), overwrite=args.overwrite
    )


if __name__ == "__main__":
    main()
