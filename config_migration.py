"""Additive migration from legacy config documents to immutable v3 generations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config_schema import ConfigSourceRecord, PKMConfigV3

SOURCE_COLLECTIONS = {"rss_feeds", "youtube_channels", "ielts_resources"}
SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|credential)(?:$|_)",
    re.IGNORECASE,
)
BINDING_PATTERN = re.compile(r"^(\d{8})-[0-9a-f]{16}\.json$")


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_create_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _sanitize_setting(
    value: Any,
    *,
    path: str,
    credential_refs: dict[str, str],
) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            nested_path = f"{path}.{key}" if path else str(key)
            if SECRET_KEY_PATTERN.search(str(key)):
                if nested not in (None, "", [], {}):
                    if nested_path == "obsidian_api.api_key":
                        credential_refs[nested_path] = "credential://obsidian/rest-api"
                    else:
                        credential_refs[nested_path] = "credential://pkm/" + nested_path.replace(
                            ".", "/"
                        )
                continue
            sanitized[str(key)] = _sanitize_setting(
                nested,
                path=nested_path,
                credential_refs=credential_refs,
            )
        return sanitized
    if isinstance(value, list):
        return [
            _sanitize_setting(
                item,
                path=f"{path}[]",
                credential_refs=credential_refs,
            )
            for item in value
        ]
    return value


def _iter_source_records(
    origin: str,
    document: dict[str, Any],
) -> Iterable[tuple[str, int, str, str, str, bool, dict[str, Any]]]:
    rss = document.get("rss_feeds", [])
    youtube = document.get("youtube_channels", [])
    if not isinstance(rss, list) or not isinstance(youtube, list):
        raise ValueError(f"source arrays must be lists in origin {origin}")

    for index, raw in enumerate(rss):
        if not isinstance(raw, dict):
            raise ValueError(f"rss_feeds[{index}] must be an object in {origin}")
        endpoint = str(raw.get("url", "")).strip()
        if not endpoint:
            raise ValueError(f"rss_feeds[{index}] has no URL in {origin}")
        yield (
            "rss_feeds",
            index,
            "rss",
            str(raw.get("name", "")).strip() or endpoint,
            endpoint,
            bool(raw.get("enabled", True)),
            raw,
        )

    for index, raw in enumerate(youtube):
        if not isinstance(raw, dict):
            raise ValueError(f"youtube_channels[{index}] must be an object in {origin}")
        channel = str(raw.get("channel_id", "")).strip()
        endpoint = str(raw.get("url", "")).strip()
        if channel:
            endpoint = f"https://www.youtube.com/channel/{channel}"
        if not endpoint:
            raise ValueError(f"youtube_channels[{index}] has no channel identity in {origin}")
        yield (
            "youtube_channels",
            index,
            "youtube",
            str(raw.get("name", "")).strip() or channel or endpoint,
            endpoint,
            bool(raw.get("enabled", True)),
            raw,
        )

    ielts = document.get("ielts_resources", {})
    if ielts in (None, {}):
        return
    if not isinstance(ielts, dict):
        raise ValueError(f"ielts_resources must be an object in {origin}")
    for collection, values in ielts.items():
        if not isinstance(values, list):
            raise ValueError(f"ielts_resources.{collection} must be a list in {origin}")
        for index, value in enumerate(values):
            raw = value if isinstance(value, dict) else {"value": value}
            endpoint = str(
                raw.get("url") or raw.get("channel_id") or raw.get("value") or ""
            ).strip()
            if not endpoint:
                raise ValueError(f"ielts_resources.{collection}[{index}] is empty in {origin}")
            adapter = (
                "youtube-url"
                if collection == "youtube_channels"
                else "book"
                if collection == "books"
                else "manual-url"
            )
            yield (
                f"ielts_resources.{collection}",
                index,
                adapter,
                str(raw.get("name") or raw.get("value") or endpoint).strip(),
                endpoint,
                bool(raw.get("enabled", True)),
                raw,
            )


def migrate_config_documents(
    documents: Sequence[tuple[str, dict[str, Any]]],
    *,
    migration_id: str,
    previous_generation: PKMConfigV3 | None = None,
) -> PKMConfigV3:
    """Create one additive v3 value; earlier origins win value conflicts."""

    if not migration_id.strip():
        raise ValueError("migration_id cannot be blank")
    if not documents:
        raise ValueError("at least one config document is required")
    origins = [origin for origin, _ in documents]
    if len(origins) != len(set(origins)):
        raise ValueError("config origin names must be unique")

    previous_by_legacy = {
        record.legacy_record_id: record
        for record in (previous_generation.source_records if previous_generation else [])
    }
    settings: dict[str, Any] = {}
    setting_origins: dict[str, list[str]] = {}
    credential_refs: dict[str, str] = {}
    source_records: list[ConfigSourceRecord] = []

    for origin, document in documents:
        if not isinstance(document, dict):
            raise ValueError(f"config origin must be an object: {origin}")
        for key, value in document.items():
            if key in SOURCE_COLLECTIONS:
                continue
            setting_origins.setdefault(key, []).append(origin)
            sanitized = _sanitize_setting(
                value,
                path=key,
                credential_refs=credential_refs,
            )
            if key not in settings:
                settings[key] = sanitized

        for (
            collection,
            index,
            adapter,
            name,
            endpoint,
            enabled,
            raw,
        ) in _iter_source_records(origin, document):
            legacy_record_id = f"{origin}::{collection}::{index}"
            previous = previous_by_legacy.get(legacy_record_id)
            sanitized_raw = _sanitize_setting(
                raw,
                path=f"source.{legacy_record_id}",
                credential_refs=credential_refs,
            )
            source_records.append(
                ConfigSourceRecord(
                    source_id=(previous.source_id if previous else f"source-{uuid.uuid4()}"),
                    observed_record_id=(
                        previous.observed_record_id if previous else f"observed-{uuid.uuid4()}"
                    ),
                    legacy_record_id=legacy_record_id,
                    origin=origin,
                    collection=collection,
                    origin_record_index=index,
                    adapter=adapter,
                    name=name,
                    endpoint=endpoint,
                    enabled=enabled,
                    raw_record_hash=_sha256(_canonical_json_bytes(sanitized_raw)),
                    raw_record=sanitized_raw,
                )
            )

    return PKMConfigV3(
        migration_id=migration_id,
        generation_id=f"config-{migration_id}-{uuid.uuid4()}",
        created_at=datetime.now(timezone.utc),
        settings=settings,
        setting_origins=setting_origins,
        source_records=source_records,
        credential_refs=credential_refs,
    )


def _generation_bytes(generation: PKMConfigV3) -> bytes:
    return (
        json.dumps(
            generation.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def write_config_generation(target_dir: Path, generation: PKMConfigV3) -> Path:
    directory = Path(target_dir).resolve(strict=True)
    payload = _generation_bytes(generation)
    digest = _sha256(payload)
    target = directory / (f"pkm_config.v3-{generation.migration_id}-{digest[:16]}.json")
    if target.exists():
        if target.read_bytes() != payload:
            raise FileExistsError(f"conflicting config generation exists: {target}")
        return target
    _write_create_new(target, payload)
    return target


def write_config_binding(
    target_dir: Path,
    generation_path: Path,
    *,
    reason: str,
) -> Path:
    directory = Path(target_dir).resolve(strict=True)
    generation = Path(generation_path).resolve(strict=True)
    if not reason.strip():
        raise ValueError("binding reason cannot be blank")
    generation_hash = _sha256(generation.read_bytes())
    latest = 0
    for path in directory.glob("*.json"):
        match = BINDING_PATTERN.fullmatch(path.name)
        if match is None:
            raise ValueError(f"invalid binding filename: {path.name}")
        latest = max(latest, int(match.group(1)))
    sequence = latest + 1
    target = directory / f"{sequence:08d}-{generation_hash[:16]}.json"
    payload = _canonical_json_bytes(
        {
            "schema_version": 1,
            "sequence": sequence,
            "generation_path": str(generation),
            "generation_sha256": generation_hash,
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _write_create_new(target, payload)
    return target
