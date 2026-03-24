#!/usr/bin/env python3
"""
writer.py — Submits formatted Markdown strings to Obsidian.
Can use disk-write (for local usage) or REST API (Obsidian Local REST API).
Includes retry logic for API calls.
"""

import logging
import os
from pathlib import Path

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

log = logging.getLogger("pkm.writer")


class ObsidianAPIError(Exception):
    """Custom exception raised when Obsidian REST API calls fail."""
    pass


# ── Local File System ─────────────────────────────────────────────────────────

def write_to_obsidian_disk(vault_path: str, filepath: str, content: str) -> bool:
    """Writes directly to the local Obsidian Vault via the filesystem."""
    full_path = os.path.join(vault_path, filepath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info(f"[write] {filepath}")
        return True
    except Exception as e:
        log.error(f"[write-fail] {filepath} — {e}")
        return False


def append_to_obsidian_disk(vault_path: str, filepath: str, content: str) -> bool:
    """Appends to a file in the local Obsidian Vault via the filesystem."""
    full_path = os.path.join(vault_path, filepath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    try:
        with open(full_path, "a", encoding="utf-8") as f:
            f.write(content)
        log.info(f"[append] {filepath}")
        return True
    except Exception as e:
        log.error(f"[append-fail] {filepath} — {e}")
        return False


# ── Obsidian REST API ─────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_get(api_base: str, endpoint: str, headers: dict) -> requests.Response:
    """Make an API GET request with retry logic."""
    r = requests.get(f"{api_base}{endpoint}", headers=headers, timeout=5)
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
    r = requests.put(f"{api_base}{endpoint}", headers=headers, data=data, timeout=10)
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
    r = requests.post(f"{api_base}{endpoint}", headers=headers, data=data, timeout=10)
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
        r = requests.get(f"{api_base}/vault/{filepath}", headers=headers, timeout=5)
        return r.status_code == 200
    except requests.exceptions.ConnectionError:
        raise ObsidianAPIError("Cannot connect to Obsidian Local REST API.")


def write_via_api(api_base: str, filepath: str, content: str, headers: dict, overwrite: bool = False) -> bool:
    """Write note via API."""
    if note_exists_api(api_base, filepath, headers) and not overwrite:
        log.info(f"[skip] Note exists: {filepath}")
        return False

    try:
        _api_put(api_base, f"/vault/{filepath}", headers, content.encode("utf-8"))
        log.info(f"[✓] API write ok: {filepath}")
        return True
    except Exception as e:
        log.error(f"[✗] API write fail: {filepath} ({e})")
        return False


def append_via_api(api_base: str, filepath: str, content: str, headers: dict) -> bool:
    """Append to note via API."""
    try:
        _api_post(api_base, f"/vault/{filepath}", headers, content.encode("utf-8"))
        log.info(f"[✓] API append ok: {filepath}")
        return True
    except Exception as e:
        log.error(f"[✗] API append fail: {filepath} ({e})")
        return False
