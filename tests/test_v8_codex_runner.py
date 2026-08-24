from __future__ import annotations

import hashlib
import json

import pytest

from pkm_workflow.codex_runner import (
    CONFIG_OVERRIDES,
    OFFICIAL_CODEX_ARCHIVE_SHA256,
    OFFICIAL_CODEX_CONFIG_SCHEMA_SHA256,
    OFFICIAL_CODEX_EXE_SHA256,
    CodexReleaseEvidence,
    CodexRole,
    CodexToolEventError,
)
from pkm_workflow.codex_runner import (
    audit_codex_jsonl_strict as _audit_codex_jsonl_strict,
)


def audit_codex_jsonl_strict(value: str | bytes, *, role: CodexRole):
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return _audit_codex_jsonl_strict(raw, role=role)


def _release_payload() -> dict[str, object]:
    return {
        "schema_version": "t1.3b-release-evidence-v1",
        "release_tag": "rust-v0.147.0",
        "asset_name": "codex-x86_64-pc-windows-msvc.exe.zip",
        "archive_sha256": OFFICIAL_CODEX_ARCHIVE_SHA256,
        "executable_name": "codex-x86_64-pc-windows-msvc.exe",
        "executable_sha256": OFFICIAL_CODEX_EXE_SHA256,
        "executable_size": 298_668_336,
        "version_output": "codex-cli 0.147.0",
        "pe_machine": "0x8664",
        "signature_status": "Valid",
        "signer_subject": (
            'CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", '
            "L=San Francisco, S=California, C=US"
        ),
        "signer_thumbprint": "8B0ADFB840E141DAD3044D2B5AC819873DDE3590",
        "config_schema_sha256": OFFICIAL_CODEX_CONFIG_SCHEMA_SHA256,
        "top_help_sha256": "3de771c53536ad76d05fe8fcd79bc03858bc366260cc38736a53b9fd20f662c2",
        "exec_help_sha256": "87d2b48149b3762a78dae5bdc293727e257f6d08743aaa03bd45db1b4e0386cd",
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_release_evidence_is_exact_strict_and_content_addressed() -> None:
    payload = _release_payload()
    encoded = _canonical(payload)

    evidence = CodexReleaseEvidence.from_json_bytes(encoded)

    assert evidence.executable_sha256 == OFFICIAL_CODEX_EXE_SHA256
    assert evidence.release_evidence_sha256 == hashlib.sha256(encoded).hexdigest()
    assert evidence.to_json_bytes() == encoded

    for field, mutation in (
        ("archive_sha256", "0" * 64),
        ("executable_sha256", "1" * 64),
        ("version_output", "codex-cli 0.148.0"),
        ("pe_machine", "0x014c"),
        ("signature_status", "UnknownError"),
        ("signer_subject", "CN=Someone Else"),
        ("config_schema_sha256", "2" * 64),
    ):
        changed = dict(payload)
        changed[field] = mutation
        with pytest.raises(ValueError):
            CodexReleaseEvidence.from_json_bytes(_canonical(changed))

    with pytest.raises(ValueError, match="canonical"):
        CodexReleaseEvidence.from_json_bytes(
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        )
    duplicate = encoded[:-1] + b',"release_tag":"rust-v0.147.0"}'
    with pytest.raises(ValueError, match="duplicate"):
        CodexReleaseEvidence.from_json_bytes(duplicate)
    with pytest.raises(ValueError, match="finite"):
        CodexReleaseEvidence.from_json_bytes(b'{"value":NaN}')


def test_release_evidence_rejects_unknown_types_encoding_and_nonobject() -> None:
    payload = _release_payload()
    missing = dict(payload)
    missing.pop("asset_name")
    with pytest.raises(ValueError, match="fields"):
        CodexReleaseEvidence.from_json_bytes(_canonical(missing))

    wrong_text = dict(payload)
    wrong_text["asset_name"] = 7
    with pytest.raises(ValueError, match="types"):
        CodexReleaseEvidence.from_json_bytes(_canonical(wrong_text))

    wrong_size = dict(payload)
    wrong_size["executable_size"] = True
    with pytest.raises(ValueError, match="types"):
        CodexReleaseEvidence.from_json_bytes(_canonical(wrong_size))

    with pytest.raises(ValueError, match="UTF-8"):
        CodexReleaseEvidence.from_json_bytes(b"\xff")
    with pytest.raises(ValueError, match="valid JSON"):
        CodexReleaseEvidence.from_json_bytes(b"{")
    with pytest.raises(ValueError, match="one JSON object"):
        CodexReleaseEvidence.from_json_bytes(b"[]")

    encoded = _canonical(payload)
    plain = CodexReleaseEvidence.from_json_bytes(encoded)
    terminated = CodexReleaseEvidence.from_json_bytes(encoded + b"\n")
    assert plain.to_json_bytes() == terminated.to_json_bytes() == encoded
    assert plain.release_evidence_sha256 == terminated.release_evidence_sha256


def test_frozen_config_has_no_unknown_0147_keys_and_disables_all_catalogs() -> None:
    assert "features.code_tool=false" not in CONFIG_OVERRIDES
    assert "features.mcp_client=false" not in CONFIG_OVERRIDES
    assert "features.image_tool=false" not in CONFIG_OVERRIDES
    required = {
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
        "features.apply_patch_freeform=false",
        'web_search="disabled"',
        "features.apps=false",
        "features.plugins=false",
        "features.remote_plugin=false",
        "features.memory_tool=false",
        "features.collab=false",
        "features.multi_agent=false",
        "features.tool_search=false",
        "features.image_generation=false",
        "features.js_repl=false",
        "features.responses_websockets=false",
        "features.responses_websockets_v2=false",
        "features.shell_snapshot=false",
        "features.shell_zsh_fork=false",
        "features.workspace_dependencies=false",
        "skills.include_instructions=false",
        "skills.config=[]",
        "orchestrator.mcp.enabled=false",
        "orchestrator.skills.enabled=false",
        "apps={}",
        "plugins={}",
        "mcp_servers={}",
    }
    assert required <= set(CONFIG_OVERRIDES)
    assert len(CONFIG_OVERRIDES) == len(set(CONFIG_OVERRIDES))


def _valid_jsonl(*, message: str = '{"stories":[]}') -> str:
    return "\n".join(
        (
            json.dumps(
                {
                    "type": "thread.started",
                    "thread_id": "019f2b90-0c34-77a1-ad02-b38355965b93",
                },
                separators=(",", ":"),
            ),
            '{"type":"turn.started"}',
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "reasoning",
                        "text": "bounded summary",
                    },
                },
                separators=(",", ":"),
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_1",
                        "type": "agent_message",
                        "text": message,
                    },
                },
                separators=(",", ":"),
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 30,
                        "reasoning_output_tokens": 10,
                    },
                },
                separators=(",", ":"),
            ),
        )
    )


def test_strict_jsonl_requires_full_lifecycle_and_real_fresh_thread() -> None:
    result = audit_codex_jsonl_strict(_valid_jsonl(), role=CodexRole.GENERATOR)

    assert result.thread_id == "019f2b90-0c34-77a1-ad02-b38355965b93"
    assert result.final_agent_message == '{"stories":[]}'
    assert result.tool_event_count == 0
    assert result.reasoning_item_count == 1
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 30
    assert result.event_count == 5

    lines = _valid_jsonl().splitlines()
    for malformed in (
        "\n".join(lines[1:]),
        "\n".join(lines[:-1]),
        "\n".join((lines[0], lines[2], lines[1], *lines[3:])),
        "\n".join((lines[0], lines[0], *lines[1:])),
        "\n".join((*lines[:-1], lines[-2], lines[-1])),
    ):
        with pytest.raises(CodexToolEventError):
            audit_codex_jsonl_strict(malformed, role=CodexRole.GENERATOR)


@pytest.mark.parametrize(
    "item_type",
    (
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
        "todo_list",
        "error",
    ),
)
def test_strict_jsonl_rejects_every_tool_or_unknown_item(item_type: str) -> None:
    lines = _valid_jsonl().splitlines()
    tool = json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "item_attack", "type": item_type},
        },
        separators=(",", ":"),
    )
    payload = "\n".join((*lines[:2], tool, *lines[2:]))
    with pytest.raises(CodexToolEventError, match="tool|unknown"):
        audit_codex_jsonl_strict(payload, role=CodexRole.REVIEWER)


@pytest.mark.parametrize(
    "payload",
    (
        '{"type":"thread.started","thread_id":"x","thread_id":"y"}',
        '{"type":"turn.completed","usage":{"input_tokens":NaN}}',
        '{"type":"future.event"}',
        '[]',
    ),
)
def test_strict_jsonl_rejects_duplicate_nonfinite_unknown_or_nonobject(
    payload: str,
) -> None:
    with pytest.raises(CodexToolEventError):
        audit_codex_jsonl_strict(payload, role=CodexRole.GENERATOR)


def test_strict_jsonl_accepts_only_unmodified_utf8_bytes() -> None:
    with pytest.raises(TypeError, match="must be bytes"):
        _audit_codex_jsonl_strict(_valid_jsonl(), role=CodexRole.GENERATOR)  # type: ignore[arg-type]
    with pytest.raises(CodexToolEventError, match="strict UTF-8"):
        _audit_codex_jsonl_strict(b"\xff", role=CodexRole.GENERATOR)


def test_generator_and_reviewer_thread_ids_must_be_distinct() -> None:
    generator = audit_codex_jsonl_strict(_valid_jsonl(), role=CodexRole.GENERATOR)
    reviewer = audit_codex_jsonl_strict(_valid_jsonl(), role=CodexRole.REVIEWER)

    with pytest.raises(CodexToolEventError, match="fresh"):
        reviewer.assert_fresh_from(generator)

    different = _valid_jsonl().replace(
        "019f2b90-0c34-77a1-ad02-b38355965b93",
        "019f2b90-0c34-77a1-ad02-b38355965b94",
        1,
    )
    audit_codex_jsonl_strict(
        different, role=CodexRole.REVIEWER
    ).assert_fresh_from(generator)


def test_strict_jsonl_rejects_identity_field_usage_and_size_mutants() -> None:
    lines = _valid_jsonl().splitlines()

    invalid_thread = json.loads(lines[0])
    invalid_thread["thread_id"] = "not-a-thread"
    extra_turn = json.loads(lines[1])
    extra_turn["unexpected"] = True
    extra_item = json.loads(lines[2])
    extra_item["unexpected"] = True
    duplicate_id = json.loads(lines[3])
    duplicate_id["item"]["id"] = "item_0"
    bad_usage_shape = json.loads(lines[4])
    bad_usage_shape["usage"].pop("output_tokens")
    bad_usage_value = json.loads(lines[4])
    bad_usage_value["usage"]["output_tokens"] = -1

    mutants = (
        "\n".join((json.dumps(invalid_thread), *lines[1:])),
        "\n".join((lines[0], json.dumps(extra_turn), *lines[2:])),
        "\n".join((*lines[:2], json.dumps(extra_item), *lines[3:])),
        "\n".join((*lines[:3], json.dumps(duplicate_id), lines[4])),
        "\n".join((*lines[:4], json.dumps(bad_usage_shape))),
        "\n".join((*lines[:4], json.dumps(bad_usage_value))),
        "\n".join((*lines, lines[1])),
    )
    for mutant in mutants:
        with pytest.raises(CodexToolEventError):
            audit_codex_jsonl_strict(mutant, role=CodexRole.GENERATOR)

    without_agent = "\n".join((*lines[:3], lines[4]))
    with pytest.raises(CodexToolEventError, match="without one agent"):
        audit_codex_jsonl_strict(without_agent, role=CodexRole.GENERATOR)


def test_strict_jsonl_rejects_duplicate_or_oversized_messages() -> None:
    lines = _valid_jsonl().splitlines()
    second_agent = json.loads(lines[2])
    second_agent["item"]["id"] = "item_2"
    second_agent["item"]["type"] = "agent_message"
    with pytest.raises(CodexToolEventError, match="multiple"):
        audit_codex_jsonl_strict(
            "\n".join((*lines[:3], json.dumps(second_agent), *lines[3:])),
            role=CodexRole.GENERATOR,
        )

    huge_reasoning = json.loads(lines[2])
    huge_reasoning["item"]["text"] = "r" * 100_001
    with pytest.raises(CodexToolEventError, match="reasoning"):
        audit_codex_jsonl_strict(
            "\n".join((*lines[:2], json.dumps(huge_reasoning), *lines[3:])),
            role=CodexRole.GENERATOR,
        )

    huge_agent = json.loads(lines[3])
    huge_agent["item"]["text"] = "a" * 1_000_001
    with pytest.raises(CodexToolEventError, match="agent message"):
        audit_codex_jsonl_strict(
            "\n".join((*lines[:3], json.dumps(huge_agent), lines[4])),
            role=CodexRole.GENERATOR,
        )

    with pytest.raises(CodexToolEventError, match="byte limit"):
        audit_codex_jsonl_strict(
            " " * 2_000_001,
            role=CodexRole.GENERATOR,
        )
