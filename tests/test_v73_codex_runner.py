from __future__ import annotations

import hashlib
import json
import operator
from dataclasses import replace
from pathlib import Path

import pytest

from pkm_workflow.codex_runner import (
    CodexInvocationBuilder,
    CodexQualificationError,
    CodexRole,
    CodexToolEventError,
    audit_codex_jsonl,
)


def _builder(tmp_path: Path) -> tuple[CodexInvocationBuilder, Path, Path]:
    release = tmp_path / "release"
    sealed = tmp_path / "sealed"
    codex_home = tmp_path / "codex-home"
    run_root = tmp_path / "isolated-run"
    for path in (release, sealed, codex_home, run_root):
        path.mkdir(parents=True)
    executable = release / "codex.exe"
    with executable.open("xb") as stream:
        stream.write(b"pinned-codex-binary")
    schema = sealed / "generator.schema.json"
    evidence = sealed / "evidence.json"
    proxy_contract = sealed / "https-proxy-contract.json"
    with schema.open("x", encoding="utf-8") as stream:
        stream.write('{"type":"object"}')
    with evidence.open("x", encoding="utf-8") as stream:
        stream.write('{"items":[]}')
    proxy_payload = json.dumps(
        {
            "schema_version": "7.3",
            "https_proxy": "http://127.0.0.1:43117",
            "connect_allowlist": ["api.openai.com:443", "chatgpt.com:443"],
            "loopback_only": True,
            "one_shot": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    with proxy_contract.open("xb") as stream:
        stream.write(proxy_payload)
    return (
        CodexInvocationBuilder(
            executable=executable,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            executable_version="0.147.0",
            codex_home=codex_home,
            run_root=run_root,
            sealed_root=sealed,
            https_proxy_contract=proxy_contract,
            https_proxy_contract_sha256=hashlib.sha256(proxy_payload).hexdigest(),
        ),
        schema,
        evidence,
    )


def _replace_proxy_contract(
    builder: CodexInvocationBuilder,
    payload: object,
    *,
    raw: bytes | None = None,
) -> CodexInvocationBuilder:
    encoded = raw or json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    identity = hashlib.sha256(encoded).hexdigest()
    replacement = builder.sealed_root / f"https-proxy-contract-{identity[:16]}.json"
    with replacement.open("xb") as stream:
        stream.write(encoded)
    return replace(
        builder,
        https_proxy_contract=replacement,
        https_proxy_contract_sha256=identity,
    )


def _valid_proxy_payload() -> dict[str, object]:
    return {
        "schema_version": "7.3",
        "https_proxy": "http://127.0.0.1:43117",
        "connect_allowlist": ["api.openai.com:443", "chatgpt.com:443"],
        "loopback_only": True,
        "one_shot": True,
    }


def test_planner_freezes_generator_and_reviewer_argv_without_tools(tmp_path: Path) -> None:
    builder, schema, evidence = _builder(tmp_path)

    generator = builder.plan(
        role=CodexRole.GENERATOR,
        output_schema=schema,
        evidence=evidence,
    )
    reviewer = builder.plan(
        role=CodexRole.REVIEWER,
        output_schema=schema,
        evidence=evidence,
    )

    assert generator.model == "gpt-5.6-terra"
    assert reviewer.model == "gpt-5.6-sol"
    assert generator.argv[0].endswith("codex.exe")
    assert generator.argv[:4] == (
        str(builder.executable.resolve()),
        "--ask-for-approval",
        "never",
        "exec",
    )
    for required in (
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--ephemeral",
        "--output-schema",
        "--json",
        "danger-full-access",
        "project_doc_max_bytes=0",
        "project_root_markers=[]",
        "project_doc_fallback_filenames=[]",
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.code_mode=false",
        "features.apply_patch_freeform=false",
        "features.web_search_request=false",
        "features.web_search_cached=false",
        "features.apps=false",
        "features.plugins=false",
        "features.remote_plugin=false",
        "features.memory_tool=false",
        "features.collab=false",
        "features.tool_search=false",
        "skills.include_instructions=false",
        "features.image_generation=false",
        "mcp_servers={}",
    ):
        assert required in generator.argv
    assert generator.argv[-1] == "-"
    assert generator.env == {
        "CODEX_HOME": str(builder.codex_home.resolve()),
        "HTTPS_PROXY": "http://127.0.0.1:43117",
        "NO_PROXY": "",
    }
    with pytest.raises(TypeError):
        operator.setitem(generator.env, "HTTPS_PROXY", "http://127.0.0.1:1")
    assert generator.allowed_read_files == (evidence.resolve(), schema.resolve())
    assert generator.session_id != reviewer.session_id
    assert generator.tool_policy == "ZERO_TOOLS_REQUIRES_REAL_PARSER_QUALIFICATION"
    assert generator.outer_sandbox == "APPCONTAINER_LPAC_WFP_REQUIRED"
    assert generator.production_eligible is False
    assert generator.activation_blockers == (
        "CODEX_0_147_REAL_PARSER_NOT_QUALIFIED",
        "APPCONTAINER_LPAC_WFP_NOT_QUALIFIED",
        "SEALED_PROXY_NOT_QUALIFIED",
    )
    assert not any("DeepSeek" in value for value in generator.argv)


def test_builder_rejects_wrong_binary_version_hash_and_unsealed_files(tmp_path: Path) -> None:
    builder, schema, evidence = _builder(tmp_path)
    tampered_executable = builder.executable.parent / "tampered-codex.exe"
    with tampered_executable.open("xb") as stream:
        stream.write(b"tampered")
    builder = replace(builder, executable=tampered_executable)
    with pytest.raises(ValueError, match="hash"):
        builder.plan(role=CodexRole.GENERATOR, output_schema=schema, evidence=evidence)

    wrong_version, schema, evidence = _builder(tmp_path / "other")
    object.__setattr__(wrong_version, "executable_version", "0.125.0")
    with pytest.raises(ValueError, match="0.147.0"):
        wrong_version.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )

    builder, schema, evidence = _builder(tmp_path / "third")
    outside = tmp_path / "outside.json"
    with outside.open("x", encoding="utf-8") as stream:
        stream.write("{}")
    with pytest.raises(ValueError, match="sealed root"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=outside,
        )

    builder, _schema, evidence = _builder(tmp_path / "directory-input")
    with pytest.raises(ValueError, match="must be a file"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=builder.sealed_root,
            evidence=evidence,
        )


def test_builder_rejects_a_run_root_that_can_inherit_git_instructions(
    tmp_path: Path,
) -> None:
    builder, schema, evidence = _builder(tmp_path)
    (builder.run_root / ".git").mkdir()

    with pytest.raises(ValueError, match="non-git"):
        builder.plan(
            role=CodexRole.REVIEWER,
            output_schema=schema,
            evidence=evidence,
        )


def test_builder_remains_blocked_until_real_parser_and_outer_sandbox_qualification(
    tmp_path: Path,
) -> None:
    builder, schema, evidence = _builder(tmp_path)

    with pytest.raises(CodexQualificationError, match="real parser"):
        builder.build(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )


def test_proxy_contract_is_sealed_loopback_only_and_create_time_bound(tmp_path: Path) -> None:
    builder, schema, evidence = _builder(tmp_path)
    invalid_payload = json.dumps(
        {
            "schema_version": "7.3",
            "https_proxy": "http://proxy.example:8080",
            "connect_allowlist": ["api.openai.com:443"],
            "loopback_only": True,
            "one_shot": True,
        }
    )
    replacement = builder.sealed_root / "https-proxy-contract-invalid-host.json"
    with replacement.open("x", encoding="utf-8") as stream:
        stream.write(invalid_payload)
    builder = replace(builder, https_proxy_contract=replacement)

    with pytest.raises(ValueError, match="hash"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )
    builder = replace(
        builder,
        https_proxy_contract_sha256=hashlib.sha256(
            builder.https_proxy_contract.read_bytes()
        ).hexdigest(),
    )
    with pytest.raises(ValueError, match="loopback"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )


def test_proxy_contract_rejects_an_unusable_loopback_port(tmp_path: Path) -> None:
    builder, schema, evidence = _builder(tmp_path)
    payload = json.dumps(
        {
            "schema_version": "7.3",
            "https_proxy": "http://127.0.0.1:0",
            "connect_allowlist": ["api.openai.com:443"],
            "loopback_only": True,
            "one_shot": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    replacement = builder.sealed_root / "https-proxy-contract-zero-port.json"
    with replacement.open("xb") as stream:
        stream.write(payload)
    builder = replace(
        builder,
        https_proxy_contract=replacement,
        https_proxy_contract_sha256=hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(ValueError, match="loopback"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"schema_version": "7.2"}, "one-shot contract"),
        ({"loopback_only": False}, "one-shot contract"),
        ({"one_shot": False}, "one-shot contract"),
        ({"https_proxy": 43117}, "URL must be text"),
        ({"https_proxy": "http://127.0.0.1:99999"}, "port is invalid"),
        ({"unexpected": True}, "fields are not exact"),
    ),
)
def test_proxy_contract_rejects_schema_flag_type_port_and_field_confusion(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    builder, schema, evidence = _builder(tmp_path)
    payload = _valid_proxy_payload()
    payload.update(mutation)
    builder = _replace_proxy_contract(builder, payload)

    with pytest.raises(ValueError, match=message):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )


def test_proxy_contract_rejects_non_json_and_noncanonical_hash_identifiers(
    tmp_path: Path,
) -> None:
    builder, schema, evidence = _builder(tmp_path)
    builder = _replace_proxy_contract(builder, {}, raw=b"not-json")
    with pytest.raises(ValueError, match="valid JSON"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )

    builder, schema, evidence = _builder(tmp_path / "bad-hash")
    builder = replace(builder, https_proxy_contract_sha256="A" * 64)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        builder.plan(
            role=CodexRole.GENERATOR,
            output_schema=schema,
            evidence=evidence,
        )


@pytest.mark.parametrize(
    "allowlist",
    (
        [],
        ["api.openai.com:443", "api.openai.com:443"],
        ["API.openai.com:443"],
        ["api.openai.com:80"],
        ["api.openai.com"],
        ["evilopenai.com:443"],
        [42],
    ),
)
def test_proxy_contract_rejects_ambiguous_or_non_openai_connect_destinations(
    tmp_path: Path,
    allowlist: list[object],
) -> None:
    builder, schema, evidence = _builder(tmp_path)
    payload = _valid_proxy_payload()
    payload["connect_allowlist"] = allowlist
    builder = _replace_proxy_contract(builder, payload)

    with pytest.raises(ValueError, match="CONNECT allowlist"):
        builder.plan(
            role=CodexRole.REVIEWER,
            output_schema=schema,
            evidence=evidence,
        )


def test_jsonl_auditor_rejects_every_tool_or_unknown_item_event() -> None:
    safe = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "fresh"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"claims":[]}'},
                }
            ),
            json.dumps({"type": "turn.completed"}),
        )
    )
    result = audit_codex_jsonl(safe)
    assert result.final_agent_message == '{"claims":[]}'
    assert result.tool_event_count == 0

    for item_type in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
        payload = json.dumps(
            {"type": "item.completed", "item": {"type": item_type}}
        )
        with pytest.raises(CodexToolEventError):
            audit_codex_jsonl(payload)

    with pytest.raises(CodexToolEventError, match="unknown event"):
        audit_codex_jsonl(json.dumps({"type": "future.unknown"}))
    with pytest.raises(CodexToolEventError, match="object"):
        audit_codex_jsonl("[]")


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ("{", "invalid JSONL"),
        ("", "exactly one"),
        (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": ""},
                }
            ),
            "message is empty",
        ),
        (
            "\n".join(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": value},
                    }
                )
                for value in ("first", "second")
            ),
            "exactly one",
        ),
    ),
)
def test_jsonl_auditor_fails_closed_on_malformed_or_ambiguous_completion(
    payload: str,
    message: str,
) -> None:
    with pytest.raises(CodexToolEventError, match=message):
        audit_codex_jsonl(payload)


def test_jsonl_auditor_ignores_only_blank_transport_lines() -> None:
    payload = "\n" + json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "sealed"},
        }
    ) + "\n\n"

    result = audit_codex_jsonl(payload)

    assert result.final_agent_message == "sealed"
    assert result.event_count == 1
