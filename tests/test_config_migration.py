from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from config_migration import (
    migrate_config_documents,
    write_config_binding,
    write_config_generation,
)


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def test_migration_keeps_origin_records_separate_and_unions_settings() -> None:
    shared = {
        "name": "Same endpoint",
        "url": "https://example.com/feed.xml",
        "note_folder": "30-Daily",
    }
    private = {
        "vault_path": "D:/vault",
        "rss_feeds": [shared, {**shared, "name": "Disabled", "enabled": False}],
        "youtube_channels": [],
        "ielts_resources": {
            "practice_sites": [
                {
                    "name": "IELTS practice",
                    "url": "https://example.com/ielts",
                    "skills": ["reading"],
                }
            ],
            "books": ["Book A"],
        },
        "validate_ielts_urls": True,
        "obsidian_api": {
            "base_url": "http://localhost:27123",
            "api_key": "private-secret",
        },
    }
    public = {
        "rss_feeds": [shared],
        "youtube_channels": [],
        "daily_digest_top_picks": 8,
        "enable_fulltext_enrichment": True,
        "obsidian_api": {
            "base_url": "http://localhost:27123",
            "api_key": "public-secret",
        },
    }

    migrated = migrate_config_documents(
        [("private", private), ("public", public)],
        migration_id="migration-001",
    )

    assert migrated.schema_version == 3
    assert migrated.initial_mode == "shadow"
    assert set(migrated.settings) >= {
        "vault_path",
        "validate_ielts_urls",
        "daily_digest_top_picks",
        "enable_fulltext_enrichment",
        "obsidian_api",
    }
    assert len(migrated.source_records) == 5
    rss_records = [record for record in migrated.source_records if record.collection == "rss_feeds"]
    assert len(rss_records) == 3
    assert len({record.observed_record_id for record in rss_records}) == 3
    assert sum(not record.enabled for record in rss_records) == 1
    assert migrated.credential_refs == {"obsidian_api.api_key": "credential://obsidian/rest-api"}
    serialized = migrated.model_dump_json()
    assert "private-secret" not in serialized
    assert "public-secret" not in serialized


def test_migration_preserves_source_identity_from_previous_generation() -> None:
    document = {
        "rss_feeds": [
            {
                "name": "Feed",
                "url": "https://example.com/feed.xml",
                "note_folder": "30-Daily",
            }
        ],
        "youtube_channels": [],
    }
    first = migrate_config_documents(
        [("private", document)],
        migration_id="migration-001",
    )
    second = migrate_config_documents(
        [("private", document)],
        migration_id="migration-002",
        previous_generation=first,
    )

    assert second.source_records[0].observed_record_id == (
        first.source_records[0].observed_record_id
    )
    assert second.source_records[0].source_id == first.source_records[0].source_id


def test_generation_and_binding_are_create_new_and_retryable() -> None:
    root = _case_root("config-generation")
    generations = root / "config-generations"
    bindings = root / "config-bindings"
    generations.mkdir()
    bindings.mkdir()
    migrated = migrate_config_documents(
        [
            (
                "private",
                {
                    "rss_feeds": [
                        {
                            "name": "Feed",
                            "url": "https://example.com/feed.xml",
                            "note_folder": "30-Daily",
                        }
                    ],
                    "youtube_channels": [],
                },
            )
        ],
        migration_id="migration-001",
    )

    generation = write_config_generation(generations, migrated)
    same_generation = write_config_generation(generations, migrated)
    binding = write_config_binding(
        bindings,
        generation,
        reason="validated-migration",
    )

    assert same_generation == generation
    assert generation.name.startswith("pkm_config.v3-migration-001-")
    assert json.loads(binding.read_text(encoding="utf-8"))["generation_path"] == str(generation)
    assert len(tuple(generations.iterdir())) == 1
    assert len(tuple(bindings.iterdir())) == 1
