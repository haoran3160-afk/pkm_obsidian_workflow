# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [2.1.0] - 2026-03-28

### Added
- **Pydantic v2 config validation** (`config_schema.py`): `pkm_config.json` is now validated at startup with human-readable error messages for misconfigured feeds, invalid URLs, and wrong time formats.
- **`write_mode` configuration**: Set `"write_mode": "disk" | "api" | "both"` in `pkm_config.json` (or `PKM_WRITE_MODE` env var) to select the Vault write target. Previously only disk writes were supported even though the REST API interface existed.
- **Source Plugin Registry** (`fetcher_registry.py`): New Strategy Pattern registry that allows community plugins to register custom data source fetchers (e.g., Reddit, Twitter) without modifying `fetcher.py`.
- **Rich terminal summaries**: Run results are now rendered as a formatted table using the `rich` library, showing per-source item counts, status, and elapsed time.
- **Structured logging** (`structlog`): Machine-readable structured log output replaces plain text `logging` calls. Log keys follow `module.action.event` convention.
- **`--dry-run` mode**: New CLI flag that executes the full pipeline and prints all file paths that would be written, without performing any actual disk or API writes.
- **GitHub Actions CI** (`.github/workflows/ci.yml`): Automated pipeline with Ruff linting, Mypy type checking, and pytest across Python 3.10/3.11/3.12 with Codecov coverage reporting.
- **Community templates**: Added `ISSUE_TEMPLATE/bug_report.md`, `ISSUE_TEMPLATE/feature_request.md`, `pull_request_template.md`, `SECURITY.md`.
- **`pyproject.toml` full configuration**: Complete project metadata, `pkm` CLI entry point, Ruff/mypy/pytest/coverage tool configuration.

### Changed
- `requirements.txt`: Added `pydantic>=2.7.0`, `rich>=13.7.0`, `structlog>=24.1.0`.
- `requirements-dev.txt`: Added `ruff>=0.4.0`, `mypy>=1.10.0`, `pytest-cov>=5.0.0`.
- `main.py`: Refactored to use `PKMConfig` typed model instead of raw `dict` access. All config reads now go through validated Pydantic fields.

### Fixed
- Doctor mode now prints write mode and uses Rich styled output for clearer pass/fail indication.

---

## [2.0.0] - 2026-03-24

### Added
- **V2 ETL Architecture**: Monolithic `daily_fetch_v1.py` split into `fetcher.py` (Extract), `formatter.py` (Transform), `writer.py` (Load).
- **Jinja2 templates**: All Markdown note generation moved to `templates/*.md.j2`.
- **Network retry**: `tenacity` exponential backoff on all HTTP calls.
- **`.env` configuration**: Sensitive paths isolated via `python-dotenv`.
- **`--doctor` mode**: Pre-flight config and connectivity checker.
- **`--raw-only` mode**: Agent-friendly raw feed extraction.
- **Unit tests**: `pytest` test suite covering all ETL modules.
- **`CONTRIBUTING.md`**: ETL architecture rules for contributors.
- **`pkm_bridge.py`**: Obsidian Local REST API bridge.

### Removed
- `daily_fetch_v1.py` promoted to legacy reference (kept for comparison).

---

## [1.0.0] - 2026-03-16

### Added
- Initial single-file `daily_fetch_v1.py` implementation.
- RSS + YouTube feed fetching.
- Obsidian Vault disk-write output.
- Basic IELTS study log generation.

[Unreleased]: https://github.com/yourusername/obsidian_workflow_open/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/yourusername/obsidian_workflow_open/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/yourusername/obsidian_workflow_open/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/yourusername/obsidian_workflow_open/releases/tag/v1.0.0
