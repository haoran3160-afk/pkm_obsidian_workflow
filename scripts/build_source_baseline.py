#!/usr/bin/env python3
"""Build one create-new source baseline from explicitly named config files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pkm_workflow.source_import import build_source_baseline, write_source_baseline


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--document",
        action="append",
        required=True,
        metavar="ORIGIN=PATH",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    documents = []
    for specification in args.document:
        if "=" not in specification:
            parser.error("--document must use ORIGIN=PATH")
        origin, raw_path = specification.split("=", 1)
        path = Path(raw_path).resolve(strict=True)
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            parser.error(f"config must be a JSON object: {path}")
        documents.append((origin, value, hashlib.sha256(raw).hexdigest()))

    baseline = build_source_baseline(tuple(documents))
    target = write_source_baseline(args.output, baseline)
    print(
        json.dumps(
            {
                "baseline_id": baseline.baseline_id,
                "record_count": len(baseline.records),
                "output": str(target),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
