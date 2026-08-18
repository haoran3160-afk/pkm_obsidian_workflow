from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

import state_schema
from run_store import RunStore
from state_schema import LegacyStateImporter, LegacyStateImportError, LegacyStateKind


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _write_new_json(path: Path, payload: object, *, bom: bool = False) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return raw


def _importer(
    root: Path,
    sources: dict[LegacyStateKind, Path],
) -> tuple[RunStore, LegacyStateImporter]:
    runtime = root / "runtime"
    runtime.mkdir()
    store = RunStore(runtime)
    return store, LegacyStateImporter(store=store, sources=sources)


def test_imports_all_four_legacy_states_as_events_without_changing_bytes() -> None:
    root = _case_root("legacy-all")
    fixtures = {
        LegacyStateKind.FEED_CACHE: {"https://example.com/a": "2026-04-20"},
        LegacyStateKind.USED_ARTICLES: {
            "articles": [
                {"date": "2026-04-20", "url": "https://example.com/a"},
            ]
        },
        LegacyStateKind.SOURCE_ROTATION: {
            "sources": {
                "OpenAI News": {
                    "category": "news",
                    "last_used": "2026-04-20",
                }
            },
            "weekly_summary": {
                "week_start": "2026-04-20",
                "3blue1brown_used_this_week": False,
            },
        },
        LegacyStateKind.SOURCE_HEALTH: {"runs": []},
    }
    paths: dict[LegacyStateKind, Path] = {}
    original: dict[LegacyStateKind, bytes] = {}
    for index, (kind, payload) in enumerate(fixtures.items()):
        path = root / f"{kind.value}.json"
        original[kind] = _write_new_json(path, payload, bom=index == 0)
        paths[kind] = path
    store, importer = _importer(root, paths)

    records = importer.import_all()

    assert set(records) == set(LegacyStateKind)
    assert len(tuple(store.events_root.glob("legacy-state-*.json"))) == 4
    for kind, path in paths.items():
        assert path.read_bytes() == original[kind]
        assert records[kind]["source_sha256"] == hashlib.sha256(original[kind]).hexdigest()
        assert records[kind]["legacy_payload"] == fixtures[kind]


def test_bom_is_accepted_and_repeated_import_is_idempotent() -> None:
    root = _case_root("legacy-bom")
    path = root / "used_articles.json"
    raw = _write_new_json(
        path,
        {
            "articles": [
                {"date": "2026-04-20", "url": "https://example.com/a"},
            ]
        },
        bom=True,
    )
    store, importer = _importer(
        root,
        {LegacyStateKind.USED_ARTICLES: path},
    )

    first = importer.import_one(LegacyStateKind.USED_ARTICLES)
    second = importer.import_one(LegacyStateKind.USED_ARTICLES)

    assert first == second
    assert path.read_bytes() == raw
    assert len(tuple(store.events_root.glob("legacy-state-*.json"))) == 1


def test_corrupt_json_fails_closed_and_creates_no_import_event() -> None:
    root = _case_root("legacy-corrupt")
    path = root / "source_rotation.json"
    with path.open("xb") as stream:
        stream.write(b'{"weekly_summary":{"week_start":"bad-date"}}')
        stream.flush()
        os.fsync(stream.fileno())
    before = path.read_bytes()
    store, importer = _importer(
        root,
        {LegacyStateKind.SOURCE_ROTATION: path},
    )

    with pytest.raises(LegacyStateImportError, match="invalid legacy state"):
        importer.import_one(LegacyStateKind.SOURCE_ROTATION)

    assert path.read_bytes() == before
    assert tuple(store.events_root.glob("legacy-state-*.json")) == ()


def test_duplicate_and_7_30_day_records_are_preserved_in_order() -> None:
    root = _case_root("legacy-retention")
    path = root / "used_articles.json"
    duplicate = {"date": "2026-04-20", "url": "https://example.com/a"}
    payload = {
        "articles": [
            {"date": "2025-01-01", "url": "https://example.com/old"},
            duplicate,
            duplicate,
            {"date": "2026-04-01", "url": "https://example.com/30-day"},
        ]
    }
    original = _write_new_json(path, payload)
    _, importer = _importer(
        root,
        {LegacyStateKind.USED_ARTICLES: path},
    )

    record = importer.import_one(
        LegacyStateKind.USED_ARTICLES,
        legacy_retention_days=(7, 30),
    )

    assert record["legacy_payload"]["articles"] == payload["articles"]
    assert record["record_count"] == 4
    assert record["retention_resolution"] == {
        "legacy_values_days": [7, 30],
        "effective_policy": "retain_all_no_delete",
    }
    assert path.read_bytes() == original


def test_legacy_loaders_raise_on_corruption_instead_of_returning_empty() -> None:
    root = _case_root("legacy-loader-corrupt")
    path = root / "source_rotation.json"
    original = _write_new_json(
        path,
        {"weekly_summary": {"week_start": "bad-date"}},
    )

    with pytest.raises(LegacyStateImportError, match="invalid legacy state"):
        state_schema.load_source_rotation_state(path)

    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("write_json_file", ({"articles": []},)),
        ("save_used_articles_state", (state_schema.UsedArticlesState(),)),
        ("compact_used_articles_state", (7,)),
        ("save_source_rotation_state", (state_schema.SourceRotationState(),)),
        ("refresh_source_rotation_week", ("2026-04-21",)),
        (
            "append_source_health_run",
            (),
        ),
    ],
)
def test_legacy_mutation_apis_are_disabled(
    operation: str,
    arguments: tuple[object, ...],
) -> None:
    root = _case_root(f"legacy-disabled-{operation}")
    path = root / "legacy.json"
    original = _write_new_json(path, {"runs": [], "articles": []})
    function = getattr(state_schema, operation)

    with pytest.raises(LegacyStateImportError, match="read-only"):
        if operation == "append_source_health_run":
            function(
                path,
                run_date="2026-04-20",
                run_at="2026-04-20T07:00:00",
                entries=[],
                keep_runs=1,
            )
        else:
            function(path, *arguments)

    assert path.read_bytes() == original
