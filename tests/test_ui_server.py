import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ui_server


def test_get_config_output_never_echoes_legacy_env_or_credentials(monkeypatch):
    client = TestClient(ui_server.app)
    monkeypatch.setattr(
        ui_server,
        "_load_config",
        lambda: ui_server.PKMConfig(
            rss_feeds=[
                ui_server.RssFeed(
                    name="OpenAI News",
                    url="https://example.com/feed.xml",
                    note_folder="30-Daily/AI-News",
                )
            ],
            youtube_channels=[],
        ),
    )
    monkeypatch.setattr(
        ui_server,
        "_load_env_values",
        lambda: {
            "OBSIDIAN_VAULT_PATH": "D:/vault",
            "PKM_ENABLE_LLM_DIGEST_COPY": "1",
            "PKM_CURATION_MODEL": "gpt-5.4-mini",
        },
    )

    response = client.get("/api/config/output")

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]


def test_put_config_sources_is_blocked_without_changing_config(monkeypatch, tmp_path: Path):
    client = TestClient(ui_server.app)
    config_path = tmp_path / "pkm_config.json"
    config_path.write_text(
        json.dumps(
            {
                "vault_path": "D:/vault",
                "write_mode": "disk",
                "rss_feeds": [
                    {
                        "name": "Old Feed",
                        "url": "https://example.com/old.xml",
                        "note_folder": "30-Daily/AI-News",
                    }
                ],
                "youtube_channels": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ui_server, "CONFIG_PATH", config_path)

    before = config_path.read_bytes()
    response = client.put(
        "/api/config/sources",
        json={
            "rss_feeds": [
                {
                    "name": "OpenAI News",
                    "url": "https://example.com/feed.xml",
                    "note_folder": "30-Daily/AI-News",
                    "enabled": True,
                }
            ],
            "youtube_channels": [],
        },
    )

    assert response.status_code == 503
    assert config_path.read_bytes() == before


def test_post_doctor_is_blocked_before_subprocess_start(monkeypatch):
    client = TestClient(ui_server.app)
    called: list[list[str]] = []
    monkeypatch.setattr(
        ui_server,
        "_run_subprocess",
        lambda command: called.append(command),
    )

    response = client.post("/api/doctor", json={"skip_network": True})

    assert response.status_code == 503
    assert called == []


def test_post_run_is_fail_closed_and_never_dispatches_legacy_worker(monkeypatch):
    client = TestClient(ui_server.app)
    called: list[str] = []
    monkeypatch.setattr(ui_server, "_start_background_run", lambda mode: called.append(mode))

    response = client.post("/api/run", json={"mode": "dry-run"})

    assert response.status_code == 503
    assert "v7.3" in response.json()["detail"]
    assert called == []


def test_get_logs_history_is_blocked_without_reading_fetch_log(monkeypatch, tmp_path: Path):
    client = TestClient(ui_server.app)
    log_path = tmp_path / "fetch.log"
    log_path.write_text("line-1\nline-2\n", encoding="utf-8")
    monkeypatch.setattr(ui_server, "LOG_PATH", log_path)

    response = client.get("/api/logs/history")

    assert response.status_code == 503
    assert "line-2" not in response.text


def test_settings_env_read_and_write_are_blocked_without_secret_echo_or_mutation(
    monkeypatch,
    tmp_path: Path,
):
    client = TestClient(ui_server.app)
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=old-key\n", encoding="utf-8")
    monkeypatch.setattr(ui_server, "ENV_PATH", env_path)

    before = env_path.read_bytes()
    response = client.put(
        "/api/settings/env",
        json={
            "obsidian_api_base": "http://127.0.0.1:27123",
            "obsidian_api_key": "obsidian-secret",
            "openai_api_key": "new-key",
            "openai_base_url": "https://api.openai.com/v1",
            "curation_model": "gpt-5.4-mini",
            "curation_reasoning_effort": "high",
        },
    )

    assert response.status_code == 503
    assert env_path.read_bytes() == before
    read_response = client.get("/api/settings/env")
    assert read_response.status_code == 503
    assert "old-key" not in read_response.text


def test_settings_system_is_blocked_before_loading_paths(monkeypatch):
    client = TestClient(ui_server.app)
    called: list[str] = []
    monkeypatch.setattr(ui_server, "_load_config", lambda: called.append("config"))
    monkeypatch.setattr(ui_server, "_load_env_values", lambda: called.append("env"))

    response = client.get("/api/settings/system")

    assert response.status_code == 503
    assert called == []


def test_validate_vault_is_blocked_before_arbitrary_path_probe(monkeypatch):
    client = TestClient(ui_server.app)
    called: list[str] = []
    monkeypatch.setattr(
        ui_server,
        "_validate_vault_path",
        lambda vault_path: called.append(vault_path),
    )

    response = client.post(
        "/api/validate/vault",
        json={"vault_path": r"\\attacker.invalid\share"},
    )

    assert response.status_code == 503
    assert called == []


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/", "GET"),
        ("/docs", "GET"),
        ("/openapi.json", "GET"),
        ("/api/status", "GET"),
        ("/api/config/sources", "GET"),
        ("/api/logs/stream", "GET"),
    ],
)
def test_importable_legacy_asgi_app_is_globally_sealed(path: str, method: str) -> None:
    response = TestClient(ui_server.app).request(method, path)

    assert response.status_code == 503
    assert "v7.3" in response.json()["detail"]
