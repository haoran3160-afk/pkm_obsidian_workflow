import json
from pathlib import Path

from fastapi.testclient import TestClient

import ui_server


def test_get_config_output_combines_config_and_env(monkeypatch):
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

    assert response.status_code == 200
    assert response.json()["vault_path"] == "D:/vault"
    assert response.json()["enable_llm_copy"] is True


def test_put_config_sources_writes_validated_config(monkeypatch, tmp_path: Path):
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

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["rss_feeds"][0]["name"] == "OpenAI News"


def test_post_doctor_returns_subprocess_result(monkeypatch):
    client = TestClient(ui_server.app)
    monkeypatch.setattr(
        ui_server,
        "_run_subprocess",
        lambda command: {"return_code": 0, "stdout": "ok", "stderr": "", "command": command},
    )

    response = client.post("/api/doctor", json={"skip_network": True})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "ok" == response.json()["stdout"]


def test_post_run_dispatches_background_worker(monkeypatch):
    client = TestClient(ui_server.app)
    called: list[str] = []
    monkeypatch.setattr(ui_server, "_start_background_run", lambda mode: called.append(mode))

    response = client.post("/api/run", json={"mode": "dry-run"})

    assert response.status_code == 200
    assert called == ["dry-run"]


def test_get_logs_history_reads_fetch_log(monkeypatch, tmp_path: Path):
    client = TestClient(ui_server.app)
    log_path = tmp_path / "fetch.log"
    log_path.write_text("line-1\nline-2\n", encoding="utf-8")
    monkeypatch.setattr(ui_server, "LOG_PATH", log_path)

    response = client.get("/api/logs/history")

    assert response.status_code == 200
    assert response.json()["history"][-1]["message"] == "line-2"


def test_settings_env_round_trip(monkeypatch, tmp_path: Path):
    client = TestClient(ui_server.app)
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=old-key\n", encoding="utf-8")
    monkeypatch.setattr(ui_server, "ENV_PATH", env_path)

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

    assert response.status_code == 200
    saved_text = env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=new-key" in saved_text
    assert response.json()["curation_reasoning_effort"] == "high"


def test_settings_system_reports_paths(monkeypatch):
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
            vault_path="D:/vault",
        ),
    )
    monkeypatch.setattr(ui_server, "_load_env_values", lambda: {"OBSIDIAN_VAULT_PATH": "D:/vault"})
    monkeypatch.setattr(
        ui_server,
        "_validate_vault_path",
        lambda vault_path: {"exists": True, "is_dir": True, "writable": True},
    )

    response = client.get("/api/settings/system")

    assert response.status_code == 200
    assert response.json()["vault"]["writable"] is True
    assert response.json()["config"]["path"].endswith("pkm_config.json")


def test_validate_vault_reports_filesystem_state(tmp_path: Path):
    client = TestClient(ui_server.app)
    vault = tmp_path / "vault"
    vault.mkdir()

    response = client.post("/api/validate/vault", json={"vault_path": str(vault)})

    assert response.status_code == 200
    assert response.json()["exists"] is True
    assert response.json()["is_dir"] is True
