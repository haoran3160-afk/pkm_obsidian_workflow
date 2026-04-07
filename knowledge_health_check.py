#!/usr/bin/env python3
"""
knowledge_health_check.py - PKM knowledge base health check.

Scans 10-Notes for:
1) Missing required frontmatter
2) Unsourced pages
3) Orphan pages
4) Ghost wikilinks
5) Stale pages (updated > 60 days)
6) One-way links and thin-source pages

Outputs:
- 40-MOC/lint-report-YYYY-MM-DD.md
- Optional append to 40-MOC/log.md
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REQUIRED_FIELDS = {"title", "domain", "status", "tags", "source_count"}
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class NoteRecord:
    path: Path
    title: str
    frontmatter: dict
    outlinks: set[str] = field(default_factory=set)
    incoming: int = 0


def load_config(script_dir: Path) -> dict:
    return json.loads((script_dir / "pkm_config.json").read_text(encoding="utf-8"))


def parse_frontmatter(md_text: str) -> tuple[dict, str]:
    if not md_text.startswith("---\n"):
        return {}, md_text

    end_idx = md_text.find("\n---", 4)
    if end_idx < 0:
        return {}, md_text

    fm_text = md_text[4:end_idx]
    body = md_text[end_idx + 4 :].lstrip("\n")
    data: dict = {}
    current_key = None

    for raw_line in fm_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            data[current_key].append(line[4:].strip().strip('"'))
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key

        if value == "":
            data[key] = []
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [x.strip().strip('"') for x in inner.split(",") if x.strip()] if inner else []
            continue

        clean_value = value.strip('"')
        if clean_value.isdigit():
            data[key] = int(clean_value)
        else:
            data[key] = clean_value

    return data, body


def normalize_link_target(raw_target: str) -> str:
    target = raw_target.split("|", 1)[0]
    target = target.split("#", 1)[0].strip()
    target = target.replace("\\", "/")
    if target.endswith(".md"):
        target = target[:-3]
    if "/" in target:
        target = target.split("/")[-1]
    return target.lower()


def extract_wikilinks(body: str) -> set[str]:
    return {normalize_link_target(m.group(1)) for m in WIKILINK_RE.finditer(body)}


def to_dt(value: str) -> datetime | None:
    if not value:
        return None

    candidates = [value, value.replace("Z", ""), value.replace("/", "-")]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
        try:
            return datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError:
            pass
    return None


def build_records(vault_path: Path) -> list[NoteRecord]:
    records: list[NoteRecord] = []
    notes_root = vault_path / "10-Notes"
    if not notes_root.exists():
        return records

    for md_file in sorted(notes_root.rglob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        title = str(fm.get("title", md_file.stem)).strip('"').strip() or md_file.stem
        records.append(
            NoteRecord(
                path=md_file,
                title=title,
                frontmatter=fm,
                outlinks=extract_wikilinks(body),
            )
        )
    return records


def generate_report(vault_path: Path, max_items: int = 30, write_log: bool = True) -> tuple[Path, dict]:
    records = build_records(vault_path)
    now = datetime.now()

    title_map: dict[str, NoteRecord] = {}
    for rec in records:
        title_map[rec.title.lower()] = rec
        title_map[rec.path.stem.lower()] = rec

    ghost_links: list[tuple[Path, str]] = []
    one_way_links: list[tuple[str, str]] = []
    missing_frontmatter: list[Path] = []
    unsourced: list[Path] = []
    stale_pages: list[tuple[Path, int]] = []
    thin_sources: list[Path] = []

    for rec in records:
        for tgt in rec.outlinks:
            target_rec = title_map.get(tgt)
            if target_rec:
                target_rec.incoming += 1
                if rec.title.lower() not in target_rec.outlinks:
                    one_way_links.append((rec.title, target_rec.title))
            else:
                ghost_links.append((rec.path, tgt))

    orphans = [rec.path for rec in records if rec.incoming == 0]

    for rec in records:
        fm = rec.frontmatter
        if not fm or not REQUIRED_FIELDS.issubset(set(fm.keys())):
            missing_frontmatter.append(rec.path)

        source_count = int(fm.get("source_count", 0) or 0)
        sources = fm.get("sources", [])
        if source_count <= 0 or (isinstance(sources, list) and len(sources) == 0):
            unsourced.append(rec.path)
        if source_count == 1:
            thin_sources.append(rec.path)

        updated_dt = to_dt(str(fm.get("updated", "")))
        if updated_dt and (now - updated_dt).days > 60:
            stale_pages.append((rec.path, (now - updated_dt).days))

    critical_count = len(unsourced)
    warning_count = len(orphans) + len(ghost_links) + len(stale_pages) + len(missing_frontmatter)
    suggestion_count = len(one_way_links) + len(thin_sources)
    score = max(0, 100 - critical_count * 10 - warning_count * 3 - suggestion_count)

    today = now.strftime("%Y-%m-%d")
    report_path = vault_path / "40-MOC" / f"lint-report-{today}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def fmt_paths(paths: list[Path], limit: int = max_items) -> str:
        if not paths:
            return "- *(none)*"
        return "\n".join(f"- [[{p.stem}]]" for p in paths[:limit])

    stale_lines = (
        "\n".join(f"- [[{p.stem}]] - updated {days} days ago" for p, days in stale_pages[:max_items])
        if stale_pages
        else "- *(none)*"
    )
    ghost_lines = (
        "\n".join(f"- [[{p.stem}]] -> [[{tgt}]]" for p, tgt in ghost_links[:max_items])
        if ghost_links
        else "- *(none)*"
    )
    one_way_lines = (
        "\n".join(f"- [[{a}]] -> [[{b}]]" for a, b in one_way_links[:max_items])
        if one_way_links
        else "- *(none)*"
    )

    report = f"""---
title: "Lint Report - {today}"
type: lint-report
date: {today}
---

# Knowledge Base Health Report - {today}

## Overview
- Total pages: {len(records)}
- Health score: {score}/100
- Critical: {critical_count}
- Warning: {warning_count}
- Suggestions: {suggestion_count}

## Critical ({critical_count})
### Unsourced pages
{fmt_paths(unsourced)}

## Warnings ({warning_count})
### Orphan pages ({len(orphans)})
{fmt_paths(orphans)}

### Ghost links ({len(ghost_links)})
{ghost_lines}

### Stale pages ({len(stale_pages)})
{stale_lines}

### Missing frontmatter ({len(missing_frontmatter)})
{fmt_paths(missing_frontmatter)}

## Suggestions ({suggestion_count})
### Thin sources (source_count == 1) ({len(thin_sources)})
{fmt_paths(thin_sources)}

### One-way links ({len(one_way_links)})
{one_way_lines}
"""

    report_path.write_text(report, encoding="utf-8")

    if write_log:
        log_path = vault_path / "40-MOC" / "log.md"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_entry = (
            f"\n\n## [{today}] lint | weekly health check"
            f"\n- Score: {score}/100"
            f"\n- Critical: {critical_count}, Warning: {warning_count}, Suggestions: {suggestion_count}"
            f"\n- Orphans: {len(orphans)}, Ghost links: {len(ghost_links)}, Stale: {len(stale_pages)}"
        )
        with log_path.open("a", encoding="utf-8") as f:
            f.write(log_entry)

    summary = {
        "score": score,
        "total": len(records),
        "critical": critical_count,
        "warning": warning_count,
        "suggestion": suggestion_count,
        "report_path": report_path.as_posix(),
    }
    return report_path, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="PKM Knowledge Health Check")
    parser.add_argument("--vault-path", default="", help="Obsidian vault path")
    parser.add_argument("--max-items", type=int, default=30, help="max items per section")
    parser.add_argument("--no-log", action="store_true", help="do not append 40-MOC/log.md")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    config = load_config(script_dir)
    vault_path = Path(args.vault_path or config.get("vault_path", "D:/personal/Obsidian"))

    report_path, summary = generate_report(
        vault_path,
        max_items=args.max_items,
        write_log=not args.no_log,
    )
    print(
        f"[OK] lint done | score={summary['score']}/100 "
        f"| critical={summary['critical']} warning={summary['warning']} suggestion={summary['suggestion']}"
    )
    print(f"[OK] report: {report_path}")


if __name__ == "__main__":
    main()
