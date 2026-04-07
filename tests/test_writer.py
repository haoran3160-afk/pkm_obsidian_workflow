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


def test_write_and_append_to_obsidian_disk(tmp_path):
    vault = tmp_path / "vault"
    filepath = "10-Notes/demo.md"

    assert writer.write_to_obsidian_disk(str(vault), filepath, "hello")
    assert writer.append_to_obsidian_disk(str(vault), filepath, "\nworld")
    assert (vault / filepath).read_text(encoding="utf-8") == "hello\nworld"


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


def test_write_via_api_skips_existing_note(monkeypatch):
    monkeypatch.setattr(writer, "note_exists_api", lambda *args, **kwargs: True)
    called = {"put": False}

    def _fake_put(*args, **kwargs):
        called["put"] = True
        return DummyResponse()

    monkeypatch.setattr(writer, "_api_put", _fake_put)

    ok = writer.write_via_api("http://localhost:27123", "note.md", "content", {}, overwrite=False)

    assert ok is False
    assert called["put"] is False


def test_write_via_api_uses_encoded_vault_endpoint(monkeypatch):
    monkeypatch.setattr(writer, "note_exists_api", lambda *args, **kwargs: False)
    capture = {}

    def _fake_put(api_base, endpoint, headers, data):
        capture["api_base"] = api_base
        capture["endpoint"] = endpoint
        capture["payload"] = data
        return DummyResponse(status_code=200)

    monkeypatch.setattr(writer, "_api_put", _fake_put)

    ok = writer.write_via_api(
        "http://localhost:27123",
        "10-Notes/中文 标题.md",
        "hello",
        {"Authorization": "Bearer test"},
    )

    assert ok is True
    assert capture["api_base"] == "http://localhost:27123"
    assert capture["endpoint"].startswith("/vault/10-Notes/")
    assert "%E4%B8%AD%E6%96%87%20%E6%A0%87%E9%A2%98.md" in capture["endpoint"]
    assert capture["payload"] == b"hello"
