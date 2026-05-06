#!/usr/bin/env python3
"""
ui_server.py - Local FastAPI control plane for the PKM workflow.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal

import uvicorn
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

import state_schema
from config_schema import PKMConfig, RssFeed, YouTubeChannel, load_and_validate

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "pkm_config.json"
ENV_PATH = SCRIPT_DIR / ".env"
LOG_PATH = SCRIPT_DIR / "fetch.log"

PYTHON_EXE = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(os.environ.get("PYTHON", "")) if os.environ.get("PYTHON") else Path("python")


class RunRequest(BaseModel):
    mode: Literal["digest", "raw", "dry-run", "test"]


class DoctorRequest(BaseModel):
    skip_network: bool = False


class VaultValidationRequest(BaseModel):
    vault_path: str = Field(min_length=1)


class SourcesConfigPayload(BaseModel):
    rss_feeds: list[RssFeed]
    youtube_channels: list[YouTubeChannel]


class OutputConfigPayload(BaseModel):
    write_mode: Literal["disk", "api", "both"]
    vault_path: str = Field(min_length=1)
    obsidian_api_base: str = Field(min_length=1)
    obsidian_api_key: str = ""
    max_papers_per_day: int = Field(ge=1, le=100)
    max_videos_per_channel: int = Field(ge=1, le=20)
    max_paper_notes_per_day: int = Field(ge=0, le=50)
    max_video_notes_per_day: int = Field(ge=0, le=30)
    daily_digest_top_picks: int = Field(ge=1, le=30)
    daily_digest_max_items_per_source: int = Field(ge=1, le=20)
    daily_digest_action_items: int = Field(ge=1, le=10)
    daily_digest_max_deferred_items: int = Field(ge=1, le=30)
    daily_digest_only_output: bool = True
    enable_llm_copy: bool = False
    openai_api_key: str = ""
    openai_base_url: str = ""
    curation_model: str = "gpt-5.4-mini"
    curation_reasoning_effort: str = "medium"


class RunState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = False
        self.mode = ""
        self.started_at = ""
        self.finished_at = ""
        self.return_code: int | None = None
        self.last_error = ""
        self.events: list[dict[str, Any]] = []
        self.sequence = 0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "active": self.active,
                "mode": self.mode,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "return_code": self.return_code,
                "last_error": self.last_error,
                "event_count": len(self.events),
            }

    def start(self, mode: str) -> None:
        with self.lock:
            self.active = True
            self.mode = mode
            self.started_at = _now_iso()
            self.finished_at = ""
            self.return_code = None
            self.last_error = ""
            self._append_locked("run.started", f"Started {mode} workflow.")

    def append(self, kind: str, message: str) -> None:
        with self.lock:
            self._append_locked(kind, message)

    def finish(self, return_code: int, error: str = "") -> None:
        with self.lock:
            self.active = False
            self.finished_at = _now_iso()
            self.return_code = return_code
            self.last_error = error
            final_kind = "run.failed" if return_code else "run.completed"
            final_message = error or "Run completed successfully."
            self._append_locked(final_kind, final_message)

    def _append_locked(self, kind: str, message: str) -> None:
        self.sequence += 1
        self.events.append(
            {
                "id": self.sequence,
                "ts": _now_iso(),
                "kind": kind,
                "message": message.rstrip(),
            }
        )
        if len(self.events) > 500:
            self.events = self.events[-500:]


RUN_STATE = RunState()

app = FastAPI(title="Obsidian PKM Workflow UI", version="0.1.0")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_config() -> PKMConfig:
    return load_and_validate(CONFIG_PATH)


def _load_env_values() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    return {key: value or "" for key, value in dotenv_values(ENV_PATH).items()}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _write_env_atomic(values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    with NamedTemporaryFile("w", delete=False, dir=ENV_PATH.parent, encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
        temp_path = Path(handle.name)
    temp_path.replace(ENV_PATH)


def _build_output_payload(config: PKMConfig, env_values: dict[str, str]) -> dict[str, Any]:
    return {
        "write_mode": config.write_mode,
        "vault_path": env_values.get("OBSIDIAN_VAULT_PATH") or config.vault_path or "",
        "obsidian_api_base": env_values.get("OBSIDIAN_API_BASE") or config.obsidian_api.base_url,
        "obsidian_api_key": env_values.get("OBSIDIAN_API_KEY") or config.obsidian_api.api_key,
        "max_papers_per_day": config.max_papers_per_day,
        "max_videos_per_channel": config.max_videos_per_channel,
        "max_paper_notes_per_day": config.max_paper_notes_per_day,
        "max_video_notes_per_day": config.max_video_notes_per_day,
        "daily_digest_top_picks": config.daily_digest_top_picks,
        "daily_digest_max_items_per_source": config.daily_digest_max_items_per_source,
        "daily_digest_action_items": config.daily_digest_action_items,
        "daily_digest_max_deferred_items": config.daily_digest_max_deferred_items,
        "daily_digest_only_output": config.daily_digest_only_output,
        "enable_llm_copy": _env_truthy(env_values.get("PKM_ENABLE_LLM_DIGEST_COPY", "0")),
        "openai_api_key": env_values.get("OPENAI_API_KEY", ""),
        "openai_base_url": env_values.get("OPENAI_BASE_URL", ""),
        "curation_model": env_values.get("PKM_CURATION_MODEL", "gpt-5.4-mini"),
        "curation_reasoning_effort": env_values.get("PKM_CURATION_REASONING_EFFORT", "medium"),
    }


def _env_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _save_sources(payload: SourcesConfigPayload) -> PKMConfig:
    current = _load_config()
    raw = current.model_dump()
    raw["rss_feeds"] = [feed.model_dump() for feed in payload.rss_feeds]
    raw["youtube_channels"] = [channel.model_dump() for channel in payload.youtube_channels]
    validated = PKMConfig.model_validate(raw)
    _write_json_atomic(CONFIG_PATH, validated.model_dump())
    return validated


def _save_output(payload: OutputConfigPayload) -> dict[str, Any]:
    current = _load_config()
    raw = current.model_dump()
    raw.update(
        {
            "write_mode": payload.write_mode,
            "vault_path": payload.vault_path,
            "max_papers_per_day": payload.max_papers_per_day,
            "max_videos_per_channel": payload.max_videos_per_channel,
            "max_paper_notes_per_day": payload.max_paper_notes_per_day,
            "max_video_notes_per_day": payload.max_video_notes_per_day,
            "daily_digest_top_picks": payload.daily_digest_top_picks,
            "daily_digest_max_items_per_source": payload.daily_digest_max_items_per_source,
            "daily_digest_action_items": payload.daily_digest_action_items,
            "daily_digest_max_deferred_items": payload.daily_digest_max_deferred_items,
            "daily_digest_only_output": payload.daily_digest_only_output,
            "obsidian_api": {
                "base_url": payload.obsidian_api_base,
                "api_key": payload.obsidian_api_key,
            },
        }
    )
    validated = PKMConfig.model_validate(raw)
    _write_json_atomic(CONFIG_PATH, validated.model_dump())

    env_values = _load_env_values()
    env_values.update(
        {
            "OBSIDIAN_VAULT_PATH": payload.vault_path,
            "OBSIDIAN_API_BASE": payload.obsidian_api_base,
            "OBSIDIAN_API_KEY": payload.obsidian_api_key,
            "PKM_ENABLE_LLM_DIGEST_COPY": "1" if payload.enable_llm_copy else "0",
            "OPENAI_API_KEY": payload.openai_api_key,
            "OPENAI_BASE_URL": payload.openai_base_url,
            "PKM_CURATION_MODEL": payload.curation_model,
            "PKM_CURATION_REASONING_EFFORT": payload.curation_reasoning_effort,
        }
    )
    _write_env_atomic(env_values)
    return _build_output_payload(validated, env_values)


def _validate_vault_path(vault_path: str) -> dict[str, Any]:
    vault = Path(vault_path)
    return {
        "exists": vault.exists(),
        "is_dir": vault.is_dir(),
        "writable": os.access(vault, os.W_OK) if vault.exists() else False,
    }


def _recent_outputs(vault_path: str, *, limit: int = 8) -> list[dict[str, Any]]:
    vault = Path(vault_path)
    targets = [
        vault / "30-Daily" / "AI-News",
        vault / "00-Inbox" / "Raw-Feeds",
        vault / "40-MOC",
    ]
    files: list[Path] = []
    for target in targets:
        if target.exists():
            files.extend([item for item in target.glob("*.md") if item.is_file()])
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return [
        {
            "name": path.name,
            "path": str(path),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        for path in files[:limit]
    ]


def _source_health_summary() -> dict[str, Any]:
    state = state_schema.load_source_health_state(SCRIPT_DIR / "source_health.json")
    if not state.runs:
        return {"run_date": "", "sources": [], "counts": {"ok": 0, "warning": 0, "error": 0}}
    latest = state.runs[-1]
    counts = {"ok": 0, "warning": 0, "error": 0}
    for entry in latest.entries:
        key = entry.status if entry.status in counts else "warning"
        counts[key] += 1
    return {
        "run_date": latest.run_date,
        "sources": [entry.model_dump() for entry in latest.entries[:20]],
        "counts": counts,
    }


def _log_history(limit: int = 200) -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    lines = deque(LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines(), maxlen=limit)
    return [{"kind": "log", "message": line} for line in lines]


def _command_for_mode(mode: str) -> list[str]:
    command = [str(PYTHON_EXE), "main.py"]
    if mode == "raw":
        command.append("--raw-only")
    elif mode == "dry-run":
        command.append("--dry-run")
    elif mode == "test":
        command.append("--test")
    return command


def _start_background_run(mode: str) -> None:
    with RUN_STATE.lock:
        if RUN_STATE.active:
            raise HTTPException(status_code=409, detail="Another workflow run is already active.")
    RUN_STATE.start(mode)

    def worker() -> None:
        env = os.environ.copy()
        process = subprocess.Popen(
            _command_for_mode(mode),
            cwd=SCRIPT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            RUN_STATE.append("run.log", line.rstrip())
        return_code = process.wait()
        RUN_STATE.finish(return_code, "" if return_code == 0 else f"{mode} exited with {return_code}.")

    threading.Thread(target=worker, name=f"pkm-ui-{mode}", daemon=True).start()


def _run_subprocess(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    config = _load_config()
    env_values = _load_env_values()
    vault_path = env_values.get("OBSIDIAN_VAULT_PATH") or config.vault_path or ""
    return {
        "run": RUN_STATE.snapshot(),
        "config_summary": {
            "rss_count": len(config.rss_feeds),
            "youtube_count": len(config.youtube_channels),
            "write_mode": config.write_mode,
            "vault_path": vault_path,
        },
        "recent_outputs": _recent_outputs(vault_path) if vault_path else [],
        "feed_health": _source_health_summary(),
    }


@app.post("/api/run")
def post_run(payload: RunRequest) -> dict[str, Any]:
    _start_background_run(payload.mode)
    return {"ok": True, "run": RUN_STATE.snapshot()}


@app.post("/api/doctor")
def post_doctor(payload: DoctorRequest) -> dict[str, Any]:
    command = [str(PYTHON_EXE), "main.py", "--doctor"]
    if payload.skip_network:
        command.append("--doctor-skip-network")
    result = _run_subprocess(command)
    result["ok"] = result["return_code"] == 0
    return result


@app.get("/api/logs/history")
def get_logs_history(limit: int = 200) -> dict[str, Any]:
    return {
        "events": RUN_STATE.snapshot(),
        "history": _log_history(limit),
        "stream": list(RUN_STATE.events[-limit:]),
    }


@app.get("/api/logs/stream")
def get_logs_stream() -> StreamingResponse:
    def event_stream() -> Any:
        last_id = 0
        while True:
            events = [event for event in RUN_STATE.events if event["id"] > last_id]
            for event in events:
                last_id = event["id"]
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/config/sources")
def get_config_sources() -> dict[str, Any]:
    config = _load_config()
    return {
        "rss_feeds": [feed.model_dump() for feed in config.rss_feeds],
        "youtube_channels": [channel.model_dump() for channel in config.youtube_channels],
    }


@app.put("/api/config/sources")
def put_config_sources(payload: SourcesConfigPayload) -> dict[str, Any]:
    try:
        config = _save_sources(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return {
        "ok": True,
        "rss_feeds": [feed.model_dump() for feed in config.rss_feeds],
        "youtube_channels": [channel.model_dump() for channel in config.youtube_channels],
    }


@app.get("/api/config/output")
def get_config_output() -> dict[str, Any]:
    return _build_output_payload(_load_config(), _load_env_values())


@app.put("/api/config/output")
def put_config_output(payload: OutputConfigPayload) -> dict[str, Any]:
    try:
        return {"ok": True, **_save_output(payload)}
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@app.post("/api/validate/vault")
def post_validate_vault(payload: VaultValidationRequest) -> dict[str, Any]:
    return _validate_vault_path(payload.vault_path)


if __name__ == "__main__":
    uvicorn.run("ui_server:app", host="127.0.0.1", port=int(os.getenv("PKM_UI_PORT", "8000")))
