#!/usr/bin/env python3
"""Run the fixed T1.3b Python coverage gate without a coverage data file."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import stat
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any, TypedDict

SCHEMA_VERSION = "pkm.t13b.coverage.v1"
THRESHOLD_PERCENT = 90
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPOSITORY_ROOT / "pyproject.toml"
SOURCE_ROOT = REPOSITORY_ROOT / "src"
VENV_ROOT = REPOSITORY_ROOT / ".venv"
VENV_PYTHON = VENV_ROOT / "Scripts" / "python.exe"
VENV_SITE_PACKAGES = VENV_ROOT / "Lib" / "site-packages"
VENV_PYTHON_SHA256 = "0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14"
RUNTIME_SCRATCH = Path("D:/personal/obsidian_workflow-runtime/scratch")
VERIFICATION_ARTIFACTS_ROOT = Path("D:/personal/obsidian_workflow/verification-artifacts")
COVERAGE_ROOT_ENV = "PKM_T13B_COVERAGE_ROOT"

MODULES = (
    "src/pkm_workflow/curation.py",
    "src/pkm_workflow/codex_job.py",
    "src/pkm_workflow/codex_proxy.py",
    "src/pkm_workflow/codex_runner.py",
    "src/pkm_workflow/model_contract.py",
    "src/pkm_workflow/review_contract.py",
)
TESTS = (
    "tests/test_v8_curation.py",
    "tests/test_v8_codex_job.py",
    "tests/test_v8_codex_proxy.py",
    "tests/test_v8_codex_runner.py",
    "tests/test_v73_codex_runner.py",
    "tests/test_v8_model_contract.py",
    "tests/test_v8_review_contract.py",
)

EXIT_COVERAGE_FAILED = 1
EXIT_TESTS_FAILED = 3
EXIT_INTERNAL_ERROR = 10
OUTPUT_LIMIT_BYTES = 1_048_576
TRUSTED_DISTRIBUTIONS = {
    "colorama": "0.4.6",
    "coverage": "7.13.5",
    "iniconfig": "2.3.0",
    "packaging": "26.0",
    "pluggy": "1.6.0",
    "pygments": "2.19.2",
    "pytest": "9.0.2",
}
EXPECTED_RUNTIME_CLOSURE_SHA256 = (
    "sha256:f02dd6b952b95691b969598341ac8ea75514ff9cced8d4e1f8fe2dfa5e688d53"
)


class CoverageRootError(ValueError):
    """The constrained coverage-artifact parent is not safe to use."""


class ModuleCoverageResult(TypedDict):
    module: str
    statements: int
    missing_statements: int
    branches: int
    missing_branches: int
    line_pct: float
    branch_pct: float
    passed: bool


class _BoundedUtf8Sink:
    """Hash text output without retaining an attacker-controlled transcript."""

    def __init__(self, limit_bytes: int = OUTPUT_LIMIT_BYTES) -> None:
        self._limit_bytes = limit_bytes
        self._byte_count = 0
        self._digest = hashlib.sha256()
        self._overflow = False

    encoding = "utf-8"

    @property
    def byte_count(self) -> int:
        return self._byte_count

    @property
    def overflow(self) -> bool:
        return self._overflow

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def writable(self) -> bool:
        return True

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        if type(text) is not str:
            raise TypeError("coverage output must be text")
        for offset in range(0, len(text), 8_192):
            encoded = text[offset : offset + 8_192].encode("utf-8", errors="strict")
            self._digest.update(encoded)
            self._byte_count += len(encoded)
            if self._byte_count > self._limit_bytes:
                self._overflow = True
        return len(text)


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="scripts/run_t13b_coverage.py",
        description="Run the immutable T1.3b in-memory line+branch coverage gate.",
        allow_abbrev=False,
    )


def _fixed_paths(relative_paths: tuple[str, ...]) -> tuple[Path, ...]:
    repository = REPOSITORY_ROOT.resolve(strict=True)
    paths: list[Path] = []
    for relative_path in relative_paths:
        lexical_candidate = repository / relative_path
        _reject_reparse_components(lexical_candidate)
        candidate = lexical_candidate.resolve(strict=True)
        if not candidate.is_relative_to(repository) or not candidate.is_file():
            raise RuntimeError("fixed coverage input is not a repository file")
        paths.append(candidate)
    return tuple(paths)


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    details = resolved.stat()
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            digest.update(chunk)
    return {
        "path": str(resolved),
        "size": details.st_size,
        "sha256": "sha256:" + digest.hexdigest(),
        "volume": details.st_dev,
        "file_id": details.st_ino,
        "link_count": details.st_nlink,
    }


def _repository_input_paths() -> tuple[Path, ...]:
    repository = REPOSITORY_ROOT.resolve(strict=True)
    candidates: set[Path] = set()
    for lexical_path in (SOURCE_ROOT / "pkm_workflow").rglob("*"):
        _reject_reparse_components(lexical_path)
        if (
            "__pycache__" in lexical_path.parts
            or lexical_path.suffix.casefold() != ".py"
            or not lexical_path.is_file()
        ):
            continue
        resolved = lexical_path.resolve(strict=True)
        if not resolved.is_relative_to(repository):
            raise RuntimeError("coverage source input escaped the repository")
        candidates.add(resolved)
    for lexical_path in (REPOSITORY_ROOT / "tests").rglob("*"):
        _reject_reparse_components(lexical_path)
        if "__pycache__" in lexical_path.parts or lexical_path.suffix.casefold() in (
            ".pyc",
            ".pyo",
        ):
            continue
        if lexical_path.is_file():
            resolved = lexical_path.resolve(strict=True)
            if not resolved.is_relative_to(repository):
                raise RuntimeError("coverage test input escaped the repository")
            candidates.add(resolved)
    for lexical_path in (PYPROJECT, Path(__file__)):
        _reject_reparse_components(lexical_path)
        resolved = lexical_path.resolve(strict=True)
        if not resolved.is_relative_to(repository) or not resolved.is_file():
            raise RuntimeError("fixed coverage metadata escaped the repository")
        candidates.add(resolved)
    paths = tuple(sorted(candidates, key=lambda path: str(path).casefold()))
    if not paths or any(not path.is_relative_to(repository) for path in paths):
        raise RuntimeError("coverage input closure escaped the repository")
    for path in paths:
        if not path.is_file():
            raise RuntimeError("coverage input closure contains a non-file")
    return paths


def _input_closure(paths: Sequence[Path]) -> tuple[list[dict[str, Any]], str]:
    records = [_file_identity(path) for path in paths]
    if len({record["path"] for record in records}) != len(records):
        raise RuntimeError("fixed coverage inputs must be unique")
    closure_bytes = json.dumps(
        records, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return records, "sha256:" + hashlib.sha256(closure_bytes).hexdigest()


def _load_trusted_test_runtime() -> tuple[Any, Any, dict[str, Any]]:
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("coverage gate requires Python -I -S isolated mode")
    interpreter = Path(sys.executable).resolve(strict=True)
    expected_interpreter = VENV_PYTHON.resolve(strict=True)
    if interpreter != expected_interpreter:
        raise RuntimeError("coverage gate requires the fixed virtual-environment Python")
    if _file_identity(interpreter)["sha256"] != "sha256:" + VENV_PYTHON_SHA256:
        raise RuntimeError("coverage gate interpreter hash is not frozen")
    repository = REPOSITORY_ROOT.resolve(strict=True)
    if any(
        Path(entry).resolve(strict=False).is_relative_to(repository)
        for entry in sys.path
        if entry
    ):
        raise RuntimeError("repository paths were visible before runtime verification")
    site_packages = VENV_SITE_PACKAGES.resolve(strict=True)
    if not site_packages.is_dir():
        raise RuntimeError("fixed virtual-environment site-packages is unavailable")
    _reject_reparse_components(site_packages)
    runtime_records, runtime_closure = _distribution_closure(site_packages)
    if runtime_closure != EXPECTED_RUNTIME_CLOSURE_SHA256:
        raise RuntimeError("coverage runtime distribution closure is not frozen")
    sealed_root = _materialize_runtime(runtime_records)
    sealed_site_packages = sealed_root / "Lib" / "site-packages"
    if not sealed_site_packages.is_dir():
        raise RuntimeError("sealed coverage runtime has no site-packages")
    sys.path.append(str(sealed_site_packages))
    coverage = importlib.import_module("coverage")
    pytest = importlib.import_module("pytest")
    identities: dict[str, Any] = {}
    for name, module in (("coverage", coverage), ("pytest", pytest)):
        module_file = getattr(module, "__file__", None)
        if type(module_file) is not str:
            raise RuntimeError(f"{name} has no concrete module file")
        module_path = Path(module_file).resolve(strict=True)
        if not module_path.is_relative_to(sealed_site_packages):
            raise RuntimeError(f"{name} did not load from the isolated environment")
        _reject_reparse_components(module_path)
        identities[name] = str(module_path)
    identities["distribution_closure_sha256"] = runtime_closure
    identities["distribution_file_count"] = len(runtime_records)
    identities["sealed_runtime_root"] = str(sealed_root)
    return coverage, pytest, identities


def _distribution_closure(
    site_packages: Path,
) -> tuple[list[dict[str, Any]], str]:
    prefix = VENV_ROOT.resolve(strict=True)
    distributions = {
        distribution.metadata["Name"].lower(): distribution
        for distribution in importlib.metadata.distributions(path=[str(site_packages)])
        if distribution.metadata["Name"]
    }
    records: list[dict[str, Any]] = []
    for name, expected_version in sorted(TRUSTED_DISTRIBUTIONS.items()):
        distribution = distributions.get(name)
        if distribution is None:
            raise RuntimeError(f"missing {name} distribution")
        if distribution.version != expected_version:
            raise RuntimeError(f"unexpected {name} distribution version")
        files = distribution.files
        if files is None:
            raise RuntimeError(f"{name} distribution has no file inventory")
        for entry in sorted(files, key=str):
            lexical_path = Path(str(distribution.locate_file(entry)))
            _reject_reparse_components(lexical_path)
            path = lexical_path.resolve(strict=True)
            if not path.is_relative_to(prefix) or not path.is_file():
                raise RuntimeError(f"{name} distribution escaped the isolated environment")
            relative_path = path.relative_to(prefix)
            if "__pycache__" in relative_path.parts or relative_path.suffix.casefold() in (
                ".pyc",
                ".pyo",
            ):
                continue
            details = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1_048_576):
                    digest.update(chunk)
            records.append(
                {
                    "distribution": name,
                    "version": expected_version,
                    "path": relative_path.as_posix(),
                    "size": details.st_size,
                    "sha256": digest.hexdigest(),
                }
            )
    if len({record["path"] for record in records}) != len(records):
        raise RuntimeError("trusted distributions contain duplicate file paths")
    closure_bytes = json.dumps(
        records, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return records, "sha256:" + hashlib.sha256(closure_bytes).hexdigest()


def _create_new_sealed_runtime_root() -> Path:
    temporary_root = resolve_coverage_root()
    for _attempt in range(16):
        candidate = temporary_root / f"rt-{uuid.uuid4().hex[:16]}"
        try:
            candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            continue
        _reject_reparse_components(candidate)
        if candidate.parent.resolve(strict=True) != temporary_root:
            raise RuntimeError("sealed runtime root escaped its fixed parent")
        return candidate
    raise RuntimeError("could not reserve a create-new sealed runtime root")


def _materialize_runtime(records: Sequence[Mapping[str, Any]]) -> Path:
    source_root = VENV_ROOT.resolve(strict=True)
    sealed_root = _create_new_sealed_runtime_root()
    longest_target = max(
        len(str(sealed_root / Path(*PureWindowsPath(str(record["path"])).parts)))
        for record in records
    )
    if longest_target >= 240:
        raise CoverageRootError("coverage root is too deep for the sealed runtime")
    for record in records:
        relative = PureWindowsPath(str(record["path"]))
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise RuntimeError("trusted runtime record has an unsafe relative path")
        source = (source_root / Path(*relative.parts)).resolve(strict=True)
        target = sealed_root / Path(*relative.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _reject_reparse_components(target.parent)
        if not target.parent.resolve(strict=True).is_relative_to(sealed_root):
            raise RuntimeError("sealed runtime target escaped its root")
        digest = hashlib.sha256()
        byte_count = 0
        with source.open("rb") as source_stream, target.open("xb") as target_stream:
            while chunk := source_stream.read(1_048_576):
                digest.update(chunk)
                byte_count += len(chunk)
                target_stream.write(chunk)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        if (
            byte_count != record["size"]
            or digest.hexdigest() != record["sha256"]
        ):
            raise RuntimeError("trusted runtime source changed during materialization")
    _validate_materialized_runtime(sealed_root, records)
    return sealed_root


def _validate_materialized_runtime(
    sealed_root: Path, records: Sequence[Mapping[str, Any]]
) -> None:
    expected_paths = {str(record["path"]) for record in records}
    actual_paths = {
        path.relative_to(sealed_root).as_posix()
        for path in sealed_root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RuntimeError("sealed runtime file set differs from the trusted inventory")
    for record in records:
        path = sealed_root / Path(*PureWindowsPath(str(record["path"])).parts)
        _reject_reparse_components(path)
        details = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1_048_576):
                digest.update(chunk)
        if details.st_size != record["size"] or digest.hexdigest() != record["sha256"]:
            raise RuntimeError("sealed runtime content does not match its trusted inventory")


def _seal_python_test_environment(pycache_root: Path) -> None:
    for key in tuple(os.environ):
        if key.startswith(("COVERAGE_", "PYTEST_")):
            os.environ.pop(key, None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTEST_ADDOPTS"] = ""
    os.environ["PYTEST_PLUGINS"] = ""
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONPYCACHEPREFIX"] = str(pycache_root)
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(pycache_root)


def _is_within(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _reject_reparse_components(path: Path) -> None:
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    for component in components:
        try:
            details = os.lstat(component)
        except OSError as error:
            raise CoverageRootError("coverage root must be an existing directory") from error
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
            raise CoverageRootError("coverage root cannot contain a reparse component")


def resolve_coverage_root(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve the sole constrained environment seam for create-new pytest artifacts."""

    environment = os.environ if environ is None else environ
    raw_value: object = (
        environment[COVERAGE_ROOT_ENV]
        if COVERAGE_ROOT_ENV in environment
        else str(RUNTIME_SCRATCH)
    )
    if type(raw_value) is not str or not raw_value or raw_value != raw_value.strip():
        raise CoverageRootError("coverage root must be non-empty canonical text")
    windows_text = raw_value.replace("/", "\\")
    if windows_text.startswith("\\\\"):
        raise CoverageRootError("UNC and device coverage roots are forbidden")
    windows_path = PureWindowsPath(raw_value)
    if (
        not windows_path.is_absolute()
        or len(windows_path.drive) != 2
        or windows_path.drive[1:] != ":"
    ):
        raise CoverageRootError("coverage root must be an absolute local drive path")
    segments = windows_text[3:].split("\\")
    if any(
        not segment
        or segment in (".", "..")
        or ":" in segment
        or segment.endswith((" ", "."))
        for segment in segments
    ):
        raise CoverageRootError("coverage root contains an ambiguous path component")

    candidate = Path(str(windows_path))
    allowed_roots = (RUNTIME_SCRATCH, VERIFICATION_ARTIFACTS_ROOT)
    if not any(_is_within(candidate, root) for root in allowed_roots):
        raise CoverageRootError("coverage root is outside fixed artifact namespaces")
    _reject_reparse_components(candidate)
    try:
        resolved = candidate.resolve(strict=True)
        resolved_allowed_roots = tuple(root.resolve(strict=True) for root in allowed_roots)
    except OSError as error:
        raise CoverageRootError("coverage root must resolve inside an existing namespace") from error
    if not resolved.is_dir() or not any(
        _is_within(resolved, root) for root in resolved_allowed_roots
    ):
        raise CoverageRootError("coverage root must be a directory inside a fixed namespace")
    return resolved


def _create_new_basetemp() -> tuple[Path, Path]:
    temporary_root = resolve_coverage_root()
    for _attempt in range(16):
        reservation = temporary_root / f"pt-{uuid.uuid4().hex[:16]}"
        try:
            reservation.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            continue
        _reject_reparse_components(reservation)
        if reservation.parent.resolve(strict=True) != temporary_root:
            raise RuntimeError("pytest reservation escaped its fixed parent")
        candidate = reservation / "basetemp"
        if candidate.exists():
            raise RuntimeError("pytest basetemp was unexpectedly pre-created")
        return reservation, candidate
    raise RuntimeError("could not reserve a create-new pytest basetemp")


def _create_new_pycache_root() -> Path:
    temporary_root = resolve_coverage_root()
    for _attempt in range(16):
        candidate = temporary_root / f"pc-{uuid.uuid4().hex[:16]}"
        try:
            candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            continue
        _reject_reparse_components(candidate)
        if candidate.parent.resolve(strict=True) != temporary_root:
            raise RuntimeError("pycache root escaped its fixed parent")
        return candidate
    raise RuntimeError("could not reserve a create-new pycache root")


def passes_module_coverage(
    *,
    statements: int,
    missing_statements: int,
    branches: int,
    missing_branches: int,
) -> bool:
    """Return whether one module independently meets both fixed 90% gates."""

    values = (statements, missing_statements, branches, missing_branches)
    if any(type(value) is not int for value in values):
        raise TypeError("coverage counts must be exact integers")
    if statements <= 0 or branches <= 0:
        return False
    if not 0 <= missing_statements <= statements:
        raise ValueError("missing statements must be within the statement denominator")
    if not 0 <= missing_branches <= branches:
        raise ValueError("missing branches must be within the branch denominator")
    covered_statements = statements - missing_statements
    covered_branches = branches - missing_branches
    return (
        covered_statements * 100 >= THRESHOLD_PERCENT * statements
        and covered_branches * 100 >= THRESHOLD_PERCENT * branches
    )


def _percentage(total: int, missing: int) -> float:
    if total <= 0:
        return 0.0
    return round((total - missing) * 100 / total, 2)


def _module_coverage_result(
    cov: Any,
    *,
    relative_path: str,
    absolute_path: Path,
) -> ModuleCoverageResult:
    analyzed_filename, statement_lines, _excluded, missing_lines, _formatted = cov.analysis2(
        str(absolute_path)
    )
    if Path(analyzed_filename).resolve(strict=True) != absolute_path:
        raise RuntimeError("coverage analysis resolved a different module")

    branch_stats = cov.branch_stats(str(absolute_path))
    branches = 0
    taken_branches = 0
    for total_exits, taken_exits in branch_stats.values():
        if total_exits < 0 or taken_exits < 0 or taken_exits > total_exits:
            raise RuntimeError("coverage returned invalid branch statistics")
        branches += total_exits
        taken_branches += taken_exits

    statements = len(statement_lines)
    missing_statements = len(missing_lines)
    missing_branches = branches - taken_branches
    passed = passes_module_coverage(
        statements=statements,
        missing_statements=missing_statements,
        branches=branches,
        missing_branches=missing_branches,
    )
    return {
        "module": relative_path,
        "statements": statements,
        "missing_statements": missing_statements,
        "branches": branches,
        "missing_branches": missing_branches,
        "line_pct": _percentage(statements, missing_statements),
        "branch_pct": _percentage(branches, missing_branches),
        "passed": passed,
    }


def _create_new_tmp_path_plugin(pytest: Any, case_root: Path) -> Any:
    case_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    _reject_reparse_components(case_root)

    class CreateNewTmpPathPlugin:
        def __init__(self) -> None:
            self.failures: list[dict[str, str]] = []

        @pytest.fixture
        def tmp_path(self) -> Path:
            for _attempt in range(16):
                candidate = case_root / f"c-{uuid.uuid4().hex[:16]}"
                try:
                    candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
                except FileExistsError:
                    continue
                _reject_reparse_components(candidate)
                return candidate
            raise RuntimeError("could not reserve a create-new test directory")

        def pytest_runtest_logreport(self, report: Any) -> None:
            if report.failed and len(self.failures) < 32:
                self.failures.append(
                    {"nodeid": str(report.nodeid), "phase": str(report.when)}
                )

    return CreateNewTmpPathPlugin()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _run_gate() -> tuple[int, dict[str, Any]]:
    pycache_root = _create_new_pycache_root()
    _seal_python_test_environment(pycache_root)
    coverage, pytest, runtime_identities = _load_trusted_test_runtime()
    module_paths = _fixed_paths(MODULES)
    test_paths = _fixed_paths(TESTS)
    pyproject = PYPROJECT.resolve(strict=True)
    if not pyproject.is_file():
        raise RuntimeError("fixed pytest configuration is not a file")
    input_paths = _repository_input_paths()
    input_records_before, input_closure_sha256 = _input_closure(input_paths)
    reservation, basetemp = _create_new_basetemp()
    tmp_path_plugin = _create_new_tmp_path_plugin(pytest, reservation / "cases")

    sys.path.insert(0, str(SOURCE_ROOT))
    sys.path.insert(0, str(REPOSITORY_ROOT))

    pytest_stdout = _BoundedUtf8Sink()
    pytest_stderr = _BoundedUtf8Sink()
    cov = coverage.Coverage(
        branch=True,
        config_file=False,
        data_file=None,
        include=[str(path) for path in module_paths],
    )
    cov.start()
    try:
        from contextlib import redirect_stderr, redirect_stdout

        with redirect_stdout(pytest_stdout), redirect_stderr(pytest_stderr):
            pytest_exit_code = int(
                pytest.main(
                    [
                        "-p",
                        "no:cacheprovider",
                        "-p",
                        "no:pytest_cov",
                        "-o",
                        "addopts=",
                        "-o",
                        "tmp_path_retention_policy=all",
                        "--noconftest",
                        "--basetemp",
                        str(basetemp),
                        "--rootdir",
                        str(REPOSITORY_ROOT),
                        "-c",
                        str(pyproject),
                        "--tb=short",
                        "-q",
                        *(str(path) for path in test_paths),
                    ],
                    plugins=[tmp_path_plugin],
                )
            )
    finally:
        cov.stop()

    input_paths_after = _repository_input_paths()
    input_records_after, input_closure_after = _input_closure(input_paths_after)
    if (
        input_paths_after != input_paths
        or input_records_after != input_records_before
        or input_closure_after != input_closure_sha256
    ):
        raise RuntimeError("fixed coverage inputs changed during execution")
    runtime_records_after, runtime_closure_after = _distribution_closure(
        VENV_SITE_PACKAGES.resolve(strict=True)
    )
    if (
        runtime_closure_after
        != runtime_identities["distribution_closure_sha256"]
        or len(runtime_records_after) != runtime_identities["distribution_file_count"]
    ):
        raise RuntimeError("coverage runtime changed during execution")
    _validate_materialized_runtime(
        Path(runtime_identities["sealed_runtime_root"]), runtime_records_after
    )
    if any(pycache_root.iterdir()):
        raise RuntimeError("coverage gate unexpectedly wrote bytecode")
    _reject_reparse_components(reservation)
    basetemp_created = basetemp.exists()
    if basetemp_created:
        _reject_reparse_components(basetemp)
        if basetemp.resolve(strict=True).parent != reservation.resolve(strict=True):
            raise RuntimeError("pytest basetemp escaped its reserved parent")

    module_results = [
        _module_coverage_result(cov, relative_path=relative_path, absolute_path=absolute_path)
        for relative_path, absolute_path in zip(MODULES, module_paths, strict=True)
    ]
    total_statements = sum(result["statements"] for result in module_results)
    total_missing_statements = sum(result["missing_statements"] for result in module_results)
    total_branches = sum(result["branches"] for result in module_results)
    total_missing_branches = sum(result["missing_branches"] for result in module_results)
    coverage_percent = _percentage(
        total_statements + total_branches,
        total_missing_statements + total_missing_branches,
    )
    totals = {
        "statements": total_statements,
        "missing_statements": total_missing_statements,
        "branches": total_branches,
        "missing_branches": total_missing_branches,
        "line_pct": _percentage(total_statements, total_missing_statements),
        "branch_pct": _percentage(total_branches, total_missing_branches),
    }
    output_digest = hashlib.sha256(
        (
            f"stdout:{pytest_stdout.byte_count}:{pytest_stdout.sha256}\n"
            f"stderr:{pytest_stderr.byte_count}:{pytest_stderr.sha256}"
        ).encode("ascii")
    ).hexdigest()

    if pytest_stdout.overflow or pytest_stderr.overflow:
        terminal = "OUTPUT_OVERFLOW"
        exit_code = EXIT_TESTS_FAILED
    elif pytest_exit_code != 0:
        terminal = "TESTS_FAILED"
        exit_code = EXIT_TESTS_FAILED
    elif not all(result["passed"] for result in module_results):
        terminal = "COVERAGE_BELOW_THRESHOLD"
        exit_code = EXIT_COVERAGE_FAILED
    else:
        terminal = "PASSED"
        exit_code = 0

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "terminal": terminal,
        "threshold_percent": THRESHOLD_PERCENT,
        "coverage_percent": round(coverage_percent, 2),
        "branch": True,
        "data_file": None,
        "pytest_exit_code": pytest_exit_code,
        "pytest_failures": tmp_path_plugin.failures,
        "modules": list(MODULES),
        "module_results": module_results,
        "tests": list(TESTS),
        "totals": totals,
        "pytest_output_bytes": pytest_stdout.byte_count + pytest_stderr.byte_count,
        "pytest_output_sha256": "sha256:" + output_digest,
        "pytest_stdout_sha256": "sha256:" + pytest_stdout.sha256,
        "pytest_stderr_sha256": "sha256:" + pytest_stderr.sha256,
        "pytest_output_limit_bytes": OUTPUT_LIMIT_BYTES,
        "pytest_output_overflow": pytest_stdout.overflow or pytest_stderr.overflow,
        "pytest_basetemp": str(basetemp),
        "pytest_basetemp_created": basetemp_created,
        "pytest_reservation": str(reservation),
        "coverage_root": str(reservation.parent),
        "input_records": input_records_before,
        "input_closure_sha256": input_closure_sha256,
        "runtime_identities": runtime_identities,
        "isolated_mode": True,
        "pytest_plugin_autoload": False,
        "pytest_cacheprovider": False,
        "pytest_cov_plugin": False,
        "pytest_tmp_path_mode": "CREATE_NEW_NO_CLEANUP",
        "bytecode_writes": False,
        "pycache_root": str(pycache_root),
    }
    return exit_code, payload


def main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(list(argv) if argv is not None else None)
    try:
        exit_code, payload = _run_gate()
    except CoverageRootError as error:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "terminal": "COVERAGE_ROOT_REJECTED",
            "threshold_percent": THRESHOLD_PERCENT,
            "error_type": type(error).__name__,
        }
        exit_code = EXIT_TESTS_FAILED
    except Exception as error:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "terminal": "INTERNAL_ERROR",
            "threshold_percent": THRESHOLD_PERCENT,
            "error_type": type(error).__name__,
        }
        if isinstance(error, OSError):
            payload["error_winerror"] = error.winerror
            payload["error_filename"] = error.filename
        exit_code = EXIT_INTERNAL_ERROR
    print(_canonical_json(payload))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
