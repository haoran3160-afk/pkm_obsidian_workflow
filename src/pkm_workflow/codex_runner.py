"""Frozen Codex 0.147.0 invocation description and zero-tool event audit.

This module intentionally does not spawn Codex. The native broker must first
qualify AppContainer/WFP/proxy controls and then execute this sealed argv.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

REQUIRED_CODEX_VERSION = "0.147.0"
OFFICIAL_CODEX_RELEASE_TAG = "rust-v0.147.0"
OFFICIAL_CODEX_ARCHIVE_NAME = "codex-x86_64-pc-windows-msvc.exe.zip"
OFFICIAL_CODEX_ARCHIVE_SHA256 = (
    "a1c073ede409178502b1ff11bed635d2b644424352f9d4f3999de8272c6aebbd"
)
OFFICIAL_CODEX_EXE_NAME = "codex-x86_64-pc-windows-msvc.exe"
OFFICIAL_CODEX_EXE_SHA256 = (
    "935a1911ed2556e4ffcec995f4886ac2ac425863ba26fed264df62e30272ad9d"
)
OFFICIAL_CODEX_EXE_SIZE = 298_668_336
OFFICIAL_CODEX_CONFIG_SCHEMA_SHA256 = (
    "b9105f17442d5c41ba5d4d82603259d3cc0ceb38aeb3badf9e5ca20da328ae6e"
)
OFFICIAL_CODEX_SIGNER_SUBJECT = (
    'CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", '
    "L=San Francisco, S=California, C=US"
)
OFFICIAL_CODEX_SIGNER_THUMBPRINT = "8B0ADFB840E141DAD3044D2B5AC819873DDE3590"
OFFICIAL_CODEX_TOP_HELP_SHA256 = (
    "3de771c53536ad76d05fe8fcd79bc03858bc366260cc38736a53b9fd20f662c2"
)
OFFICIAL_CODEX_EXEC_HELP_SHA256 = (
    "87d2b48149b3762a78dae5bdc293727e257f6d08743aaa03bd45db1b4e0386cd"
)
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
THREAD_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MAX_JSONL_BYTES = 2_000_000
MAX_AGENT_MESSAGE_BYTES = 1_000_000
MAX_REASONING_SUMMARY_BYTES = 100_000
ACTIVATION_BLOCKERS = (
    "CODEX_0_147_REAL_PARSER_NOT_QUALIFIED",
    "APPCONTAINER_LPAC_WFP_NOT_QUALIFIED",
    "SEALED_PROXY_NOT_QUALIFIED",
)
CONFIG_OVERRIDES = (
    "allow_login_shell=false",
    "analytics.enabled=false",
    "feedback.enabled=false",
    'forced_login_method="chatgpt"',
    "notify=[]",
    'otel.exporter="none"',
    "otel.log_user_prompt=false",
    'otel.metrics_exporter="none"',
    'otel.trace_exporter="none"',
    "project_doc_max_bytes=0",
    "project_root_markers=[]",
    "project_doc_fallback_filenames=[]",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.code_mode=false",
    "features.code_mode_only=false",
    "features.code_mode_host=false",
    "features.apply_patch_freeform=false",
    "features.web_search=false",
    "features.web_search_request=false",
    "features.web_search_cached=false",
    "features.standalone_web_search=false",
    'web_search="disabled"',
    "features.apps=false",
    "features.enable_mcp_apps=false",
    "features.connectors=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.browser_use_full_cdp_access=false",
    "features.in_app_browser=false",
    "features.computer_use=false",
    "features.plugins=false",
    "features.remote_plugin=false",
    "features.recommended_plugins=false",
    "features.plugin_hooks=false",
    "features.plugin_sharing=false",
    "features.memory_tool=false",
    "features.memories=false",
    "features.js_repl=false",
    "features.external_agent_memory_import=false",
    "features.collab=false",
    "features.collaboration_modes=false",
    "features.multi_agent=false",
    "features.multi_agent_mode=false",
    "features.multi_agent_v2=false",
    "features.enable_fanout=false",
    "features.tool_search=false",
    "features.tool_suggest=false",
    "features.search_tool=false",
    "features.responses_websockets=false",
    "features.responses_websockets_v2=false",
    "features.shell_snapshot=false",
    "features.shell_zsh_fork=false",
    "features.image_generation=false",
    "features.imagegenext=false",
    "features.view_image=false",
    "features.workspace_dependencies=false",
    "features.mcp_2026_07_28=false",
    "features.tool_call_mcp_elicitation=false",
    "features.non_prefixed_mcp_tool_names=false",
    "features.skill_search=false",
    "features.skill_env_var_dependency_prompt=false",
    "features.skill_mcp_dependency_install=false",
    "skills.include_instructions=false",
    "skills.config=[]",
    "orchestrator.mcp.enabled=false",
    "orchestrator.skills.enabled=false",
    "apps={}",
    "plugins={}",
    "mcp_servers={}",
)

_RELEASE_EVIDENCE_FIELDS = {
    "schema_version",
    "release_tag",
    "asset_name",
    "archive_sha256",
    "executable_name",
    "executable_sha256",
    "executable_size",
    "version_output",
    "pe_machine",
    "signature_status",
    "signer_subject",
    "signer_thumbprint",
    "config_schema_sha256",
    "top_help_sha256",
    "exec_help_sha256",
}


class CodexRole(str, Enum):
    GENERATOR = "generator"
    REVIEWER = "reviewer"


class CodexToolEventError(RuntimeError):
    pass


class CodexQualificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexReleaseEvidence:
    """Content-addressed evidence for the one approved 0.147.0 Windows asset.

    This object does not inspect Authenticode itself.  It consumes the canonical,
    create-new record emitted by the Windows preflight and refuses every value
    outside the release identity qualified for T1.3b.
    """

    schema_version: str
    release_tag: str
    asset_name: str
    archive_sha256: str
    executable_name: str
    executable_sha256: str
    executable_size: int
    version_output: str
    pe_machine: str
    signature_status: str
    signer_subject: str
    signer_thumbprint: str
    config_schema_sha256: str
    top_help_sha256: str
    exec_help_sha256: str
    release_evidence_sha256: str = field(init=False, repr=False)

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> CodexReleaseEvidence:
        value = _strict_json_object(raw, "Codex release evidence")
        if set(value) != _RELEASE_EVIDENCE_FIELDS:
            raise ValueError("Codex release evidence fields are not exact")
        canonical = _canonical_json_bytes(value)
        if raw not in (canonical, canonical + b"\n"):
            raise ValueError("Codex release evidence must be canonical JSON")
        text_fields = _RELEASE_EVIDENCE_FIELDS - {"executable_size"}
        if any(type(value[name]) is not str for name in text_fields):
            raise ValueError("Codex release evidence field types are invalid")
        executable_size = value["executable_size"]
        if type(executable_size) is not int:
            raise ValueError("Codex release evidence field types are invalid")
        evidence = cls(
            schema_version=str(value["schema_version"]),
            release_tag=str(value["release_tag"]),
            asset_name=str(value["asset_name"]),
            archive_sha256=str(value["archive_sha256"]),
            executable_name=str(value["executable_name"]),
            executable_sha256=str(value["executable_sha256"]),
            executable_size=executable_size,
            version_output=str(value["version_output"]),
            pe_machine=str(value["pe_machine"]),
            signature_status=str(value["signature_status"]),
            signer_subject=str(value["signer_subject"]),
            signer_thumbprint=str(value["signer_thumbprint"]),
            config_schema_sha256=str(value["config_schema_sha256"]),
            top_help_sha256=str(value["top_help_sha256"]),
            exec_help_sha256=str(value["exec_help_sha256"]),
        )
        evidence._validate()
        object.__setattr__(
            evidence,
            "release_evidence_sha256",
            hashlib.sha256(canonical).hexdigest(),
        )
        return evidence

    def to_json_bytes(self) -> bytes:
        return _canonical_json_bytes(
            {
                field_name: getattr(self, field_name)
                for field_name in sorted(_RELEASE_EVIDENCE_FIELDS)
            }
        )

    def _validate(self) -> None:
        expected: dict[str, object] = {
            "schema_version": "t1.3b-release-evidence-v1",
            "release_tag": OFFICIAL_CODEX_RELEASE_TAG,
            "asset_name": OFFICIAL_CODEX_ARCHIVE_NAME,
            "archive_sha256": OFFICIAL_CODEX_ARCHIVE_SHA256,
            "executable_name": OFFICIAL_CODEX_EXE_NAME,
            "executable_sha256": OFFICIAL_CODEX_EXE_SHA256,
            "executable_size": OFFICIAL_CODEX_EXE_SIZE,
            "version_output": f"codex-cli {REQUIRED_CODEX_VERSION}",
            "pe_machine": "0x8664",
            "signature_status": "Valid",
            "signer_subject": OFFICIAL_CODEX_SIGNER_SUBJECT,
            "signer_thumbprint": OFFICIAL_CODEX_SIGNER_THUMBPRINT,
            "config_schema_sha256": OFFICIAL_CODEX_CONFIG_SCHEMA_SHA256,
            "top_help_sha256": OFFICIAL_CODEX_TOP_HELP_SHA256,
            "exec_help_sha256": OFFICIAL_CODEX_EXEC_HELP_SHA256,
        }
        for field_name, expected_value in expected.items():
            actual = getattr(self, field_name)
            if type(actual) is not type(expected_value) or actual != expected_value:
                raise ValueError(f"Codex release evidence {field_name} is not approved")
@dataclass(frozen=True)
class CodexInvocation:
    role: CodexRole
    model: str
    session_id: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    allowed_read_files: tuple[Path, ...]
    tool_policy: str
    outer_sandbox: str
    https_proxy_contract: Path
    https_proxy_contract_sha256: str
    production_eligible: bool
    activation_blockers: tuple[str, ...]


@dataclass(frozen=True)
class CodexAuditResult:
    final_agent_message: str
    tool_event_count: int
    event_count: int


@dataclass(frozen=True)
class CodexUsage:
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int


@dataclass(frozen=True)
class StrictCodexAuditResult:
    role: CodexRole
    thread_id: str
    final_agent_message: str
    usage: CodexUsage
    tool_event_count: int
    reasoning_item_count: int
    event_count: int

    def assert_fresh_from(self, previous: StrictCodexAuditResult) -> None:
        if self.thread_id == previous.thread_id:
            raise CodexToolEventError("Codex reviewer must use a fresh thread")


@dataclass(frozen=True)
class CodexInvocationBuilder:
    executable: Path
    executable_sha256: str
    executable_version: str
    codex_home: Path
    run_root: Path
    sealed_root: Path
    https_proxy_contract: Path
    https_proxy_contract_sha256: str

    def verify_official_release(self, evidence: CodexReleaseEvidence) -> str:
        """Re-read the selected executable before handing it to the native TCB."""

        evidence._validate()
        executable = self.executable.resolve(strict=True)
        if executable.name != OFFICIAL_CODEX_EXE_NAME:
            raise ValueError("Codex executable leaf is not the approved release asset")
        stat = executable.stat()
        if stat.st_size != evidence.executable_size:
            raise ValueError("Codex executable size does not match release evidence")
        observed_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        if observed_hash != evidence.executable_sha256:
            raise ValueError("Codex executable hash does not match release evidence")
        if self.executable_sha256 != evidence.executable_sha256:
            raise ValueError("Codex builder hash is not the approved release hash")
        if self.executable_version != REQUIRED_CODEX_VERSION:
            raise ValueError(f"Codex must be exactly {REQUIRED_CODEX_VERSION}")
        return observed_hash

    def plan(
        self,
        *,
        role: CodexRole,
        output_schema: Path,
        evidence: Path,
    ) -> CodexInvocation:
        executable = self.executable.resolve(strict=True)
        if self.executable_version != REQUIRED_CODEX_VERSION:
            raise ValueError(f"Codex must be exactly {REQUIRED_CODEX_VERSION}")
        observed_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        if observed_hash != self.executable_sha256:
            raise ValueError("Codex executable hash does not match sealed release")

        codex_home = self.codex_home.resolve(strict=True)
        run_root = self.run_root.resolve(strict=True)
        sealed_root = self.sealed_root.resolve(strict=True)
        if (run_root / ".git").exists():
            raise ValueError("Codex run root must be an isolated non-git directory")
        schema = _sealed_file(output_schema, sealed_root)
        evidence_file = _sealed_file(evidence, sealed_root)
        proxy_url, proxy_contract = _sealed_proxy_contract(
            self.https_proxy_contract,
            sealed_root,
            self.https_proxy_contract_sha256,
        )
        model = "gpt-5.6-terra" if role is CodexRole.GENERATOR else "gpt-5.6-sol"

        # The outer AppContainer/LPAC and WFP policy are the security boundary.
        # danger-full-access only prevents Codex from spawning an overlapping
        # helper sandbox. This exact argv is non-executable until a real 0.147.0
        # parser and zero-tool event trace are qualified on the release VM.
        argv: list[str] = [
            str(executable),
            "--ask-for-approval",
            "never",
            "exec",
            "--model",
            model,
            "--strict-config",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "danger-full-access",
            "--output-schema",
            str(schema),
            "--json",
            "-C",
            str(run_root),
        ]
        for override in CONFIG_OVERRIDES:
            argv.extend(("-c", override))
        argv.append("-")
        return CodexInvocation(
            role=role,
            model=model,
            session_id=f"{role.value}-{uuid.uuid4()}",
            argv=tuple(argv),
            env=MappingProxyType(
                {
                    "CODEX_HOME": str(codex_home),
                    "HTTPS_PROXY": proxy_url,
                    "NO_PROXY": "",
                }
            ),
            cwd=run_root,
            allowed_read_files=(evidence_file, schema),
            tool_policy="ZERO_TOOLS_REQUIRES_REAL_PARSER_QUALIFICATION",
            outer_sandbox="APPCONTAINER_LPAC_WFP_REQUIRED",
            https_proxy_contract=proxy_contract,
            https_proxy_contract_sha256=self.https_proxy_contract_sha256,
            production_eligible=False,
            activation_blockers=ACTIVATION_BLOCKERS,
        )

    def build(
        self,
        *,
        role: CodexRole,
        output_schema: Path,
        evidence: Path,
    ) -> CodexInvocation:
        self.plan(role=role, output_schema=output_schema, evidence=evidence)
        raise CodexQualificationError(
            "Codex 0.147.0 real parser, AppContainer/LPAC/WFP, and sealed proxy "
            "qualification are required before an executable invocation exists"
        )


def _sealed_file(path: Path, sealed_root: Path) -> Path:
    candidate = Path(path).resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("sealed input must be a file")
    try:
        candidate.relative_to(sealed_root)
    except ValueError as error:
        raise ValueError("Codex input is outside the sealed root") from error
    return candidate


def _sealed_proxy_contract(
    path: Path,
    sealed_root: Path,
    expected_sha256: str,
) -> tuple[str, Path]:
    if HASH_PATTERN.fullmatch(expected_sha256) is None:
        raise ValueError("proxy contract hash must be a lowercase SHA-256")
    candidate = _sealed_file(path, sealed_root)
    raw = candidate.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("sealed HTTPS proxy contract hash does not match release")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("sealed HTTPS proxy contract is not valid JSON") from error
    required = {
        "schema_version",
        "https_proxy",
        "connect_allowlist",
        "loopback_only",
        "one_shot",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("sealed HTTPS proxy contract fields are not exact")
    if (
        value["schema_version"] != "7.3"
        or value["loopback_only"] is not True
        or value["one_shot"] is not True
    ):
        raise ValueError("sealed HTTPS proxy contract is not a v7.3 one-shot contract")
    proxy_url = value["https_proxy"]
    if not isinstance(proxy_url, str):
        raise ValueError("sealed HTTPS proxy URL must be text")
    parsed = urlsplit(proxy_url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("sealed HTTPS proxy port is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or proxy_url != f"http://127.0.0.1:{port}"
    ):
        raise ValueError("sealed HTTPS proxy must be an exact IPv4 loopback endpoint")
    destinations = value["connect_allowlist"]
    if (
        not isinstance(destinations, list)
        or not destinations
        or len(destinations) != len(set(destinations))
        or any(not _allowed_openai_connect(value) for value in destinations)
    ):
        raise ValueError("sealed HTTPS proxy CONNECT allowlist is invalid")
    return proxy_url, candidate


def _allowed_openai_connect(value: object) -> bool:
    if not isinstance(value, str) or value.count(":") != 1:
        return False
    host, port = value.rsplit(":", 1)
    if port != "443" or host != host.lower():
        return False
    return host in {"api.openai.com", "auth.openai.com", "chatgpt.com"}


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"JSON numbers must be finite: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _strict_json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=_reject_nonfinite,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_event(line: str) -> dict[str, object]:
    try:
        return _strict_json_object(line.encode("utf-8"), "Codex JSONL event")
    except ValueError as error:
        raise CodexToolEventError(str(error)) from error


def _usage(value: object) -> CodexUsage:
    fields = {
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CodexToolEventError("Codex usage fields are not exact")
    numbers: dict[str, int] = {}
    for name in fields:
        number = value[name]
        if type(number) is not int or not 0 <= number <= 1_000_000_000:
            raise CodexToolEventError("Codex usage counters are invalid")
        numbers[name] = number
    return CodexUsage(**numbers)


def audit_codex_jsonl_strict(
    value: bytes,
    *,
    role: CodexRole,
) -> StrictCodexAuditResult:
    """Validate the exact successful 0.147.0 exec lifecycle.

    Reasoning summaries are counted and discarded.  Every executable/tool item,
    partial lifecycle, duplicated identity, unknown field, failure event, or
    trailing event is a security failure.
    """

    if type(value) is not bytes:
        raise TypeError("Codex JSONL audit input must be bytes")
    if len(value) > MAX_JSONL_BYTES:
        raise CodexToolEventError("Codex JSONL exceeds the audit byte limit")
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CodexToolEventError("Codex JSONL is not strict UTF-8") from error
    lines = [line for line in text.splitlines() if line.strip()]
    state = "EXPECT_THREAD"
    thread_id: str | None = None
    agent_message: str | None = None
    usage: CodexUsage | None = None
    item_ids: set[str] = set()
    reasoning_count = 0

    for line in lines:
        event = _strict_event(line)
        event_type = event.get("type")
        if state == "EXPECT_THREAD":
            if event_type != "thread.started" or set(event) != {"type", "thread_id"}:
                raise CodexToolEventError("Codex first event must be thread.started")
            candidate = event["thread_id"]
            if not isinstance(candidate, str) or THREAD_ID_PATTERN.fullmatch(candidate) is None:
                raise CodexToolEventError("Codex thread id is invalid")
            thread_id = candidate
            state = "EXPECT_TURN"
            continue

        if state == "EXPECT_TURN":
            if event_type != "turn.started" or set(event) != {"type"}:
                raise CodexToolEventError("Codex second event must be turn.started")
            state = "IN_TURN"
            continue

        if state == "COMPLETED":
            raise CodexToolEventError("Codex emitted an event after turn completion")

        if event_type == "item.completed":
            if set(event) != {"type", "item"}:
                raise CodexToolEventError("Codex item event fields are not exact")
            item = event["item"]
            if not isinstance(item, dict) or set(item) != {"id", "type", "text"}:
                raise CodexToolEventError("Codex emitted a tool or unknown item event")
            item_id = item["id"]
            item_type = item["type"]
            text = item["text"]
            if (
                not isinstance(item_id, str)
                or not item_id
                or item_id in item_ids
                or not isinstance(text, str)
                or not text
            ):
                raise CodexToolEventError("Codex item identity or text is invalid")
            item_ids.add(item_id)
            if item_type == "reasoning":
                if len(text.encode("utf-8")) > MAX_REASONING_SUMMARY_BYTES:
                    raise CodexToolEventError("Codex reasoning summary is too large")
                reasoning_count += 1
                continue
            if item_type == "agent_message":
                if agent_message is not None:
                    raise CodexToolEventError("Codex emitted multiple agent messages")
                if len(text.encode("utf-8")) > MAX_AGENT_MESSAGE_BYTES:
                    raise CodexToolEventError("Codex agent message is too large")
                agent_message = text
                continue
            raise CodexToolEventError("Codex emitted a tool or unknown item event")

        if event_type == "turn.completed":
            if set(event) != {"type", "usage"} or agent_message is None:
                raise CodexToolEventError("Codex turn completed without one agent message")
            usage = _usage(event["usage"])
            state = "COMPLETED"
            continue

        raise CodexToolEventError(f"Codex emitted a tool or unknown event: {event_type}")

    if state != "COMPLETED" or thread_id is None or agent_message is None or usage is None:
        raise CodexToolEventError("Codex JSONL lifecycle is incomplete")
    return StrictCodexAuditResult(
        role=role,
        thread_id=thread_id,
        final_agent_message=agent_message,
        usage=usage,
        tool_event_count=0,
        reasoning_item_count=reasoning_count,
        event_count=len(lines),
    )


def audit_codex_jsonl(value: str) -> CodexAuditResult:
    """Allow only lifecycle events and exactly one final agent message."""

    messages: list[str] = []
    count = 0
    for line in value.splitlines():
        if not line.strip():
            continue
        count += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CodexToolEventError("Codex emitted invalid JSONL") from error
        if not isinstance(event, dict):
            raise CodexToolEventError("Codex JSONL event must be an object")
        event_type = event.get("type")
        if event_type in {"thread.started", "turn.started", "turn.completed"}:
            continue
        if event_type != "item.completed":
            raise CodexToolEventError(f"Codex emitted unknown event: {event_type}")
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            raise CodexToolEventError("Codex emitted a tool or unknown item event")
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise CodexToolEventError("Codex agent message is empty")
        messages.append(text)

    if len(messages) != 1:
        raise CodexToolEventError("Codex must emit exactly one final agent message")
    return CodexAuditResult(
        final_agent_message=messages[0],
        tool_event_count=0,
        event_count=count,
    )
