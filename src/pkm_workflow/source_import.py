"""Deterministic, non-destructive import of every configured source record."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ImportedSourceRecord:
    source_id: str
    origin: str
    collection: str
    original_index: int
    raw_record_hash: str
    adapter: str
    name: str
    endpoint: str
    enabled: bool
    lane: None
    origin_group: None
    approval_state: str


@dataclass(frozen=True)
class SourceBaseline:
    schema_version: str
    baseline_id: str
    document_hashes: Mapping[str, str]
    records: tuple[ImportedSourceRecord, ...]


def build_source_baseline(
    documents: Sequence[tuple[str, Mapping[str, object], str]],
) -> SourceBaseline:
    if not documents:
        raise ValueError("at least one source document is required")
    origins = [origin for origin, _document, _hash in documents]
    if len(origins) != len(set(origins)):
        raise ValueError("source document origins must be unique")

    records: list[ImportedSourceRecord] = []
    document_hashes: dict[str, str] = {}
    for origin, document, document_hash in documents:
        if not origin or HASH_PATTERN.fullmatch(document_hash) is None:
            raise ValueError("origin and source document SHA-256 are required")
        document_hashes[origin] = document_hash
        for collection, original_index, raw, adapter, name, endpoint, enabled in _iter_records(
            origin, document
        ):
            raw_hash = hashlib.sha256(_canonical(raw)).hexdigest()
            records.append(
                ImportedSourceRecord(
                    source_id=stable_source_id(f"{origin}:{collection}", original_index, raw_hash),
                    origin=origin,
                    collection=collection,
                    original_index=original_index,
                    raw_record_hash=raw_hash,
                    adapter=adapter,
                    name=name,
                    endpoint=endpoint,
                    enabled=enabled,
                    lane=None,
                    origin_group=None,
                    # Classification is a later append-only human-approved
                    # generation. Import never silently assigns or drops a row.
                    approval_state="UNCLASSIFIED_REQUIRES_APPROVAL",
                )
            )

    identity_payload = {
        "schema_version": "7.3",
        "document_hashes": document_hashes,
        "records": [asdict(record) for record in records],
    }
    baseline_id = "source-baseline-" + hashlib.sha256(_canonical(identity_payload)).hexdigest()
    return SourceBaseline(
        schema_version="7.3",
        baseline_id=baseline_id,
        document_hashes=document_hashes,
        records=tuple(records),
    )


def _iter_records(
    origin: str,
    document: Mapping[str, object],
):
    rss = document.get("rss_feeds", [])
    youtube = document.get("youtube_channels", [])
    if not isinstance(rss, list) or not isinstance(youtube, list):
        raise ValueError(f"source arrays must be lists in {origin}")
    for index, value in enumerate(rss):
        raw = _object_record(value, f"{origin}.rss_feeds[{index}]")
        endpoint = str(raw.get("url", "")).strip()
        if not endpoint:
            raise ValueError(f"source endpoint is missing: {origin}.rss_feeds[{index}]")
        yield (
            "rss_feeds",
            index,
            raw,
            "rss",
            str(raw.get("name") or endpoint),
            endpoint,
            bool(raw.get("enabled", True)),
        )
    for index, value in enumerate(youtube):
        raw = _object_record(value, f"{origin}.youtube_channels[{index}]")
        channel = str(raw.get("channel_id", "")).strip()
        endpoint = str(raw.get("url", "")).strip()
        if channel:
            endpoint = "https://www.youtube.com/feeds/videos.xml?" + urlencode(
                {"channel_id": channel}
            )
        if not endpoint:
            raise ValueError(f"source endpoint is missing: {origin}.youtube_channels[{index}]")
        yield (
            "youtube_channels",
            index,
            raw,
            "youtube",
            str(raw.get("name") or channel or endpoint),
            endpoint,
            bool(raw.get("enabled", True)),
        )

    ielts = document.get("ielts_resources", {})
    if ielts in (None, {}):
        return
    if not isinstance(ielts, dict):
        raise ValueError(f"ielts_resources must be an object in {origin}")
    for subsection, values in ielts.items():
        if not isinstance(values, list):
            raise ValueError(f"ielts_resources.{subsection} must be a list")
        for index, value in enumerate(values):
            raw = value if isinstance(value, dict) else {"value": value}
            endpoint = str(
                raw.get("url") or raw.get("channel_id") or raw.get("value") or ""
            ).strip()
            if not endpoint:
                raise ValueError(
                    f"source endpoint is missing: {origin}.ielts_resources.{subsection}[{index}]"
                )
            adapter = (
                "youtube-url"
                if subsection == "youtube_channels"
                else "book"
                if subsection == "books"
                else "manual-url"
            )
            yield (
                f"ielts_resources.{subsection}",
                index,
                raw,
                adapter,
                str(raw.get("name") or raw.get("value") or endpoint),
                endpoint,
                bool(raw.get("enabled", True)),
            )


def _object_record(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"source record must be an object: {location}")
    return value


def write_source_baseline(target: Path, baseline: SourceBaseline) -> Path:
    destination = Path(target)
    parent = destination.parent.resolve(strict=True)
    payload = _canonical(
        {
            "schema_version": baseline.schema_version,
            "baseline_id": baseline.baseline_id,
            "document_hashes": dict(baseline.document_hashes),
            "records": [asdict(record) for record in baseline.records],
        }
    )
    with (parent / destination.name).open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return parent / destination.name


def stable_source_id(origin: str, original_index: int, raw_record_hash: str) -> str:
    """Derive identity without rewriting a null legacy ID or merging duplicates."""

    if not origin or original_index < 0:
        raise ValueError("source origin and non-negative original index are required")
    if HASH_PATTERN.fullmatch(raw_record_hash) is None:
        raise ValueError("raw_record_hash must be a lowercase SHA-256")
    digest = hashlib.sha256(
        f"{origin}\x00{original_index}\x00{raw_record_hash}".encode()
    ).hexdigest()
    return f"source-{digest[:32]}"


def validate_source_baseline(baseline: SourceBaseline) -> None:
    """Reject a mutated or ambiguous baseline before policy approval is applied."""

    if baseline.schema_version != "7.3":
        raise ValueError("unsupported source baseline schema")
    if not baseline.document_hashes or any(
        not origin or HASH_PATTERN.fullmatch(document_hash) is None
        for origin, document_hash in baseline.document_hashes.items()
    ):
        raise ValueError("source baseline document hashes are invalid")
    source_ids = [record.source_id for record in baseline.records]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source baseline IDs must be unique")
    for record in baseline.records:
        validate_imported_source_record(record)
    identity_payload = {
        "schema_version": baseline.schema_version,
        "document_hashes": dict(baseline.document_hashes),
        "records": [asdict(record) for record in baseline.records],
    }
    expected_baseline_id = (
        "source-baseline-" + hashlib.sha256(_canonical(identity_payload)).hexdigest()
    )
    if baseline.baseline_id != expected_baseline_id:
        raise ValueError("source baseline ID does not match canonical contents")


def validate_imported_source_record(record: ImportedSourceRecord) -> None:
    """Validate stable identity for baseline and approved append records."""

    expected_id = stable_source_id(
        f"{record.origin}:{record.collection}",
        record.original_index,
        record.raw_record_hash,
    )
    if record.source_id != expected_id:
        raise ValueError("source identity does not match record provenance")
    if (
        not record.endpoint
        or not record.adapter
        or record.lane is not None
        or record.origin_group is not None
        or record.approval_state != "UNCLASSIFIED_REQUIRES_APPROVAL"
    ):
        raise ValueError("source record is not an unclassified additive import")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
