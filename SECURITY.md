# Security Policy

## Current support status

The `src/pkm_workflow` package is under active lightweight redevelopment. Production automation is not yet supported; the current CLI is expected to fail closed. Legacy root modules, Local UI/API, historical Agent workflows, migration scripts, and `native/` experiments are not supported execution surfaces.

The canonical threat model and controls are documented in [docs/agentic/spec.md](docs/agentic/spec.md) and [docs/agentic/decisions.md](docs/agentic/decisions.md).

## Security scope

This is a single-user local Windows workflow. It calls approved source endpoints and, when qualified, the user's Codex subscription; it does not host a public service.

First-release security protects against:

- malicious or malformed feed content, SSRF and redirect abuse;
- model prompt injection, unsupported claims and evidence mismatch;
- path traversal, out-of-scope Vault reads, overwrite and accidental deletion;
- concurrent runs, target conflicts and failure reported as success;
- authentication, quota, network and process-crash failure modes.

First release does not claim protection against a malicious local administrator or process that can modify the code/runtime, multi-tenant attacks, compliance-grade non-repudiation, or physical power-loss transaction durability.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose credentials or user data. Prefer GitHub Private Vulnerability Reporting; otherwise contact the maintainer through the repository profile.

Include the affected entrypoint, reproduction steps, expected data boundary, observed impact and any evidence of file/network access. Reports involving old files should also show that the code is reachable from the supported wheel, CLI or Task; preserved but fail-closed historical text alone is not an active vulnerability.

## Disclosure

Please allow reasonable time for validation and mitigation before public disclosure. A fix is not production-eligible until the relevant negative test and release-bound verification evidence exist.
