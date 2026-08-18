from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from credential_provider import (
    CredentialBindingService,
    CredentialLogicalName,
    CredentialProvider,
    CredentialRequirements,
    CredentialSource,
)

CANARY = "sk-canary-never-return-this-value"


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def test_requirement_matrix_does_not_require_keys_for_disk_only() -> None:
    assert (
        CredentialRequirements.for_workflow(
            write_mode="disk",
            llm_enabled=False,
        ).required
        == ()
    )
    assert CredentialRequirements.for_workflow(
        write_mode="disk",
        llm_enabled=True,
    ).required == (CredentialLogicalName.OPENAI_LLM,)
    assert CredentialRequirements.for_workflow(
        write_mode="both",
        llm_enabled=False,
    ).required == (CredentialLogicalName.OBSIDIAN_REST_READBACK,)

    with pytest.raises(ValueError, match="API write"):
        CredentialRequirements.for_workflow(
            write_mode="api",
            llm_enabled=False,
        )


def test_explicit_windows_binding_has_priority_over_controlled_environment() -> None:
    root = _case_root("credential-priority")
    service = CredentialBindingService(root)
    challenge = service.create_challenge(
        logical_name=CredentialLogicalName.OPENAI_LLM,
        target_name="PKM/OpenAI/generation-1",
    )
    service.bind_from_tty(
        challenge_id=challenge.challenge_id,
        typed_phrase=challenge.phrase,
        windows_identity="S-1-5-21-test",
        is_tty=True,
        caller="local_cli",
    )
    provider = CredentialProvider(
        binding_root=root,
        environ={"PKM_OPENAI_API_KEY": "environment-value"},
        credential_reader=lambda target: CANARY if target == "PKM/OpenAI/generation-1" else None,
        mode="observed",
    )

    resolved = provider.resolve(CredentialLogicalName.OPENAI_LLM)
    status = provider.status(CredentialLogicalName.OPENAI_LLM)

    assert resolved.reveal_for_engine() == CANARY
    assert resolved.source is CredentialSource.WINDOWS_CREDENTIAL_MANAGER
    serialized = json.dumps(status, ensure_ascii=False)
    assert CANARY not in serialized
    assert "environment-value" not in serialized
    assert status["configured"] is True
    assert len(status["target_hash"]) == 64


def test_controlled_environment_and_missing_status_never_reveal_secret() -> None:
    root = _case_root("credential-env")
    provider = CredentialProvider(
        binding_root=root,
        environ={"PKM_OPENAI_API_KEY": CANARY},
        credential_reader=lambda _target: None,
        mode="shadow",
    )

    resolved = provider.resolve(CredentialLogicalName.OPENAI_LLM)
    configured = provider.status(CredentialLogicalName.OPENAI_LLM)
    missing = provider.status(CredentialLogicalName.OBSIDIAN_REST_READBACK)

    assert resolved.source is CredentialSource.CONTROLLED_ENVIRONMENT
    assert resolved.reveal_for_engine() == CANARY
    assert CANARY not in repr(resolved)
    assert CANARY not in json.dumps(configured)
    assert missing["configured"] is False


def test_legacy_dotenv_is_shadow_only_and_emits_warning() -> None:
    root = _case_root("credential-legacy")
    legacy = root / ".env"
    with legacy.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"OPENAI_API_KEY={CANARY}\n")
        stream.flush()
        os.fsync(stream.fileno())

    shadow = CredentialProvider(
        binding_root=root,
        environ={},
        credential_reader=lambda _target: None,
        mode="shadow",
        legacy_env_path=legacy,
    )
    observed = CredentialProvider(
        binding_root=root,
        environ={},
        credential_reader=lambda _target: None,
        mode="observed",
        legacy_env_path=legacy,
    )

    shadow_value = shadow.resolve(CredentialLogicalName.OPENAI_LLM)
    observed_value = observed.resolve(CredentialLogicalName.OPENAI_LLM)

    assert shadow_value.source is CredentialSource.LEGACY_DOTENV
    assert shadow_value.warnings == ("LEGACY_SECRET_SOURCE",)
    assert observed_value.configured is False
    assert CANARY not in json.dumps(shadow.status(CredentialLogicalName.OPENAI_LLM))


@pytest.mark.parametrize(
    ("is_tty", "caller", "expected"),
    [
        (False, "local_cli", "TTY"),
        (True, "http", "local CLI"),
        (True, "codex", "local CLI"),
    ],
)
def test_binding_rejects_non_tty_and_remote_callers(
    is_tty: bool,
    caller: str,
    expected: str,
) -> None:
    root = _case_root(f"credential-reject-{caller}-{is_tty}")
    service = CredentialBindingService(root)
    challenge = service.create_challenge(
        logical_name=CredentialLogicalName.OPENAI_LLM,
        target_name="PKM/OpenAI/generation-1",
    )

    with pytest.raises(PermissionError, match=expected):
        service.bind_from_tty(
            challenge_id=challenge.challenge_id,
            typed_phrase=challenge.phrase,
            windows_identity="S-1-5-21-test",
            is_tty=is_tty,
            caller=caller,
        )

    assert tuple((root / "bindings").glob("*.json")) == ()


def test_binding_is_create_only_and_challenge_cannot_be_replayed() -> None:
    root = _case_root("credential-binding")
    service = CredentialBindingService(root)
    challenge = service.create_challenge(
        logical_name=CredentialLogicalName.OBSIDIAN_REST_READBACK,
        target_name="PKM/Obsidian/generation-2",
    )

    receipt = service.bind_from_tty(
        challenge_id=challenge.challenge_id,
        typed_phrase=challenge.phrase,
        windows_identity="S-1-5-21-test",
        is_tty=True,
        caller="local_cli",
    )
    before = tuple(root.rglob("*.json"))

    with pytest.raises(PermissionError, match="consumed"):
        service.bind_from_tty(
            challenge_id=challenge.challenge_id,
            typed_phrase=challenge.phrase,
            windows_identity="S-1-5-21-test",
            is_tty=True,
            caller="local_cli",
        )

    assert tuple(root.rglob("*.json")) == before
    assert receipt.target_hash
    assert len(tuple((root / "bindings").glob("*.json"))) == 1
