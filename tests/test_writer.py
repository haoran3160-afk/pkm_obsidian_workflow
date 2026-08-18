import os
import uuid
from pathlib import Path

import pytest
import requests

import writer


class DummyResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def test_legacy_disk_writer_and_append_are_both_disabled():
    root = _case_root("writer-create-new")
    vault = root / "vault"
    parent = vault / "30-Daily" / "AI-News"
    parent.mkdir(parents=True)
    filepath = "30-Daily/AI-News/AI-Daily-2026-07-26.md"

    assert writer.write_to_obsidian_disk(str(vault), filepath, "hello") is False
    assert writer.write_to_obsidian_disk(str(vault), filepath, "replacement") is False
    assert writer.append_to_obsidian_disk(str(vault), filepath, "\nworld") is False
    assert not (vault / filepath).exists()


def test_disk_writer_rejects_non_workflow_vault_path():
    root = _case_root("writer-scope")
    vault = root / "vault"
    (vault / "20-Knowledge").mkdir(parents=True)

    assert (
        writer.write_to_obsidian_disk(
            str(vault),
            "20-Knowledge/should-not-write.md",
            "blocked",
        )
        is False
    )
    assert not (vault / "20-Knowledge" / "should-not-write.md").exists()


def test_vault_endpoint_encodes_unicode_and_spaces():
    endpoint = writer._vault_endpoint("10-Notes/中文 标题.md")
    assert endpoint.startswith("/vault/10-Notes/")
    assert "%E4%B8%AD%E6%96%87%20%E6%A0%87%E9%A2%98.md" in endpoint


def test_note_exists_api_returns_false_on_404(monkeypatch):
    monkeypatch.setattr(
        writer.SESSION, "get", lambda *args, **kwargs: DummyResponse(status_code=404)
    )
    assert writer.note_exists_api("http://localhost:27123", "test.md", {}) is False


def test_note_exists_api_raises_custom_error_on_request_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(writer.SESSION, "get", _raise)

    with pytest.raises(writer.ObsidianAPIError):
        writer.note_exists_api("http://localhost:27123", "test.md", {})


def test_note_exists_api_raises_custom_error_on_server_error(monkeypatch):
    monkeypatch.setattr(
        writer.SESSION,
        "get",
        lambda *args, **kwargs: DummyResponse(status_code=500, text="internal error"),
    )

    with pytest.raises(writer.ObsidianAPIError):
        writer.note_exists_api("http://localhost:27123", "test.md", {})


def test_write_via_api_is_fail_closed_even_when_target_is_new(monkeypatch):
    called = {"put": False}

    def _fake_put(*args, **kwargs):
        called["put"] = True
        return DummyResponse()

    monkeypatch.setattr(writer, "_api_put", _fake_put)

    ok = writer.write_via_api(
        "http://localhost:27123",
        "30-Daily/AI-News/new.md",
        "content",
        {},
        overwrite=False,
    )

    assert ok is False
    assert called["put"] is False


def test_write_via_api_rejects_overwrite_flag(monkeypatch):
    monkeypatch.setattr(
        writer,
        "_api_put",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API PUT must not run")),
    )

    assert (
        writer.write_via_api(
            "http://localhost:27123",
            "30-Daily/AI-News/existing.md",
            "replacement",
            {},
            overwrite=True,
        )
        is False
    )


def test_append_via_api_is_fail_closed_without_network(monkeypatch):
    called = {"post": False}

    def _fake_post(*_args, **_kwargs):
        called["post"] = True
        return DummyResponse()

    monkeypatch.setattr(writer, "_api_post", _fake_post)

    assert (
        writer.append_via_api(
            "http://localhost:27123",
            "30-Daily/AI-News/existing.md",
            "append",
            {},
        )
        is False
    )
    assert called["post"] is False
