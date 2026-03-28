#!/usr/bin/env python3
"""
writer.py — Submits formatted Markdown strings to Obsidian.
Can use disk-write (for local usage) or REST API (Obsidian Local REST API).
Includes retry logic for API calls.
"""

import logging
from pathlib import Path
from urllib.parse import quote

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

log = logging.getLogger("pkm.writer")
SESSION = requests.Session()


class ObsidianAPIError(Exception):
    """Custom exception raised when Obsidian REST API calls fail."""
    pass


# ── Local File System ─────────────────────────────────────────────────────────

def write_to_obsidian_disk(vault_path: str, filepath: str, content: str) -> bool:
    """Writes directly to the local Obsidian Vault via the filesystem."""
    full_path = Path(vault_path) / Path(filepath)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full_path.write_text(content, encoding="utf-8")
        log.info(f"[write] {filepath}")
        return True
    except Exception as e:
        log.error(f"[write-fail] {filepath} — {e}")
        return False


def append_to_obsidian_disk(vault_path: str, filepath: str, content: str) -> bool:
    """Appends to a file in the local Obsidian Vault via the filesystem."""
    full_path = Path(vault_path) / Path(filepath)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with full_path.open("a", encoding="utf-8") as f:
            f.write(content)
        log.info(f"[append] {filepath}")
        return True
    except Exception as e:
        log.error(f"[append-fail] {filepath} — {e}")
        return False


# ── Obsidian REST API ─────────────────────────────────────────────────────────

def _build_api_url(api_base: str, endpoint: str) -> str:
    base = api_base.rstrip("/")
    normalized_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    return f"{base}{normalized_endpoint}"


def _vault_endpoint(filepath: str) -> str:
    normalized_path = filepath.replace("\\", "/")
    encoded_path = quote(normalized_path, safe="/-_.~")
    return f"/vault/{encoded_path}"


@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_get(api_base: str, endpoint: str, headers: dict) -> requests.Response:
    """Make an API GET request with retry logic."""
    r = SESSION.get(_build_api_url(api_base, endpoint), headers=headers, timeout=5)
    r.raise_for_status()
    return r


@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_put(api_base: str, endpoint: str, headers: dict, data: bytes) -> requests.Response:
    """Make an API PUT request with retry logic."""
    r = SESSION.put(_build_api_url(api_base, endpoint), headers=headers, data=data, timeout=10)
    if r.status_code not in (200, 204):
        raise ObsidianAPIError(f"HTTP {r.status_code}: {r.text}")
    return r


@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_post(api_base: str, endpoint: str, headers: dict, data: bytes) -> requests.Response:
    """Make an API POST (append) request with retry logic."""
    r = SESSION.post(_build_api_url(api_base, endpoint), headers=headers, data=data, timeout=10)
    if r.status_code not in (200, 204):
        raise ObsidianAPIError(f"HTTP {r.status_code}: {r.text}")
    return r


def check_api_connection(api_base: str, headers: dict) -> bool:
    """Check if the Obsidian Local REST API is reachable."""
    try:
        r = _api_get(api_base, "/", headers)
        return r.status_code == 200
    except Exception as e:
        log.debug(f"API Connection Check Failed: {e}")
        return False


def note_exists_api(api_base: str, filepath: str, headers: dict) -> bool:
    """Check if a note exists via API."""
    try:
        endpoint = _vault_endpoint(filepath)
        r = SESSION.get(_build_api_url(api_base, endpoint), headers=headers, timeout=5)
        if r.status_code in (401, 403, 500):
            raise ObsidianAPIError(f"HTTP {r.status_code}: {r.text}")
        if r.status_code == 404:
            return False
        return r.status_code == 200
    except requests.exceptions.RequestException as exc:
        raise ObsidianAPIError(f"Cannot connect to Obsidian Local REST API: {exc}") from exc


def write_via_api(api_base: str, filepath: str, content: str, headers: dict, overwrite: bool = False) -> bool:
    """Write note via API."""
    if note_exists_api(api_base, filepath, headers) and not overwrite:
        log.info(f"[skip] Note exists: {filepath}")
        return False

    try:
        _api_put(api_base, _vault_endpoint(filepath), headers, content.encode("utf-8"))
        log.info(f"[✓] API write ok: {filepath}")
        return True
    except Exception as e:
        log.error(f"[✗] API write fail: {filepath} ({e})")
        return False


def append_via_api(api_base: str, filepath: str, content: str, headers: dict) -> bool:
    """Append to note via API."""
    try:
        _api_post(api_base, _vault_endpoint(filepath), headers, content.encode("utf-8"))
        log.info(f"[✓] API append ok: {filepath}")
        return True
    except Exception as e:
        log.error(f"[✗] API append fail: {filepath} ({e})")
        return False
