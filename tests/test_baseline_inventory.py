from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from baseline_inventory import (
    EntrypointAttributor,
    EntrypointObservation,
    InventoryBuilder,
    ObservedWrite,
    WriteWindow,
    write_attribution_create_new,
    write_baseline_create_new,
)


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def test_duplicate_endpoint_preserves_two_observations_and_one_fetch_target() -> None:
    root = _case_root("duplicate-source")
    private_config = root / "private.json"
    public_config = root / "public.json"
    shared_feed = {
        "name": "Shared feed",
        "url": "https://example.com/feed.xml",
        "note_folder": "30-Daily",
        "enabled": True,
    }
    private_config.write_text(
        json.dumps({"rss_feeds": [shared_feed]}, ensure_ascii=False),
        encoding="utf-8",
    )
    public_config.write_text(
        json.dumps({"rss_feeds": [shared_feed]}, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest = InventoryBuilder().scan_config_files([private_config, public_config])

    assert len(manifest.observed_sources) == 2
    assert len({record.observed_record_id for record in manifest.observed_sources}) == 2
    assert len(manifest.fetch_targets) == 1
    assert manifest.fetch_targets[0].observed_record_ids == sorted(
        record.observed_record_id for record in manifest.observed_sources
    )
    assert manifest.observed_record_count_by_origin == {
        str(private_config.resolve()): 1,
        str(public_config.resolve()): 1,
    }


def test_disabled_sources_remain_observed_but_are_not_fetchable() -> None:
    root = _case_root("disabled-source")
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "rss_feeds": [
                    {
                        "name": "Disabled",
                        "url": "https://example.com/disabled.xml",
                        "note_folder": "30-Daily",
                        "enabled": False,
                    }
                ],
                "youtube_channels": [
                    {
                        "name": "Enabled video",
                        "channel_id": "UC123",
                        "note_folder": "20-Sources/Videos",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = InventoryBuilder().scan_config_files([config])

    assert len(manifest.observed_sources) == 2
    assert manifest.disabled_source_count == 1
    assert manifest.fetchable_source_count == 1
    assert [target.adapter for target in manifest.fetch_targets] == ["youtube"]


def test_prior_baseline_preserves_observation_identity() -> None:
    root = _case_root("stable-identity")
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "rss_feeds": [
                    {
                        "name": "Feed",
                        "url": "https://example.com/feed.xml",
                        "note_folder": "30-Daily",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    builder = InventoryBuilder()
    first = builder.scan_config_files([config])
    second = builder.scan_config_files(
        [config],
        previous_records=first.observed_sources,
    )

    assert second.observed_sources[0].observed_record_id == (
        first.observed_sources[0].observed_record_id
    )
    assert second.observed_sources[0].source_id == first.observed_sources[0].source_id
    assert second.observed_sources[0].first_seen_at == (first.observed_sources[0].first_seen_at)


def test_workflow_urls_are_retained_as_unresolved_references() -> None:
    root = _case_root("workflow-reference")
    workflow = root / "pkm-daily.md"
    workflow.write_text(
        "Fetch https://example.com/manual-source and then render the digest.",
        encoding="utf-8",
    )

    manifest = InventoryBuilder().scan_config_files([], workflow_files=[workflow])

    assert manifest.unresolved_reference_count == 1
    reference = manifest.unresolved_references[0]
    assert reference.value == "https://example.com/manual-source"
    assert reference.origin_object_id == str(workflow.resolve())


def test_baseline_writer_never_overwrites_an_existing_generation() -> None:
    root = _case_root("create-new-baseline")
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "rss_feeds": [
                    {
                        "name": "Feed",
                        "url": "https://example.com/feed.xml",
                        "note_folder": "30-Daily",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = InventoryBuilder().scan_config_files([config])
    target = root / "baseline.json"

    write_baseline_create_new(target, manifest)
    original = target.read_bytes()

    with pytest.raises(FileExistsError):
        write_baseline_create_new(target, manifest)

    assert target.read_bytes() == original


def test_vault_baseline_records_files_and_empty_directories_without_modifying_them() -> None:
    root = _case_root("vault-baseline")
    vault = root / "vault"
    empty = vault / "empty"
    empty.mkdir(parents=True)
    note = vault / "note.md"
    note.write_text("---\nsource: https://example.com\n---\n", encoding="utf-8")
    original = note.read_bytes()

    baseline = InventoryBuilder().scan_vault_paths(vault)

    assert {entry.relative_path for entry in baseline.entries} == {
        ".",
        "empty",
        "note.md",
    }
    note_entry = next(entry for entry in baseline.entries if entry.relative_path == "note.md")
    assert note_entry.is_directory is False
    assert len(note_entry.sha256 or "") == 64
    assert note.read_bytes() == original
    assert empty.is_dir()


def test_entrypoint_attribution_covers_all_owner_kinds_and_known_window() -> None:
    timestamp = datetime(2026, 7, 26, 9, 13, tzinfo=timezone.utc)
    task = EntrypointObservation(
        kind="windows_task",
        object_id="\\PKM-Daily-Fetcher",
        state="READY",
        definition_hash="a" * 64,
        evidence_ref="task-readonly-snapshot",
        write_targets=("D:/personal/ObsidianVault/00-Inbox",),
        active_windows=(
            WriteWindow(
                started_at=datetime(2026, 7, 26, 9, 9, tzinfo=timezone.utc),
                ended_at=datetime(2026, 7, 26, 9, 14, tzinfo=timezone.utc),
            ),
        ),
        definition_text=("fetch https://example.test/extra-source through the task"),
    )
    observations = [
        task,
        *[
            EntrypointObservation(
                kind=kind,
                object_id=f"{kind}:none",
                state="ABSENT",
                definition_hash="b" * 64,
                evidence_ref=f"{kind}-readonly-check",
            )
            for kind in EntrypointAttributor.REQUIRED_KINDS
            if kind != "windows_task"
        ],
    ]
    write = ObservedWrite(
        path="D:/personal/ObsidianVault/00-Inbox/Raw-Daily-Feeds.md",
        observed_at=timestamp,
        evidence_ref="fetch.log:2026-07-26T17:13:08+08:00",
    )

    report = EntrypointAttributor().attribute(observations, [write])

    assert report.blocked is False
    assert report.attributions[0].owner_object_id == "\\PKM-Daily-Fetcher"
    assert set(report.covered_kinds) == set(EntrypointAttributor.REQUIRED_KINDS)
    assert report.unresolved_references[0].value == ("https://example.test/extra-source")


def test_unattributed_write_blocks_shadow_and_gate_a_without_mutating_inputs() -> None:
    root = _case_root("unattributed-write")
    report_path = root / "entrypoint-attribution.json"
    report = EntrypointAttributor().attribute(
        [
            EntrypointObservation(
                kind=kind,
                object_id=f"{kind}:none",
                state="ABSENT",
                definition_hash="c" * 64,
                evidence_ref=f"{kind}-readonly-check",
            )
            for kind in EntrypointAttributor.REQUIRED_KINDS
        ],
        [
            ObservedWrite(
                path="D:/personal/ObsidianVault/00-Inbox/unknown.md",
                observed_at=datetime(2026, 7, 26, 9, 20, tzinfo=timezone.utc),
                evidence_ref="filesystem-observation",
            )
        ],
    )

    write_attribution_create_new(report_path, report)
    original = report_path.read_bytes()

    assert report.blocked is True
    assert report.attributions[0].status == "UNATTRIBUTED_WRITE"
    with pytest.raises(RuntimeError, match="UNATTRIBUTED_WRITE"):
        report.assert_ready_for_shadow_or_gate_a()
    with pytest.raises(FileExistsError):
        write_attribution_create_new(report_path, report)
    assert report_path.read_bytes() == original
