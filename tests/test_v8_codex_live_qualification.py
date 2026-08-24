"""Optional real-asset identity check; this is not the T1.3b live system qualification."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pkm_workflow.codex_runner import (
    OFFICIAL_CODEX_EXE_SHA256,
    CodexInvocationBuilder,
    CodexReleaseEvidence,
)


def test_real_0147_asset_identity_matches_content_addressed_release_evidence() -> None:
    executable_value = os.environ.get("PKM_T13B_CODEX_EXE")
    evidence_value = os.environ.get("PKM_T13B_RELEASE_EVIDENCE")
    if not executable_value or not evidence_value:
        pytest.skip("explicit T1.3b release paths were not provided")

    executable = Path(executable_value)
    evidence_path = Path(evidence_value)
    evidence = CodexReleaseEvidence.from_json_bytes(evidence_path.read_bytes())
    placeholder = evidence_path.parent
    builder = CodexInvocationBuilder(
        executable=executable,
        executable_sha256=OFFICIAL_CODEX_EXE_SHA256,
        executable_version="0.147.0",
        codex_home=placeholder,
        run_root=placeholder,
        sealed_root=placeholder,
        https_proxy_contract=evidence_path,
        https_proxy_contract_sha256=hashlib.sha256(
            evidence_path.read_bytes()
        ).hexdigest(),
    )

    assert builder.verify_official_release(evidence) == OFFICIAL_CODEX_EXE_SHA256
