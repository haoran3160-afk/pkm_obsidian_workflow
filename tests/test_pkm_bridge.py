import json
from pathlib import Path

import pytest

import pkm_bridge


def test_load_config_uses_schema_validation(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "pkm_config.json"
    config_path.write_text(
        json.dumps(
            {
                "vault_path": str(tmp_path / "vault"),
                "write_mode": "disk",
                "rss_feeds": [
                    {
                        "name": "OpenAI News",
                        "url": "https://example.com/feed.xml",
                        "note_folder": "30-Daily/AI-News",
                    }
                ],
                "youtube_channels": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pkm_bridge, "CONFIG_PATH", config_path)

    config = pkm_bridge.load_config()

    assert config.write_mode == "disk"
    assert config.rss_feeds[0].name == "OpenAI News"


def test_load_config_reports_validation_error(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "pkm_config.json"
    config_path.write_text(json.dumps({"rss_feeds": [], "youtube_channels": []}), encoding="utf-8")
    monkeypatch.setattr(pkm_bridge, "CONFIG_PATH", config_path)

    with pytest.raises(ValueError):
        pkm_bridge.load_config()


def test_resolve_runtime_config_uses_plugin_data_fallback(monkeypatch, tmp_path: Path):
    vault = tmp_path / "vault"
    plugin_dir = vault / ".obsidian" / "plugins" / "obsidian-local-rest-api"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "data.json").write_text(
        json.dumps(
            {
                "apiKey": "plugin-key",
                "enableInsecureServer": True,
                "insecurePort": 31313,
            }
        ),
        encoding="utf-8",
    )

    config = pkm_bridge.load_config()
    config.vault_path = str(vault)
    config.obsidian_api.base_url = "http://localhost:27123"
    config.obsidian_api.api_key = ""

    monkeypatch.delenv("OBSIDIAN_API_KEY", raising=False)
    monkeypatch.delenv("OBSIDIAN_API_BASE", raising=False)
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)

    vault_path, api_base, api_key = pkm_bridge.resolve_runtime_config(config)

    assert vault_path == str(vault)
    assert api_base == "http://localhost:31313"
    assert api_key == "plugin-key"
