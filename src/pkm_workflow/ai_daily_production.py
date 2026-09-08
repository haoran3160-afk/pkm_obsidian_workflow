"""Create-new production publishing for one reviewed AI Daily."""

from __future__ import annotations

import hashlib
import json
import msvcrt
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO

from .daily_content import ShadowResult

VAULT_DAILY_DIR = Path(r"D:\personal\ObsidianVault\30-Daily")
PRODUCTION_LOCK_PATH = Path(
    r"D:\personal\obsidian_workflow-runtime\ai-daily-production.lock"
)
RECEIPT_SCHEMA = "pkm.ai-daily-production.receipt.v1"
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_PREPARED_FIELDS = {
    "schema",
    "status",
    "workflow",
    "content_date",
    "run_id",
    "request_count",
    "shadow_report_sha256",
    "backing_size",
    "backing_sha256",
    "backing_identity",
    "vault_path",
    "payload_hash",
}


@dataclass(frozen=True)
class ProductionResult:
    exit_code: int
    status: str
    content_date: date
    vault_path: Path | None
    report_path: Path | None
    request_count: int
    error_code: str | None = None
    shadow_report_path: Path | None = None
    vault_write_performed: bool = False
    production_activated: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "content_date": self.content_date.isoformat(),
            "vault_path": str(self.vault_path) if self.vault_path else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "shadow_report_path": (
                str(self.shadow_report_path) if self.shadow_report_path else None
            ),
            "request_count": self.request_count,
            "error_code": self.error_code,
            "vault_write": self.vault_write_performed,
            "production_activation": self.production_activated,
        }


@dataclass(frozen=True)
class _FileFacts:
    size: int
    sha256: str
    identity: dict[str, int]


@dataclass(frozen=True)
class _Prepared:
    payload: dict[str, Any]
    backing: Path
    shadow_report: Path
    backing_facts: _FileFacts


def _canonical_json(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_facts(path: Path) -> _FileFacts:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return _FileFacts(
        stat.st_size,
        "sha256:" + digest.hexdigest(),
        {"st_dev": stat.st_dev, "st_ino": stat.st_ino, "st_nlink": stat.st_nlink},
    )


def _seal(value: dict[str, object]) -> dict[str, object]:
    sealed = dict(value)
    sealed["payload_hash"] = _digest(_canonical_json(sealed))
    return sealed


def _read_sealed(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RECEIPT_NOT_OBJECT")
    claimed = value.get("payload_hash")
    body = {key: item for key, item in value.items() if key != "payload_hash"}
    if claimed != _digest(_canonical_json(body)):
        raise ValueError("RECEIPT_HASH_MISMATCH")
    return value


def _write_new(path: Path, content: bytes) -> _FileFacts:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    facts = _file_facts(path)
    if facts.size != len(content) or facts.sha256 != _digest(content):
        raise ValueError("CREATE_NEW_VERIFY_FAILED")
    return facts


def _write_or_verify(path: Path, content: bytes) -> _FileFacts:
    """Resume deterministic runtime artifacts; never use to adopt a Vault note."""
    try:
        return _write_new(path, content)
    except FileExistsError:
        facts = _file_facts(path)
        if facts.size != len(content) or facts.sha256 != _digest(content):
            raise ValueError("EXISTING_ARTIFACT_MISMATCH") from None
        return facts


def _write_receipt(path: Path, payload: dict[str, object]) -> Path:
    content = _canonical_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{uuid.uuid4().hex}.receipt-stage.json"
    _write_new(staging, content)
    try:
        os.link(staging, path)
    except FileExistsError:
        pass
    if path.read_bytes() != content or not path.samefile(staging):
        raise ValueError("RECEIPT_CANONICAL_MISMATCH")
    return path


def _paths(
    runtime_root: Path, content_date: date, run_id: str
) -> tuple[Path, Path, Path]:
    backing = (
        runtime_root
        / "durable"
        / "backing"
        / "ai-daily"
        / content_date.isoformat()
        / f"{run_id}.md"
    )
    report = (
        runtime_root
        / "durable"
        / "reports"
        / "ai-daily"
        / content_date.isoformat()
        / f"{run_id}.json"
    )
    return backing, report, runtime_root / "durable" / "receipts" / "ai-daily"


def _receipt_paths(runtime_root: Path, content_date: date) -> tuple[Path, Path]:
    receipt_root = runtime_root / "durable" / "receipts" / "ai-daily"
    stem = content_date.isoformat()
    return receipt_root / f"{stem}.prepared.json", receipt_root / f"{stem}.published.json"


def _shadow_run(
    shadow: ShadowResult, runtime_root: Path
) -> tuple[str, Path, Path]:
    if shadow.markdown_path is None or shadow.report_path is None:
        raise ValueError("SHADOW_ARTIFACTS_MISSING")
    markdown = shadow.markdown_path.resolve(strict=True)
    report = shadow.report_path.resolve(strict=True)
    run_id = markdown.parent.name
    expected = runtime_root / "scratch" / "runs" / run_id
    if (
        markdown.parent != report.parent
        or _RUN_ID.fullmatch(run_id) is None
        or markdown.parent != expected.resolve(strict=True)
    ):
        raise ValueError("SHADOW_RUN_PATH_INVALID")
    return run_id, markdown, report


def _prepared_payload(
    content_date: date,
    run_id: str,
    request_count: int,
    shadow_report: Path,
    backing_facts: _FileFacts,
    destination: Path,
) -> dict[str, object]:
    return _seal(
        {
            "schema": RECEIPT_SCHEMA,
            "status": "PREPARED",
            "workflow": "ai",
            "content_date": content_date.isoformat(),
            "run_id": run_id,
            "request_count": request_count,
            "shadow_report_sha256": _file_facts(shadow_report).sha256,
            "backing_size": backing_facts.size,
            "backing_sha256": backing_facts.sha256,
            "backing_identity": backing_facts.identity,
            "vault_path": str(destination),
        }
    )


def _load_prepared(
    path: Path,
    runtime_root: Path,
    content_date: date,
    destination: Path,
) -> _Prepared:
    payload = _read_sealed(path)
    if set(payload) != _PREPARED_FIELDS:
        raise ValueError("PREPARED_FIELDS_INVALID")
    run_id = payload.get("run_id")
    if (
        payload.get("schema") != RECEIPT_SCHEMA
        or payload.get("status") != "PREPARED"
        or payload.get("workflow") != "ai"
        or payload.get("content_date") != content_date.isoformat()
        or not isinstance(run_id, str)
        or _RUN_ID.fullmatch(run_id) is None
        or payload.get("vault_path") != str(destination)
        or type(payload.get("request_count")) is not int
    ):
        raise ValueError("PREPARED_BINDING_INVALID")
    backing, shadow_report, _ = _paths(runtime_root, content_date, run_id)
    backing_facts = _file_facts(backing)
    report_facts = _file_facts(shadow_report)
    stored_identity = payload.get("backing_identity")
    if (
        backing_facts.size != payload.get("backing_size")
        or backing_facts.sha256 != payload.get("backing_sha256")
        or report_facts.sha256 != payload.get("shadow_report_sha256")
        or not isinstance(stored_identity, dict)
        or backing_facts.identity["st_dev"] != stored_identity.get("st_dev")
        or backing_facts.identity["st_ino"] != stored_identity.get("st_ino")
    ):
        raise ValueError("PREPARED_ARTIFACT_MISMATCH")
    return _Prepared(payload, backing, shadow_report, backing_facts)


def _verify_target(prepared: _Prepared, destination: Path) -> _FileFacts:
    if not destination.samefile(prepared.backing):
        raise ValueError("VAULT_TARGET_IDENTITY_MISMATCH")
    target = _file_facts(destination)
    if (
        target.size != prepared.backing_facts.size
        or target.sha256 != prepared.backing_facts.sha256
        or target.identity["st_dev"] != prepared.backing_facts.identity["st_dev"]
        or target.identity["st_ino"] != prepared.backing_facts.identity["st_ino"]
        or target.identity["st_nlink"] < 2
    ):
        raise ValueError("VAULT_TARGET_VERIFY_FAILED")
    return target


def _published_payload(
    prepared: _Prepared, prepared_path: Path, target: _FileFacts
) -> dict[str, object]:
    base = {
        key: item
        for key, item in prepared.payload.items()
        if key not in {"status", "payload_hash"}
    }
    return _seal(
        {
            **base,
            "status": "PUBLISHED",
            "prepared_receipt_sha256": _file_facts(prepared_path).sha256,
            "target_size": target.size,
            "target_sha256": target.sha256,
            "target_identity": target.identity,
        }
    )


def _validate_published(
    path: Path,
    prepared: _Prepared,
    prepared_path: Path,
    destination: Path,
) -> None:
    actual = _read_sealed(path)
    expected = _published_payload(
        prepared, prepared_path, _verify_target(prepared, destination)
    )
    if actual != expected:
        raise ValueError("PUBLISHED_RECEIPT_MISMATCH")


def _conflict(
    content_date: date,
    error_code: str,
    destination: Path | None = None,
    report_path: Path | None = None,
    *,
    request_count: int = 0,
    shadow_report: Path | None = None,
    vault_write: bool = False,
) -> ProductionResult:
    return ProductionResult(
        3,
        "CONFLICT",
        content_date,
        destination,
        report_path,
        request_count,
        error_code,
        shadow_report_path=shadow_report,
        vault_write_performed=vault_write,
    )


def _pending(
    content_date: date,
    destination: Path,
    prepared_path: Path,
    request_count: int,
    shadow_report: Path,
    wrote_target: bool,
) -> ProductionResult:
    return ProductionResult(
        4,
        "PUBLISH_RECONCILE_REQUIRED",
        content_date,
        destination,
        prepared_path,
        request_count,
        "PUBLISH_TERMINAL_PENDING",
        shadow_report_path=shadow_report,
        vault_write_performed=wrote_target,
    )


def _reconcile(
    runtime_root: Path,
    content_date: date,
    destination: Path,
) -> ProductionResult | None:
    prepared_path, published_path = _receipt_paths(runtime_root, content_date)
    if published_path.exists():
        if not prepared_path.exists() or not destination.exists():
            return _conflict(
                content_date,
                "PUBLISH_RECEIPT_INVALID",
                destination if destination.exists() else None,
                published_path,
            )
        try:
            published_prepared = _load_prepared(
                prepared_path, runtime_root, content_date, destination
            )
            _validate_published(
                published_path, published_prepared, prepared_path, destination
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return _conflict(
                content_date, "PUBLISH_RECEIPT_INVALID", destination, published_path
            )
        return ProductionResult(
            0,
            "ALREADY_EXISTS",
            content_date,
            destination,
            published_path,
            0,
            shadow_report_path=published_prepared.shadow_report,
            production_activated=True,
        )
    if prepared_path.exists():
        wrote_target = False
        verified = False
        prepared: _Prepared | None = None
        try:
            prepared = _load_prepared(
                prepared_path, runtime_root, content_date, destination
            )
            if not destination.exists():
                destination.hardlink_to(prepared.backing)
                wrote_target = True
            target = _verify_target(prepared, destination)
            verified = True
            _write_receipt(
                published_path,
                _published_payload(prepared, prepared_path, target),
            )
            _validate_published(
                published_path, prepared, prepared_path, destination
            )
        except (OSError, ValueError, json.JSONDecodeError):
            if verified and prepared is not None:
                return _pending(
                    content_date,
                    destination,
                    prepared_path,
                    0,
                    prepared.shadow_report,
                    wrote_target,
                )
            return _conflict(
                content_date,
                "PREPARED_RECONCILIATION_FAILED",
                destination if destination.exists() else None,
                prepared_path,
                shadow_report=prepared.shadow_report if prepared else None,
                vault_write=wrote_target,
            )
        return ProductionResult(
            0,
            "PUBLISHED" if wrote_target else "ALREADY_EXISTS",
            content_date,
            destination,
            published_path,
            0,
            shadow_report_path=prepared.shadow_report,
            vault_write_performed=wrote_target,
            production_activated=True,
        )
    if destination.exists():
        return _conflict(
            content_date, "TARGET_EXISTS_WITHOUT_RECEIPT", destination
        )
    return None


def acquire_production_lock(path: Path) -> BinaryIO:
    """Acquire the existing nonblocking OS lock; caller closes via release."""
    handle = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise
    return handle


def release_production_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def run_ai_daily_production(
    content_date: date | None = None,
    *,
    vault_daily_dir: Path = VAULT_DAILY_DIR,
    coordination_path: Path = PRODUCTION_LOCK_PATH,
    shadow_runner: Callable[[date | None], ShadowResult],
) -> ProductionResult:
    selected_date = content_date or date.today()
    try:
        daily_dir = vault_daily_dir.resolve(strict=True)
        lock_path = coordination_path.resolve(strict=False)
        runtime_root = lock_path.parent.resolve(strict=True)
    except OSError:
        return ProductionResult(
            10,
            "PRODUCTION_FAILED",
            selected_date,
            None,
            None,
            0,
            "VAULT_PATH_INVALID",
        )
    destination = daily_dir / f"AI-Daily-{selected_date.isoformat()}.md"
    try:
        lock_handle = acquire_production_lock(lock_path)
    except OSError:
        return ProductionResult(
            4, "PRODUCTION_BUSY", selected_date, None, None, 0, "PRODUCTION_BUSY"
        )

    shadow: ShadowResult | None = None
    prepared_path: Path | None = None
    durable_report: Path | None = None
    wrote_target = False
    target_verified = False
    try:
        reconciled = _reconcile(runtime_root, selected_date, destination)
        if reconciled is not None:
            return reconciled
        shadow = shadow_runner(selected_date)
        if shadow.exit_code != 0 or shadow.markdown_path is None:
            return ProductionResult(
                shadow.exit_code,
                shadow.status,
                selected_date,
                None,
                shadow.report_path,
                shadow.request_count,
                shadow.error_code,
                shadow_report_path=shadow.report_path,
            )
        run_id, shadow_markdown, shadow_report = _shadow_run(shadow, runtime_root)
        backing, durable_report, _ = _paths(runtime_root, selected_date, run_id)
        _write_or_verify(durable_report, shadow_report.read_bytes())
        markdown = shadow_markdown.read_text(encoding="utf-8")
        production = (
            markdown.replace("type: ai-daily-shadow", "type: ai-daily", 1)
            .replace("production: false", "production: true", 1)
            .encode("utf-8")
        )
        backing_facts = _write_or_verify(backing, production)
        prepared_path, published_path = _receipt_paths(
            runtime_root, selected_date
        )
        _write_receipt(
            prepared_path,
            _prepared_payload(
                selected_date,
                run_id,
                shadow.request_count,
                durable_report,
                backing_facts,
                destination,
            ),
        )
        prepared = _load_prepared(
            prepared_path, runtime_root, selected_date, destination
        )
        destination.hardlink_to(backing)
        wrote_target = True
        target = _verify_target(prepared, destination)
        target_verified = True
        _write_receipt(
            published_path,
            _published_payload(prepared, prepared_path, target),
        )
        _validate_published(
            published_path, prepared, prepared_path, destination
        )
        return ProductionResult(
            0,
            "PUBLISHED",
            selected_date,
            destination,
            published_path,
            shadow.request_count,
            shadow_report_path=durable_report,
            vault_write_performed=True,
            production_activated=True,
        )
    except ValueError:
        if target_verified and prepared_path is not None and durable_report is not None:
            return _pending(
                selected_date,
                destination,
                prepared_path,
                shadow.request_count if shadow else 0,
                durable_report,
                wrote_target,
            )
        return _conflict(
            selected_date,
            "PRODUCTION_ARTIFACT_MISMATCH",
            destination if destination.exists() else None,
            prepared_path,
            request_count=shadow.request_count if shadow else 0,
            shadow_report=durable_report or (shadow.report_path if shadow else None),
            vault_write=wrote_target,
        )
    except OSError:
        if wrote_target and prepared_path is not None and durable_report is not None:
            return _pending(
                selected_date,
                destination,
                prepared_path,
                shadow.request_count if shadow else 0,
                durable_report,
                True,
            )
        return ProductionResult(
            10,
            "PRODUCTION_FAILED",
            selected_date,
            None,
            prepared_path or (shadow.report_path if shadow else None),
            shadow.request_count if shadow else 0,
            "PRODUCTION_WRITE_FAILED",
            shadow_report_path=durable_report or (shadow.report_path if shadow else None),
        )
    finally:
        release_production_lock(lock_handle)
