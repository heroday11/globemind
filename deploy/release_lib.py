"""Deterministic release staging and verification helpers.

This module is intentionally standard-library only so a release can be checked on
a host that does not have the GlobeMind runtime dependencies installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import urlsplit

SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSIONS = frozenset({1, 2})
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
DIRECT_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?\s+@\s+(\S+)$"
)

SOURCE_INPUTS = (
    "VERSION",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements/roles",
    "backend/api",
    "backend/agentic_rag",
    "backend/ai_search",
    "backend/data",
    "backend/serve_prod.py",
    "backend/nav_inject.html",
    "backend/cppt/cc_bridge.py",
    "backend/cppt/ppt-master/skills/ppt-master/scripts",
    "backend/tests",
    "config/runtime",
    "core_pipeline",
    "ops/release",
    "ops/runtime",
    "docs/operations/RUNTIME_SERVICE_CATALOG.md",
    "scripts",
    "quality",
    "frontend/package.json",
    "frontend/shared",
    "frontend/vue_project",
    "frontend/financial-terminal",
    "frontend/knowledge_graph_backup",
    "deploy",
    ".github/workflows",
)

RUNTIME_CATALOG_ARTIFACT_INPUTS = (
    "backend/tests/test_adaptive_extractor_resume.py",
    "backend/tests/test_wave1_loader_safety.py",
    "deploy/daily_news_ingest_ctl.sh",
    "deploy/ground_news_image_backfill_loop.sh",
    "deploy/ground_news_realtime_refresh_loop.sh",
    "deploy/l1_stream_workers_ctl.sh",
    "deploy/news_quality_labels_ctl.sh",
    "deploy/start_cloudflared.sh",
    "deploy/start_llm.sh",
    "deploy/start_web_prod.sh",
    "deploy/vllm_service_ctl.sh",
    "deploy/wave1_loader_ctl.sh",
    "deploy/wave1_remaining_extract_ctl.sh",
    "docs/operations/RUNTIME_SERVICE_CATALOG.md",
    "scripts/start_singbox_proxy_pool.py",
)

RELEASE_BACKEND_INPUTS = (
    "backend/api",
    "backend/agentic_rag",
    "backend/ai_search",
    "backend/data",
    "backend/serve_prod.py",
    "backend/nav_inject.html",
    "backend/cppt/cc_bridge.py",
    "backend/cppt/ppt-master/skills/ppt-master/scripts",
    "core_pipeline",
    "scripts/__init__.py",
    "scripts/db_runtime_config.py",
    "scripts/runtime_control/__init__.py",
    "scripts/runtime_control/catalog.py",
    "scripts/runtime_control/constants.py",
    "scripts/runtime_control/manifest.py",
    "scripts/runtime_control/redaction.py",
    "ops/runtime/services.json",
)
V1_RELEASE_BACKEND_INPUTS = (
    *RELEASE_BACKEND_INPUTS,
    *RUNTIME_CATALOG_ARTIFACT_INPUTS,
)

REQUIRED_RUNTIME_FILES = (
    "backend/serve_prod.py",
    "backend/api/application.py",
    "backend/cppt/cc_bridge.py",
    "backend/cppt/ppt-master/skills/ppt-master/scripts/image_gen.py",
    "backend/cppt/ppt-master/skills/ppt-master/scripts/image_backends/backend_common.py",
    "core_pipeline/__init__.py",
    "core_pipeline/event_coref_cluster.py",
    "scripts/__init__.py",
    "scripts/db_runtime_config.py",
)
V1_REQUIRED_RUNTIME_FILES = (
    *REQUIRED_RUNTIME_FILES,
    "scripts/runtime_control/__init__.py",
    "scripts/runtime_control/catalog.py",
    "scripts/runtime_control/constants.py",
    "scripts/runtime_control/manifest.py",
    "scripts/runtime_control/redaction.py",
    "ops/runtime/services.json",
    *RUNTIME_CATALOG_ARTIFACT_INPUTS,
)

LEGACY_LOCK_FILES = (
    "frontend/vue_project/package-lock.json",
    "frontend/vue_project/pnpm-lock.yaml",
    "frontend/financial-terminal/package-lock.json",
)
LOCK_FILES = (*LEGACY_LOCK_FILES, "requirements/roles/web.lock")

LEGACY_DEPENDENCY_MANIFEST_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "backend/api/requirements.txt",
    "backend/agentic_rag/requirements.txt",
    "backend/ai_search/requirements.txt",
)
DEPENDENCY_MANIFEST_FILES = (
    *LEGACY_DEPENDENCY_MANIFEST_FILES,
    "requirements/roles/web.in",
)

PYTHON_RUNTIME_ROLE = "web"
PYTHON_ROLE_INPUT = "requirements/roles/web.in"
PYTHON_ROLE_LOCK = "requirements/roles/web.lock"
PYTHON_RUNTIME_MANIFEST_NAME = "runtime.json"
PYTHON_RUNTIME_ARCHIVE_ROOT = "build-metadata/python-runtime"
PYTHON_RUNTIME_EVIDENCE_FILES = {
    "pip_freeze": "pip-freeze.txt",
    "pip_check": "pip-check.txt",
    "import_closure": "import-closure.json",
    "tests": "pytest-web.log",
}

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".vscode",
        "__pycache__",
        "coverage",
        "dist",
        "dist-ssr",
        "htmlcov",
        "node_modules",
        "test-results",
    }
)
LEGACY_EXCLUDED_DIRECTORY_NAMES = EXCLUDED_DIRECTORY_NAMES - {".vscode"}
LEGACY_EXCLUDED_SUFFIXES = frozenset(
    {".db", ".log", ".pid", ".pyc", ".pyo", ".sqlite", ".sqlite3"}
)
EXCLUDED_SUFFIXES = LEGACY_EXCLUDED_SUFFIXES | {".bak", ".orig", ".rej"}
EXCLUDED_FILE_NAMES = frozenset({".DS_Store"})
FORBIDDEN_ARTIFACT_CACHE_DIRECTORIES = frozenset(
    {".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
)
FORBIDDEN_ARTIFACT_CACHE_SUFFIXES = frozenset({".pyc", ".pyo"})
EXCLUDED_SUBTREES = (
    PurePosixPath("deploy/cloudflared"),
    PurePosixPath("frontend/vue_project/public/datasets/expert-skills"),
    PurePosixPath("frontend/vue_project/public/fin-terminal"),
    PurePosixPath("frontend/vue_project/public/imgs/hermes-generated"),
    PurePosixPath("frontend/vue_project/knowledge_graph_backup"),
)
LEGACY_EXCLUDED_SUBTREES = (
    PurePosixPath("deploy/cloudflared"),
    PurePosixPath("frontend/vue_project/public/fin-terminal"),
    PurePosixPath("frontend/vue_project/knowledge_graph_backup"),
)

CONTENT_BUNDLE_POLICY_PATH = PurePosixPath("ops/release/content-bundles.json")
CONTENT_BUNDLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("github_token", re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}")),
    ("openai_key", re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic_key", re.compile(rb"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}")),
    ("google_api_key", re.compile(rb"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}")),
    (
        "credentialed_database_url",
        re.compile(
            rb"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)://"
            rb"[^\s/:@]+:[^\s/@]+@",
            re.IGNORECASE,
        ),
    ),
)
SECRET_ALLOWLIST_PATH = PurePosixPath("quality/secret-scan-allowlist.json")

PRODUCTION_QUALITY_STEPS = frozenset(
    {
        "config",
        "root_layout",
        "content_bundles",
        "ruff_tool",
        "release_lint",
        "import_boundaries",
        "feature_registry",
        "runtime_config",
        "database_consumers",
        "source_secrets",
        "pytest",
        "frontend_lint",
        "frontend_contracts",
        "frontend_ratchet",
        "source_stability",
    }
)
HISTORICAL_PRODUCTION_QUALITY_STEPS = {
    "0.9.2": PRODUCTION_QUALITY_STEPS
    - {
        "root_layout",
        "content_bundles",
        "ruff_tool",
        "database_consumers",
        "feature_registry",
        "frontend_lint",
        "frontend_contracts",
    },
    "0.9.3": PRODUCTION_QUALITY_STEPS
    - {
        "root_layout",
        "content_bundles",
        "database_consumers",
        "feature_registry",
        "frontend_lint",
        "frontend_contracts",
    },
    "0.10.0": PRODUCTION_QUALITY_STEPS
    - {"content_bundles", "feature_registry", "root_layout"},
    "0.11.0": PRODUCTION_QUALITY_STEPS - {"content_bundles", "root_layout"},
}


class ReleaseError(RuntimeError):
    """Raised when a release invariant is violated."""


@dataclass(frozen=True)
class TreeDigest:
    sha256: str
    file_count: int
    total_bytes: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "sha256": self.sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version(project_dir: Path) -> str:
    version_file = project_dir / "VERSION"
    try:
        version = version_file.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise ReleaseError(f"cannot read VERSION: {version_file}") from exc
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError(f"invalid VERSION value: {version!r}")
    return version


def required_runtime_files(version: str) -> tuple[str, ...]:
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError(f"invalid release version: {version!r}")
    return V1_REQUIRED_RUNTIME_FILES if int(version.split(".", 1)[0]) >= 1 else REQUIRED_RUNTIME_FILES


def _is_excluded(
    relative_path: PurePosixPath,
    *,
    is_dir: bool,
    legacy_exclusions: bool = False,
) -> bool:
    directory_names = (
        LEGACY_EXCLUDED_DIRECTORY_NAMES if legacy_exclusions else EXCLUDED_DIRECTORY_NAMES
    )
    subtrees = LEGACY_EXCLUDED_SUBTREES if legacy_exclusions else EXCLUDED_SUBTREES
    if any(part in directory_names for part in relative_path.parts):
        return True
    if any(part.startswith(".venv") for part in relative_path.parts):
        return True
    name = relative_path.name
    if name == ".env" or name.startswith(".env."):
        return True
    if not is_dir:
        suffixes = LEGACY_EXCLUDED_SUFFIXES if legacy_exclusions else EXCLUDED_SUFFIXES
        if relative_path.suffix.lower() in suffixes:
            return True
        if not legacy_exclusions and (
            name in EXCLUDED_FILE_NAMES or name.startswith(".#") or name.endswith("~")
        ):
            return True
    return any(relative_path == root or root in relative_path.parents for root in subtrees)


def is_source_input_path(
    relative_path: str | PurePosixPath,
    inputs: Iterable[str] = SOURCE_INPUTS,
) -> bool:
    """Return whether a repository-relative file belongs to the release source scope."""
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return False
    roots = tuple(PurePosixPath(value) for value in inputs)
    if not any(path == root or root in path.parents for root in roots):
        return False
    return not _is_excluded(path, is_dir=False)


def _runtime_catalog_project_reference(
    value: Any,
    *,
    label: str,
    allowed_roots: tuple[str, ...],
) -> str:
    prefix = "${PROJECT_ROOT}/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ReleaseError(f"runtime catalog {label} must use {prefix}<relative-path>")
    raw_relative = value.removeprefix(prefix)
    relative = PurePosixPath(raw_relative)
    if (
        not raw_relative
        or "\\" in raw_relative
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != raw_relative
        or relative.parts[0] not in allowed_roots
    ):
        raise ReleaseError(f"runtime catalog {label} path is unsafe: {value!r}")
    relative_name = relative.as_posix()
    if not is_source_input_path(relative_name):
        raise ReleaseError(
            f"runtime catalog {label} is outside the attested source scope: {relative_name}"
        )
    return relative_name


def runtime_catalog_artifact_references(
    project_dir: Path,
    *,
    require_files: bool = True,
) -> tuple[str, ...]:
    """Return the exact immutable artifact closure declared by the V1 catalog."""

    manifest_path = project_dir / "ops/runtime/services.json"
    payload = load_json(manifest_path)
    services = payload.get("services")
    if not isinstance(services, list) or not services:
        raise ReleaseError("runtime catalog services must be a non-empty list")

    references: set[str] = set()
    for index, raw_service in enumerate(services):
        if not isinstance(raw_service, dict):
            raise ReleaseError(f"runtime catalog service {index} must be an object")
        service_id = raw_service.get("id")
        if not isinstance(service_id, str) or not service_id:
            raise ReleaseError(f"runtime catalog service {index} has no valid id")
        controller = raw_service.get("controller")
        if not isinstance(controller, dict):
            raise ReleaseError(f"runtime catalog service {service_id} has no controller")
        references.add(
            _runtime_catalog_project_reference(
                controller.get("path"),
                label=f"service {service_id} controller",
                allowed_roots=("deploy", "scripts"),
            )
        )
        entrypoint = controller.get("entrypoint")
        if entrypoint is not None:
            references.add(
                _runtime_catalog_project_reference(
                    entrypoint,
                    label=f"service {service_id} controller entrypoint",
                    allowed_roots=("deploy", "scripts"),
                )
            )

        runbook = raw_service.get("runbook")
        if not isinstance(runbook, dict):
            raise ReleaseError(f"runtime catalog service {service_id} has no runbook")
        references.add(
            _runtime_catalog_project_reference(
                runbook.get("path"),
                label=f"service {service_id} runbook",
                allowed_roots=("docs",),
            )
        )

        replay = raw_service.get("replay")
        evidence = replay.get("evidence") if isinstance(replay, dict) else None
        if not isinstance(evidence, list):
            raise ReleaseError(
                f"runtime catalog service {service_id} replay evidence must be a list"
            )
        for evidence_index, record in enumerate(evidence):
            if not isinstance(record, dict):
                raise ReleaseError(
                    f"runtime catalog service {service_id} replay evidence must be an object"
                )
            references.add(
                _runtime_catalog_project_reference(
                    record.get("path"),
                    label=f"service {service_id} replay evidence {evidence_index}",
                    allowed_roots=("backend",),
                )
            )

    expected = set(RUNTIME_CATALOG_ARTIFACT_INPUTS)
    if references != expected:
        missing = sorted(expected - references)
        unexpected = sorted(references - expected)
        raise ReleaseError(
            "runtime catalog artifact closure differs from the reviewed allowlist: "
            f"missing={missing} unexpected={unexpected}"
        )

    if require_files:
        for relative_name in sorted(references):
            path = project_dir / relative_name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ReleaseError(
                    f"runtime catalog artifact is unavailable: {relative_name}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ReleaseError(
                    f"runtime catalog artifact must be a non-symlink regular file: {relative_name}"
                )
    return tuple(sorted(references))


def iter_input_files(
    project_dir: Path,
    inputs: Iterable[str] = SOURCE_INPUTS,
    *,
    legacy_exclusions: bool = False,
) -> Iterator[tuple[Path, PurePosixPath]]:
    project_dir = project_dir.resolve()
    for input_name in sorted(set(inputs)):
        relative_root = PurePosixPath(input_name)
        source_root = project_dir / Path(*relative_root.parts)
        if not source_root.exists():
            continue
        if source_root.is_symlink():
            raise ReleaseError(f"release input must not be a symlink: {relative_root}")
        if source_root.is_file():
            if not _is_excluded(
                relative_root,
                is_dir=False,
                legacy_exclusions=legacy_exclusions,
            ):
                yield source_root, relative_root
            continue
        for directory, dir_names, file_names in os.walk(source_root, followlinks=False):
            directory_path = Path(directory)
            relative_directory = PurePosixPath(directory_path.relative_to(project_dir).as_posix())
            retained_directories: list[str] = []
            for name in sorted(dir_names):
                candidate = relative_directory / name
                path = directory_path / name
                if path.is_symlink():
                    raise ReleaseError(f"release input must not be a symlink: {candidate}")
                if not _is_excluded(
                    candidate,
                    is_dir=True,
                    legacy_exclusions=legacy_exclusions,
                ):
                    retained_directories.append(name)
            dir_names[:] = retained_directories
            for name in sorted(file_names):
                relative_file = relative_directory / name
                path = directory_path / name
                if path.is_symlink():
                    raise ReleaseError(f"release input must not be a symlink: {relative_file}")
                if not _is_excluded(
                    relative_file,
                    is_dir=False,
                    legacy_exclusions=legacy_exclusions,
                ):
                    yield path, relative_file


def digest_inputs(
    project_dir: Path,
    inputs: Iterable[str] = SOURCE_INPUTS,
    *,
    legacy_exclusions: bool = False,
) -> TreeDigest:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path, relative_path in iter_input_files(
        project_dir,
        inputs,
        legacy_exclusions=legacy_exclusions,
    ):
        file_digest = sha256_file(path)
        size = path.stat().st_size
        # Snapshots preserve executable intent while remaining stable after an
        # immutable release removes write bits from the archived source tree.
        executable_mode = stat.S_IMODE(path.stat().st_mode) & 0o111
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{executable_mode:o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
        count += 1
        total_bytes += size
    return TreeDigest(digest.hexdigest(), count, total_bytes)


def copy_inputs(
    project_dir: Path,
    destination: Path,
    inputs: Iterable[str] = SOURCE_INPUTS,
    *,
    legacy_exclusions: bool = False,
) -> TreeDigest:
    project_dir = project_dir.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for source, relative_path in iter_input_files(
        project_dir,
        inputs,
        legacy_exclusions=legacy_exclusions,
    ):
        target = destination / Path(*relative_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return digest_inputs(
        destination,
        inputs,
        legacy_exclusions=legacy_exclusions,
    )


def copy_release_backend(staged_project: Path, release_dir: Path) -> None:
    version = read_version(staged_project)
    inputs = RELEASE_BACKEND_INPUTS
    if int(version.split(".", 1)[0]) >= 1:
        runtime_catalog_artifact_references(staged_project)
        inputs = V1_RELEASE_BACKEND_INPUTS
    for source, relative_path in iter_input_files(staged_project, inputs):
        target = release_dir / Path(*relative_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def verify_runtime_catalog_artifact_copies(
    release_dir: Path,
    source_bundle: Path,
) -> None:
    """Verify that runtime catalog evidence is identical to its source archive."""

    root_references = runtime_catalog_artifact_references(release_dir)
    source_references = runtime_catalog_artifact_references(source_bundle)
    if root_references != source_references:
        raise ReleaseError("runtime catalog root and source closures differ")
    compared_files = ("ops/runtime/services.json", *root_references)
    for relative_name in compared_files:
        release_path = release_dir / relative_name
        source_path = source_bundle / relative_name
        if sha256_file(release_path) != sha256_file(source_path):
            raise ReleaseError(
                f"runtime catalog artifact differs from archived source: {relative_name}"
            )
        release_execute = stat.S_IMODE(release_path.stat().st_mode) & 0o111
        source_execute = stat.S_IMODE(source_path.stat().st_mode) & 0o111
        if release_execute != source_execute:
            raise ReleaseError(
                f"runtime catalog artifact executable mode differs from archived source: {relative_name}"
            )


def copy_dependency_files(
    staged_project: Path,
    metadata_dir: Path,
    relative_names: Iterable[str],
    artifact_subdirectory: str,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for relative_name in relative_names:
        source = staged_project / relative_name
        if not source.is_file():
            raise ReleaseError(f"missing dependency input file: {relative_name}")
        target = metadata_dir / artifact_subdirectory / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        results.append(
            {
                "path": relative_name,
                "artifact_path": target.relative_to(metadata_dir.parent).as_posix(),
                "sha256": sha256_file(source),
            }
        )
    return results


def copy_lock_files(staged_project: Path, metadata_dir: Path) -> list[dict[str, str]]:
    return copy_dependency_files(staged_project, metadata_dir, LOCK_FILES, "lockfiles")


def copy_dependency_manifests(staged_project: Path, metadata_dir: Path) -> list[dict[str, str]]:
    return copy_dependency_files(
        staged_project,
        metadata_dir,
        DEPENDENCY_MANIFEST_FILES,
        "dependency-manifests",
    )


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ReleaseError(f"{label} must be a SHA-256 digest")
    return value


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_fingerprint(payload: Mapping[str, Any]) -> str:
    fields = {
        "build_input_fingerprint": payload.get("build_input_fingerprint"),
        "pip_freeze_sha256": payload.get("pip_freeze_sha256"),
        "import_closure_sha256": payload.get("import_closure_sha256"),
        "pytest_log_sha256": payload.get("pytest_log_sha256"),
    }
    for key, value in fields.items():
        _require_sha256(value, f"runtime {key}")
    return _canonical_json_sha256(fields)


def _verify_hashed_python_lock(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleaseError("cannot read Web role lock") from exc
    blocks: list[str] = []
    current = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        blocks.append(current)
        current = ""
    if current:
        blocks.append(current)
    requirements = [block for block in blocks if not block.startswith("--")]
    if not requirements:
        raise ReleaseError("Web role lock contains no pinned requirements")
    for block in requirements:
        requirement = block.split("--hash=sha256:", 1)[0].strip()
        direct_match = DIRECT_REQUIREMENT_RE.fullmatch(requirement)
        direct_url_is_pinned = False
        if direct_match:
            parsed = urlsplit(direct_match.group(1))
            direct_url_is_pinned = bool(
                parsed.scheme == "https"
                and parsed.netloc
                and parsed.path
                and parsed.username is None
                and parsed.password is None
            )
        if ("==" not in requirement and not direct_url_is_pinned) or "--hash=sha256:" not in block:
            raise ReleaseError("Web role lock contains an unhashed or unpinned requirement")
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)", block)
        if not hashes:
            raise ReleaseError("Web role lock contains an invalid requirement hash")


def _validate_runtime_manifest_payload(
    payload: Mapping[str, Any],
    *,
    lock_sha256: str,
    production: bool,
) -> None:
    if payload.get("schema_version") != 1 or payload.get("role") != PYTHON_RUNTIME_ROLE:
        raise ReleaseError("Python runtime manifest identity is invalid")
    version = payload.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ReleaseError("Python runtime version is invalid")
    if payload.get("lock_sha256") != lock_sha256:
        raise ReleaseError("Python runtime lock digest does not match the Web role lock")
    _require_sha256(payload.get("build_input_fingerprint"), "runtime build input fingerprint")
    fingerprint = _require_sha256(payload.get("runtime_fingerprint"), "runtime fingerprint")
    if fingerprint != _runtime_fingerprint(payload):
        raise ReleaseError("Python runtime fingerprint is invalid")
    python = payload.get("python")
    if not isinstance(python, dict):
        raise ReleaseError("Python runtime interpreter metadata is missing")
    for key in ("version", "implementation", "platform", "machine"):
        if not isinstance(python.get(key), str) or not python[key]:
            raise ReleaseError(f"Python runtime {key} metadata is invalid")
    if python["implementation"] != "CPython":
        raise ReleaseError("Python runtime implementation must be CPython")
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        raise ReleaseError("Python runtime validation metadata is missing")
    required_statuses = {
        "pip_check": "pass",
        "critical_imports": "pass",
        "pytest_web": "pass" if production else validation.get("pytest_web"),
    }
    for key, expected in required_statuses.items():
        if validation.get(key) != expected:
            raise ReleaseError(f"Python runtime validation did not pass: {key}")


def _validate_import_closure(path: Path) -> None:
    payload = load_json(path)
    if payload.get("schema_version") != 1:
        raise ReleaseError("Python runtime import closure schema is invalid")
    recorded = _require_sha256(payload.get("closure_sha256"), "import closure fingerprint")
    unsigned = dict(payload)
    unsigned.pop("closure_sha256", None)
    if _canonical_json_sha256(unsigned) != recorded:
        raise ReleaseError("Python runtime import closure fingerprint is invalid")
    imports = payload.get("critical_imports")
    if (
        not isinstance(imports, list)
        or not imports
        or not all(isinstance(item, str) and item for item in imports)
    ):
        raise ReleaseError("Python runtime critical import evidence is invalid")


def _runtime_inventory_files(
    runtime_dir: Path,
    runtime_manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    runtime_dir = runtime_dir.resolve(strict=True)
    if runtime_manifest_path.is_symlink():
        raise ReleaseError("Python runtime manifest must not be a symlink")
    runtime_manifest_path = runtime_manifest_path.resolve(strict=True)
    try:
        runtime_manifest_path.relative_to(runtime_dir)
    except ValueError as exc:
        raise ReleaseError("Python runtime manifest escapes its runtime directory") from exc
    if runtime_manifest_path.name != PYTHON_RUNTIME_MANIFEST_NAME:
        raise ReleaseError("Python runtime manifest filename is invalid")
    payload = load_json(runtime_manifest_path)
    evidence = {
        name: runtime_manifest_path.parent / filename
        for name, filename in PYTHON_RUNTIME_EVIDENCE_FILES.items()
    }
    for name, path in evidence.items():
        if not path.is_file() or path.is_symlink():
            raise ReleaseError(f"Python runtime evidence is missing or unsafe: {name}")
    return payload, evidence


def _validate_runtime_evidence(
    payload: Mapping[str, Any], evidence: Mapping[str, Path]
) -> dict[str, dict[str, Any]]:
    manifest_hash_fields = {
        "pip_freeze": "pip_freeze_sha256",
        "import_closure": "import_closure_sha256",
        "tests": "pytest_log_sha256",
    }
    records: dict[str, dict[str, Any]] = {}
    for name, path in evidence.items():
        digest = sha256_file(path)
        manifest_field = manifest_hash_fields.get(name)
        if manifest_field and digest != payload.get(manifest_field):
            raise ReleaseError(f"Python runtime evidence digest mismatch: {name}")
        if path.stat().st_size <= 0:
            raise ReleaseError(f"Python runtime evidence is empty: {name}")
        records[name] = {"sha256": digest, "status": "passed"}
    _validate_import_closure(evidence["import_closure"])
    return records


def _validate_versioned_runtime_path(runtime_dir: Path, allowed_runtime_root: Path) -> Path:
    runtime_dir = runtime_dir.resolve(strict=True)
    allowed_runtime_root = allowed_runtime_root.resolve(strict=True)
    try:
        relative = runtime_dir.relative_to(allowed_runtime_root)
    except ValueError as exc:
        raise ReleaseError("Python runtime is outside the versioned runtime root") from exc
    if len(relative.parts) != 1 or relative.name.startswith("."):
        raise ReleaseError("Python runtime must be one immutable version below its runtime root")
    forbidden = ("/opt/conda/envs/", "/.env_torch/", "/.venv/")
    rendered = runtime_dir.as_posix() + "/"
    if any(marker in rendered for marker in forbidden):
        raise ReleaseError("shared live Python environments cannot be used as release runtimes")
    return runtime_dir


def _run_runtime_command(python: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    try:
        return subprocess.run(
            [str(python), *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ReleaseError(f"Python runtime validation command failed: {arguments[:3]}") from exc


def probe_python_runtime(runtime_dir: Path) -> dict[str, Any]:
    python = runtime_dir / "bin" / "python"
    if not python.is_file() or python.is_symlink() or not os.access(python, os.X_OK):
        raise ReleaseError("versioned Python runtime executable is missing or unsafe")
    try:
        python.resolve(strict=True).relative_to(runtime_dir)
    except ValueError as exc:
        raise ReleaseError("Python runtime executable escapes its versioned directory") from exc
    script = """
import json, platform, sys, sysconfig
print(json.dumps({
    'version': platform.python_version(),
    'implementation': platform.python_implementation(),
    'platform': sysconfig.get_platform(),
    'machine': platform.machine(),
    'soabi': sysconfig.get_config_var('SOABI'),
    'prefix': sys.prefix,
}, sort_keys=True))
"""
    try:
        probe = json.loads(_run_runtime_command(python, ["-I", "-c", script]).stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ReleaseError("Python runtime probe returned invalid metadata") from exc
    if not isinstance(probe, dict):
        raise ReleaseError("Python runtime probe returned invalid metadata")
    if Path(str(probe.get("prefix") or "")).resolve() != runtime_dir:
        raise ReleaseError("Python executable is not bound to the versioned runtime directory")
    for key in ("version", "implementation", "platform", "machine", "soabi"):
        if not isinstance(probe.get(key), str) or not probe[key]:
            raise ReleaseError(f"Python runtime probe did not report {key}")
    freeze = _run_runtime_command(python, ["-m", "pip", "freeze", "--all"]).stdout
    normalized_freeze = "".join(f"{line}\n" for line in sorted(freeze.splitlines()))
    probe["pip_freeze_sha256"] = hashlib.sha256(normalized_freeze.encode()).hexdigest()
    _run_runtime_command(python, ["-m", "pip", "check"])
    probe["executable_sha256"] = sha256_file(python)
    probe.pop("prefix", None)
    return probe


def archive_python_runtime_attestation(
    staged_project: Path,
    metadata_dir: Path,
    *,
    runtime_dir: Path,
    runtime_manifest_path: Path,
    allowed_runtime_root: Path,
    production: bool,
) -> dict[str, Any]:
    runtime_dir = _validate_versioned_runtime_path(runtime_dir, allowed_runtime_root)
    runtime_payload, evidence = _runtime_inventory_files(runtime_dir, runtime_manifest_path)
    role_lock = staged_project / PYTHON_ROLE_LOCK
    role_input = staged_project / PYTHON_ROLE_INPUT
    if not role_lock.is_file() or not role_input.is_file():
        raise ReleaseError("Web role input and hashed lock are required")
    lock_sha = sha256_file(role_lock)
    _verify_hashed_python_lock(role_lock)
    _validate_runtime_manifest_payload(runtime_payload, lock_sha256=lock_sha, production=production)
    if Path(str(runtime_payload.get("install_prefix") or "")).resolve() != runtime_dir:
        raise ReleaseError("Python runtime install_prefix does not match the selected runtime")
    if runtime_payload.get("version") != runtime_dir.name:
        raise ReleaseError("Python runtime directory version does not match its manifest")
    evidence_records = _validate_runtime_evidence(runtime_payload, evidence)
    for path in (
        runtime_dir,
        runtime_manifest_path,
        *evidence.values(),
        runtime_dir / "bin/python",
    ):
        if path.stat().st_mode & 0o022:
            raise ReleaseError("Python runtime contains writable attestation paths")
    probe = probe_python_runtime(runtime_dir)
    runtime_python = runtime_payload["python"]
    for key in ("version", "implementation", "platform", "machine"):
        if probe[key] != runtime_python.get(key):
            raise ReleaseError(f"Python runtime {key} differs from its manifest")
    if runtime_python.get("soabi") not in (None, probe["soabi"]):
        raise ReleaseError("Python runtime ABI differs from its manifest")
    if probe["pip_freeze_sha256"] != runtime_payload.get("pip_freeze_sha256"):
        raise ReleaseError("installed Python distributions differ from the runtime manifest")

    archive_root = metadata_dir / "python-runtime"
    archive_root.mkdir(parents=True, exist_ok=False)
    archived_manifest = archive_root / PYTHON_RUNTIME_MANIFEST_NAME
    shutil.copy2(runtime_manifest_path, archived_manifest)
    for name, source in evidence.items():
        target = archive_root / PYTHON_RUNTIME_EVIDENCE_FILES[name]
        shutil.copy2(source, target)
        evidence_records[name]["artifact_path"] = target.relative_to(metadata_dir.parent).as_posix()
    return {
        "role": PYTHON_RUNTIME_ROLE,
        "version": runtime_payload["version"],
        "role_input": {
            "path": PYTHON_ROLE_INPUT,
            "sha256": sha256_file(role_input),
        },
        "lock": {"path": PYTHON_ROLE_LOCK, "sha256": lock_sha},
        "runtime_manifest": {
            "schema_version": runtime_payload["schema_version"],
            "artifact_path": archived_manifest.relative_to(metadata_dir.parent).as_posix(),
            "sha256": sha256_file(archived_manifest),
        },
        "build_input_fingerprint": runtime_payload["build_input_fingerprint"],
        "runtime_fingerprint": runtime_payload["runtime_fingerprint"],
        "python": probe,
        "evidence": evidence_records,
        "validation": dict(runtime_payload["validation"]),
    }


def _load_secret_allowlist(root: Path) -> dict[tuple[str, str, str], str]:
    candidates = (
        root / Path(*SECRET_ALLOWLIST_PATH.parts),
        root / "build-metadata" / "source" / Path(*SECRET_ALLOWLIST_PATH.parts),
    )
    allowlist_path = next((path for path in candidates if path.is_file()), None)
    if allowlist_path is None:
        return {}
    payload = load_json(allowlist_path)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
        raise ReleaseError("secret scan allowlist schema is invalid")
    entries: dict[tuple[str, str, str], str] = {}
    valid_kinds = {kind for kind, _pattern in SECRET_PATTERNS}
    for item in payload["entries"]:
        if not isinstance(item, dict):
            raise ReleaseError("secret scan allowlist entry must be an object")
        path = item.get("path")
        kind = item.get("kind")
        digest = item.get("sha256")
        reason = item.get("reason")
        if (
            not isinstance(path, str)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or not path.startswith("frontend/vue_project/public/datasets/expert-skills/")
            or kind not in valid_kinds
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or not isinstance(reason, str)
            or len(reason.strip()) < 20
        ):
            raise ReleaseError(f"secret scan allowlist entry is invalid: {path!r}")
        key = (path, kind, digest)
        if key in entries:
            raise ReleaseError(f"duplicate secret scan allowlist entry: {path} {kind}")
        entries[key] = reason
    return entries


def _canonical_secret_path(relative: str) -> str:
    source_prefix = "build-metadata/source/"
    if relative.startswith(source_prefix):
        return relative[len(source_prefix) :]
    frontend_prefix = "frontend-dist/datasets/"
    if relative.startswith(frontend_prefix):
        return "frontend/vue_project/public/datasets/" + relative[len(frontend_prefix) :]
    return relative


def _scan_secret_records(
    root: Path,
    records: Iterable[tuple[Path, str]],
    *,
    allowlist_path_in_scope: Callable[[str], bool] | None = None,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    allowlist = _load_secret_allowlist(root)
    matched_allowlist: set[tuple[str, str, str]] = set()
    for path, relative in records:
        if path.is_symlink():
            findings.append({"path": relative, "kind": "symlink"})
            continue
        if not path.is_file():
            continue
        name = path.name.lower()
        if name == ".env" or name.startswith(".env."):
            findings.append({"path": relative, "kind": "environment_file"})
            continue
        if name.endswith((".key", ".p12", ".pfx", ".pem")):
            findings.append({"path": relative, "kind": "credential_file"})
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ReleaseError(f"cannot scan artifact file: {relative}") from exc
        digest = hashlib.sha256(data).hexdigest()
        canonical_path = _canonical_secret_path(relative)
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                allowlist_key = (canonical_path, kind, digest)
                if allowlist_key in allowlist:
                    matched_allowlist.add(allowlist_key)
                else:
                    findings.append({"path": relative, "kind": kind})
    stale_candidates = set(allowlist) - matched_allowlist
    if allowlist_path_in_scope is not None:
        stale_candidates = {
            key for key in stale_candidates if allowlist_path_in_scope(key[0])
        }
    for path, kind, _digest in sorted(stale_candidates):
        findings.append({"path": path, "kind": f"stale_allowlist:{kind}"})
    return findings


def scan_secrets(root: Path) -> list[dict[str, str]]:
    records = ((path, path.relative_to(root).as_posix()) for path in sorted(root.rglob("*")))
    return _scan_secret_records(root, records)


def scan_source_inputs(project_dir: Path) -> list[dict[str, str]]:
    project_dir = project_dir.resolve()
    records = ((path, relative.as_posix()) for path, relative in iter_input_files(project_dir))
    return _scan_secret_records(
        project_dir,
        records,
        allowlist_path_in_scope=lambda path: is_source_input_path(path),
    )


def _artifact_files(release_dir: Path) -> list[Path]:
    excluded = {release_dir / "release.json", release_dir / "SHA256SUMS"}
    files: list[Path] = []
    for path in sorted(release_dir.rglob("*")):
        relative_path = PurePosixPath(path.relative_to(release_dir).as_posix())
        if any(part in FORBIDDEN_ARTIFACT_CACHE_DIRECTORIES for part in relative_path.parts) or (
            path.is_file() and relative_path.suffix.lower() in FORBIDDEN_ARTIFACT_CACHE_SUFFIXES
        ):
            raise ReleaseError(f"release contains a forbidden cache artifact: {relative_path}")
        if path.is_symlink():
            raise ReleaseError(
                f"release artifacts must not contain symlinks: {path.relative_to(release_dir)}"
            )
        if path.is_file() and path not in excluded:
            relative = relative_path.as_posix()
            if "\n" in relative or "\r" in relative:
                raise ReleaseError(f"release filename contains a newline: {relative!r}")
            files.append(path)
    return files


def write_checksums(release_dir: Path) -> TreeDigest:
    lines: list[str] = []
    digest = hashlib.sha256()
    total_bytes = 0
    files = _artifact_files(release_dir)
    for path in files:
        relative = path.relative_to(release_dir).as_posix()
        file_hash = sha256_file(path)
        lines.append(f"{file_hash}  {relative}\n")
        total_bytes += path.stat().st_size
    payload = "".join(lines).encode("utf-8")
    (release_dir / "SHA256SUMS").write_bytes(payload)
    digest.update(payload)
    return TreeDigest(digest.hexdigest(), len(files), total_bytes)


def digest_tree(root: Path) -> TreeDigest:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReleaseError(f"tree must not contain symlinks: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        file_hash = sha256_file(path)
        size = path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        count += 1
        total_bytes += size
    return TreeDigest(digest.hexdigest(), count, total_bytes)


def iter_content_bundle_files(
    root: Path,
    *,
    reject_excluded: bool = False,
) -> Iterator[tuple[Path, PurePosixPath]]:
    root = root.resolve()
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        relative_directory = PurePosixPath(directory_path.relative_to(root).as_posix())
        retained_directories: list[str] = []
        for name in sorted(dir_names):
            relative = relative_directory / name
            path = directory_path / name
            if path.is_symlink():
                raise ReleaseError(f"content bundle must not contain symlinks: {relative}")
            if _is_excluded(relative, is_dir=True):
                if reject_excluded:
                    raise ReleaseError(
                        f"content bundle artifact contains an excluded directory: {relative}"
                    )
                continue
            retained_directories.append(name)
        dir_names[:] = retained_directories
        for name in sorted(file_names):
            relative = relative_directory / name
            path = directory_path / name
            if path.is_symlink():
                raise ReleaseError(f"content bundle must not contain symlinks: {relative}")
            if not path.is_file():
                raise ReleaseError(
                    f"content bundle must contain only regular files: {relative}"
                )
            if _is_excluded(relative, is_dir=False):
                if reject_excluded:
                    raise ReleaseError(
                        f"content bundle artifact contains an excluded file: {relative}"
                    )
                continue
            yield path, relative


def _digest_content_bundle(root: Path, *, reject_excluded: bool) -> TreeDigest:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path, relative in iter_content_bundle_files(
        root,
        reject_excluded=reject_excluded,
    ):
        file_hash = sha256_file(path)
        size = path.stat().st_size
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        count += 1
        total_bytes += size
    return TreeDigest(digest.hexdigest(), count, total_bytes)


def digest_content_bundle_source(root: Path) -> TreeDigest:
    return _digest_content_bundle(root, reject_excluded=False)


def digest_content_bundle_artifact(root: Path) -> TreeDigest:
    return _digest_content_bundle(root, reject_excluded=True)


def _safe_bundle_path(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ReleaseError(f"content bundle {field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ReleaseError(f"content bundle {field} must be a safe relative path")
    return path


def load_content_bundle_policy(project_dir: Path) -> list[dict[str, Any]]:
    policy_path = project_dir / Path(*CONTENT_BUNDLE_POLICY_PATH.parts)
    payload = load_json(policy_path)
    if set(payload) != {"schema_version", "bundles"} or payload.get("schema_version") != 1:
        raise ReleaseError("content bundle policy has an unsupported schema")
    bundles = payload.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        raise ReleaseError("content bundle policy must declare at least one bundle")

    expected_fields = {
        "id",
        "version",
        "source_path",
        "stage_path",
        "artifact_path",
        "sha256",
        "file_count",
        "total_bytes",
        "evidence",
    }
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_stage_paths: set[str] = set()
    seen_artifact_paths: set[str] = set()
    for raw in bundles:
        if not isinstance(raw, dict) or set(raw) != expected_fields:
            raise ReleaseError("content bundle record has an invalid field set")
        bundle_id = raw.get("id")
        if not isinstance(bundle_id, str) or not CONTENT_BUNDLE_ID_RE.fullmatch(bundle_id):
            raise ReleaseError("content bundle id is invalid")
        if bundle_id in seen_ids:
            raise ReleaseError(f"duplicate content bundle id: {bundle_id}")
        version = raw.get("version")
        if not isinstance(version, str) or not version.strip() or any(char.isspace() for char in version):
            raise ReleaseError(f"content bundle version is invalid: {bundle_id}")
        source_path = _safe_bundle_path(raw.get("source_path"), field="source_path")
        stage_path = _safe_bundle_path(raw.get("stage_path"), field="stage_path")
        artifact_path = _safe_bundle_path(raw.get("artifact_path"), field="artifact_path")
        if source_path != stage_path:
            raise ReleaseError(f"content bundle source/stage paths must match: {bundle_id}")
        if source_path.parts[:3] != ("frontend", "vue_project", "public"):
            raise ReleaseError(f"content bundle must stage below frontend public: {bundle_id}")
        if is_source_input_path(source_path):
            raise ReleaseError(f"content bundle overlaps application source inputs: {bundle_id}")
        if stage_path.as_posix() in seen_stage_paths or artifact_path.as_posix() in seen_artifact_paths:
            raise ReleaseError(f"content bundle paths must be unique: {bundle_id}")
        digest = raw.get("sha256")
        file_count = raw.get("file_count")
        total_bytes = raw.get("total_bytes")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ReleaseError(f"content bundle digest is invalid: {bundle_id}")
        if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count <= 0:
            raise ReleaseError(f"content bundle file count is invalid: {bundle_id}")
        if not isinstance(total_bytes, int) or isinstance(total_bytes, bool) or total_bytes <= 0:
            raise ReleaseError(f"content bundle byte count is invalid: {bundle_id}")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ReleaseError(f"content bundle evidence is required: {bundle_id}")
        evidence_paths = [_safe_bundle_path(item, field="evidence") for item in evidence]
        if len({path.as_posix() for path in evidence_paths}) != len(evidence_paths):
            raise ReleaseError(f"content bundle evidence contains duplicates: {bundle_id}")

        seen_ids.add(bundle_id)
        seen_stage_paths.add(stage_path.as_posix())
        seen_artifact_paths.add(artifact_path.as_posix())
        validated.append(dict(raw))
    return validated


def _content_bundle_secret_findings(project_dir: Path, source_root: Path) -> list[dict[str, str]]:
    source_prefix = PurePosixPath(source_root.relative_to(project_dir).as_posix()).as_posix()
    records = (
        (path, (PurePosixPath(source_prefix) / relative).as_posix())
        for path, relative in iter_content_bundle_files(source_root)
    )
    return _scan_secret_records(
        project_dir,
        records,
        allowlist_path_in_scope=lambda path: path == source_prefix
        or path.startswith(f"{source_prefix}/"),
    )


def verify_content_bundles(project_dir: Path) -> list[dict[str, Any]]:
    project_dir = project_dir.resolve()
    bundles = load_content_bundle_policy(project_dir)
    for bundle in bundles:
        source_root = project_dir / bundle["source_path"]
        if not source_root.is_dir() or source_root.is_symlink():
            raise ReleaseError(f"content bundle source is unavailable: {bundle['id']}")
        actual = digest_content_bundle_source(source_root).as_dict()
        expected = {
            "sha256": bundle["sha256"],
            "file_count": bundle["file_count"],
            "total_bytes": bundle["total_bytes"],
        }
        if actual != expected:
            raise ReleaseError(
                f"content bundle digest mismatch: {bundle['id']} expected={expected} actual={actual}"
            )
        for relative in bundle["evidence"]:
            evidence_path = source_root / Path(*PurePosixPath(relative).parts)
            if not evidence_path.is_file() or evidence_path.is_symlink():
                raise ReleaseError(f"content bundle evidence is unavailable: {bundle['id']}:{relative}")
        findings = _content_bundle_secret_findings(project_dir, source_root)
        if findings:
            raise ReleaseError(f"content bundle secret scan failed: {bundle['id']}:{findings}")
    return bundles


def stage_content_bundles(project_dir: Path, staged_project: Path) -> list[dict[str, Any]]:
    bundles = verify_content_bundles(project_dir)
    for bundle in bundles:
        source_root = project_dir / bundle["source_path"]
        destination = staged_project / bundle["stage_path"]
        if destination.exists() or destination.is_symlink():
            raise ReleaseError(f"content bundle stage destination already exists: {bundle['id']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(parents=True)
        for source, relative in iter_content_bundle_files(source_root):
            target = destination / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        actual = digest_content_bundle_artifact(destination).as_dict()
        expected = {
            "sha256": bundle["sha256"],
            "file_count": bundle["file_count"],
            "total_bytes": bundle["total_bytes"],
        }
        if actual != expected:
            raise ReleaseError(f"staged content bundle changed while copying: {bundle['id']}")
    return bundles


def verify_staged_content_bundles(
    staged_project: Path,
    frontend_dist: Path,
    expected_bundles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actual_bundles = verify_content_bundles(staged_project)
    if actual_bundles != expected_bundles:
        raise ReleaseError("staged content bundle attestations changed during release build")
    for bundle in actual_bundles:
        artifact_root = frontend_dist / bundle["artifact_path"]
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            raise ReleaseError(f"frontend content bundle artifact is unavailable: {bundle['id']}")
        actual = digest_content_bundle_artifact(artifact_root).as_dict()
        expected = {
            "sha256": bundle["sha256"],
            "file_count": bundle["file_count"],
            "total_bytes": bundle["total_bytes"],
        }
        if actual != expected:
            raise ReleaseError(f"frontend content bundle artifact mismatch: {bundle['id']}")
    return actual_bundles


def verify_release_content_bundles(
    release_dir: Path,
    source_bundle: Path,
    records: Any,
    *,
    required: bool,
) -> None:
    if records is None:
        if required:
            raise ReleaseError("release content bundle attestations are missing")
        return
    if not isinstance(records, list) or not records:
        raise ReleaseError("release content bundle attestations are invalid")
    policy = load_content_bundle_policy(source_bundle)
    if records != policy:
        raise ReleaseError("release content bundle attestations differ from archived policy")
    for bundle in policy:
        artifact_root = release_dir / "frontend-dist" / bundle["artifact_path"]
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            raise ReleaseError(f"release content bundle artifact is unavailable: {bundle['id']}")
        actual = digest_content_bundle_artifact(artifact_root).as_dict()
        expected = {
            "sha256": bundle["sha256"],
            "file_count": bundle["file_count"],
            "total_bytes": bundle["total_bytes"],
        }
        if actual != expected:
            raise ReleaseError(f"release content bundle artifact mismatch: {bundle['id']}")


def verify_release_frontend_budget(
    release_dir: Path,
    source_bundle: Path,
    entries: Mapping[str, str],
    record: Any,
    *,
    required: bool,
) -> None:
    if record is None:
        if required:
            raise ReleaseError("release frontend budget evidence is missing")
        return
    expected_fields = {"status", "artifact_path", "sha256", "config_sha256"}
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise ReleaseError("release frontend budget record is invalid")
    if record.get("status") != "passed":
        raise ReleaseError("release frontend budget did not pass")
    artifact_path = record.get("artifact_path")
    if not isinstance(artifact_path, str) or artifact_path not in entries:
        raise ReleaseError("release frontend budget artifact is absent")
    report_path = release_dir / artifact_path
    if sha256_file(report_path) != record.get("sha256"):
        raise ReleaseError("release frontend budget artifact digest mismatch")
    report = load_json(report_path)
    if (
        set(report) != {"schema_version", "status", "config_sha256", "surfaces", "failures"}
        or report.get("schema_version") != 1
        or report.get("status") != "passed"
        or report.get("failures") != []
        or set(report.get("surfaces") or {}) != {"main", "financial"}
    ):
        raise ReleaseError("release frontend budget report is invalid")
    config_path = source_bundle / "quality/frontend-budgets.json"
    if not config_path.is_file():
        raise ReleaseError("archived frontend budget config is missing")
    config_digest = sha256_file(config_path)
    if report.get("config_sha256") != config_digest or record.get("config_sha256") != config_digest:
        raise ReleaseError("release frontend budget config digest mismatch")
    for name, surface in report["surfaces"].items():
        if (
            not isinstance(surface, dict)
            or set(surface) != {"metrics", "limits", "failures"}
            or surface.get("failures") != []
            or not isinstance(surface.get("metrics"), dict)
            or not isinstance(surface.get("limits"), dict)
        ):
            raise ReleaseError(f"release frontend budget surface is invalid: {name}")


def command_version(command: str, *arguments: str) -> str:
    try:
        result = subprocess.run(
            [command, *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    return result.stdout.strip().splitlines()[0]


def tool_versions() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "node": command_version("node", "--version"),
        "npm": command_version("npm", "--version"),
        "git": command_version("git", "--version"),
    }


def parse_checksums(release_dir: Path) -> dict[str, str]:
    checksum_file = release_dir / "SHA256SUMS"
    try:
        lines = checksum_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleaseError("cannot read SHA256SUMS") from exc
    entries: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        if len(line) < 67 or line[64:66] != "  ":
            raise ReleaseError(f"invalid SHA256SUMS line {line_number}")
        digest, relative = line[:64], line[66:]
        path = PurePosixPath(relative)
        if not SHA256_RE.fullmatch(digest):
            raise ReleaseError(f"invalid checksum on line {line_number}")
        if not relative or path.is_absolute() or ".." in path.parts or relative in entries:
            raise ReleaseError(
                f"unsafe or duplicate artifact path on line {line_number}: {relative!r}"
            )
        entries[relative] = digest
    return entries


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"] or "")
        rel = set((values.get("rel") or "").split())
        if tag == "link" and rel.intersection({"stylesheet", "icon", "modulepreload", "preload"}):
            if values.get("href"):
                self.assets.append(values["href"] or "")


def verify_frontend_assets(frontend_root: Path) -> None:
    for index_name, required in (("index.html", True), ("fin-terminal/index.html", True)):
        index = frontend_root / index_name
        if not index.is_file():
            if required:
                raise ReleaseError(f"missing frontend entry: frontend-dist/{index_name}")
            continue
        parser = _AssetParser()
        parser.feed(index.read_text(encoding="utf-8"))
        for raw in parser.assets:
            parsed = urlsplit(raw)
            if parsed.scheme or parsed.netloc or raw.startswith(("data:", "//")):
                raise ReleaseError(
                    f"external frontend asset reference is forbidden in {index_name}: {raw}"
                )
            candidate = (
                frontend_root / parsed.path.lstrip("/")
                if parsed.path.startswith("/")
                else index.parent / parsed.path
            )
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(frontend_root.resolve())
            except (FileNotFoundError, ValueError) as exc:
                raise ReleaseError(
                    f"invalid frontend asset reference in {index_name}: {raw}"
                ) from exc
            if not resolved.is_file():
                raise ReleaseError(f"frontend asset is not a file in {index_name}: {raw}")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"JSON root must be an object: {path}")
    return value


def _quality_object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseError(f"quality gate {field} must be an object")
    return value


def _quality_counter(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseError(f"quality gate {field} must be a non-negative integer")
    return value


def _verify_quality_source(
    quality: Mapping[str, Any], expected_source_snapshot: Mapping[str, Any] | None
) -> None:
    if quality.get("source_unchanged") is not True:
        raise ReleaseError("quality gate did not prove source stability")
    snapshot = _quality_object(quality.get("source_snapshot"), "source_snapshot")
    digest = snapshot.get("sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ReleaseError("quality gate source_snapshot.sha256 is invalid")
    _quality_counter(snapshot.get("file_count"), "source_snapshot.file_count")
    _quality_counter(snapshot.get("total_bytes"), "source_snapshot.total_bytes")
    if expected_source_snapshot is not None and snapshot != expected_source_snapshot:
        raise ReleaseError("quality gate was run against a different source snapshot")


def _verify_complete_quality_gate(
    quality: Mapping[str, Any], required_steps: frozenset[str]
) -> None:
    schema_version = quality.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ReleaseError("quality gate schema_version must be 1")
    if quality.get("status") != "passed":
        raise ReleaseError("production releases require a passed quality gate")

    scope = _quality_object(quality.get("scope"), "scope")
    if scope.get("python_tests_skipped") is not False:
        raise ReleaseError("production quality gate cannot skip Python tests")
    if scope.get("frontend_skipped") is not False:
        raise ReleaseError("production quality gate cannot skip frontend checks")

    tests = _quality_object(quality.get("tests"), "tests")
    if tests.get("status") != "passed":
        raise ReleaseError("quality gate tests did not pass")
    for field in ("failures", "errors"):
        if _quality_counter(tests.get(field), f"tests.{field}") != 0:
            raise ReleaseError(f"quality gate tests.{field} must be zero")

    ratchets = _quality_object(quality.get("ratchets"), "ratchets")
    if ratchets.get("status") != "passed":
        raise ReleaseError("quality gate frontend ratchets did not pass")
    vue = _quality_object(ratchets.get("vue_eslint"), "ratchets.vue_eslint")
    if vue.get("status") != "passed":
        raise ReleaseError("quality gate Vue ESLint ratchet did not pass")
    vue_actual = _quality_object(vue.get("actual"), "ratchets.vue_eslint.actual")
    vue_maximum = _quality_object(vue.get("maximum"), "ratchets.vue_eslint.maximum")
    for field in ("errors", "warnings", "fatal_errors"):
        actual = _quality_counter(
            vue_actual.get(field), f"ratchets.vue_eslint.actual.{field}"
        )
        maximum = _quality_counter(
            vue_maximum.get(field), f"ratchets.vue_eslint.maximum.{field}"
        )
        if actual > maximum:
            raise ReleaseError(f"quality gate Vue ESLint {field} exceeds its maximum")

    typescript = _quality_object(
        ratchets.get("financial_typescript"), "ratchets.financial_typescript"
    )
    if typescript.get("status") != "passed":
        raise ReleaseError("quality gate financial TypeScript ratchet did not pass")
    actual_errors = _quality_counter(
        typescript.get("actual_errors"), "ratchets.financial_typescript.actual_errors"
    )
    maximum_errors = _quality_counter(
        typescript.get("maximum_errors"), "ratchets.financial_typescript.maximum_errors"
    )
    if actual_errors > maximum_errors:
        raise ReleaseError("quality gate financial TypeScript errors exceed their maximum")

    steps = quality.get("steps")
    if not isinstance(steps, list):
        raise ReleaseError("quality gate steps must be an array")
    step_counts = {name: 0 for name in required_steps}
    for index, step_value in enumerate(steps):
        step = _quality_object(step_value, f"steps[{index}]")
        name = step.get("name")
        if not isinstance(name, str) or not name:
            raise ReleaseError(f"quality gate steps[{index}].name is invalid")
        exit_code = _quality_counter(step.get("exit_code"), f"steps[{index}].exit_code")
        if step.get("status") != "passed" or exit_code != 0:
            raise ReleaseError(f"quality gate step did not pass: {name}")
        if name in step_counts:
            step_counts[name] += 1
    invalid_counts = sorted(name for name, count in step_counts.items() if count != 1)
    if invalid_counts:
        raise ReleaseError(
            "quality gate required steps must appear exactly once: "
            f"{invalid_counts}"
        )


def verify_quality_gate(
    quality: Mapping[str, Any],
    *,
    production: bool,
    allow_unverified: bool = False,
    expected_source_snapshot: Mapping[str, Any] | None = None,
    historical_release_version: str | None = None,
) -> None:
    """Verify quality evidence using creation-strict or explicit historical policy."""

    if not production:
        if quality.get("status") != "passed":
            if allow_unverified:
                return
            raise ReleaseError("quality gate metadata must have status=passed")
        if expected_source_snapshot is not None:
            _verify_quality_source(quality, expected_source_snapshot)
        return

    required_steps = HISTORICAL_PRODUCTION_QUALITY_STEPS.get(
        historical_release_version or "", PRODUCTION_QUALITY_STEPS
    )
    _verify_complete_quality_gate(quality, required_steps)
    _verify_quality_source(quality, expected_source_snapshot)


def _verify_artifact_files(release_dir: Path) -> tuple[dict[str, str], int]:
    entries = parse_checksums(release_dir)
    actual_files = {
        path.relative_to(release_dir).as_posix(): path for path in _artifact_files(release_dir)
    }
    if set(entries) != set(actual_files):
        missing = sorted(set(entries) - set(actual_files))
        extra = sorted(set(actual_files) - set(entries))
        raise ReleaseError(f"artifact manifest file set mismatch: missing={missing} extra={extra}")
    total_bytes = 0
    for relative, expected_digest in entries.items():
        path = actual_files[relative]
        if sha256_file(path) != expected_digest:
            raise ReleaseError(f"artifact checksum mismatch: {relative}")
        total_bytes += path.stat().st_size
    return entries, total_bytes


def _verify_immutable_release(release_dir: Path) -> None:
    for path in (release_dir, *release_dir.rglob("*")):
        if path.stat().st_mode & 0o222:
            relative = "." if path == release_dir else path.relative_to(release_dir)
            raise ReleaseError(f"release path is writable: {relative}")


def _verify_legacy_v1_release(
    release_dir: Path,
    manifest: Mapping[str, Any],
    *,
    expected_version: str | None,
    expected_build_id: str | None,
    expected_git_sha: str | None,
    production: bool,
) -> dict:
    if (
        manifest.get("backend_entry") != "backend/serve_prod.py"
        or manifest.get("frontend_dist") != "frontend-dist"
    ):
        raise ReleaseError("legacy release entry paths are invalid")
    build_id = manifest.get("build_id")
    git_sha = manifest.get("git_sha")
    if not isinstance(build_id, str) or not BUILD_ID_RE.fullmatch(build_id):
        raise ReleaseError("legacy release build_id is invalid")
    if not isinstance(git_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", git_sha):
        raise ReleaseError("legacy release git_sha is invalid")
    if production and release_dir.name != build_id:
        raise ReleaseError("production release directory must equal its build_id")
    version = (release_dir / "VERSION").read_text(encoding="ascii").strip()
    if manifest.get("version") != version or not VERSION_RE.fullmatch(version):
        raise ReleaseError("legacy release VERSION and release.json do not match")
    expected = {
        "version": expected_version,
        "build_id": expected_build_id,
        "git_sha": expected_git_sha,
    }
    for key, value in expected.items():
        if value is not None and str(manifest.get(key) or "") != value:
            raise ReleaseError(
                f"release {key} mismatch: expected {value!r}, got {manifest.get(key)!r}"
            )
    entries, _total_bytes = _verify_artifact_files(release_dir)
    checksum_digest = sha256_file(release_dir / "SHA256SUMS")
    if manifest.get("artifact_manifest_sha256") != checksum_digest:
        raise ReleaseError("legacy artifact manifest digest does not match release.json")
    missing_runtime = [name for name in REQUIRED_RUNTIME_FILES if name not in entries]
    if missing_runtime:
        raise ReleaseError(f"legacy release runtime closure is incomplete: {missing_runtime}")
    verify_frontend_assets(release_dir / "frontend-dist")
    findings = scan_secrets(release_dir)
    if findings:
        raise ReleaseError(f"secret scan failed: {findings}")
    _verify_immutable_release(release_dir)
    return dict(manifest)


def _record_for_path(records: Any, path: str, label: str) -> Mapping[str, Any]:
    if not isinstance(records, list):
        raise ReleaseError(f"release {label} records are invalid")
    matches = [item for item in records if isinstance(item, dict) and item.get("path") == path]
    if len(matches) != 1:
        raise ReleaseError(f"release {label} record is missing or duplicated: {path}")
    return matches[0]


def _verify_release_python_runtime(
    release_dir: Path,
    manifest: Mapping[str, Any],
    entries: Mapping[str, str],
    *,
    production: bool,
) -> None:
    attestation = manifest.get("python_runtime")
    if not isinstance(attestation, dict) or attestation.get("role") != PYTHON_RUNTIME_ROLE:
        raise ReleaseError("release Python runtime attestation is missing")
    version = attestation.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ReleaseError("release Python runtime version is invalid")
    if version != manifest.get("version"):
        raise ReleaseError("release Python runtime version differs from the application version")

    input_record = _record_for_path(
        manifest.get("dependency_manifests"), PYTHON_ROLE_INPUT, "dependency manifest"
    )
    lock_record = _record_for_path(manifest.get("dependency_locks"), PYTHON_ROLE_LOCK, "lock")
    role_input = attestation.get("role_input")
    lock = attestation.get("lock")
    if not isinstance(role_input, dict) or not isinstance(lock, dict):
        raise ReleaseError("release Python role input or lock attestation is missing")
    if role_input.get("path") != PYTHON_ROLE_INPUT or role_input.get("sha256") != input_record.get(
        "sha256"
    ):
        raise ReleaseError("release Python role input attestation is inconsistent")
    if lock.get("path") != PYTHON_ROLE_LOCK or lock.get("sha256") != lock_record.get("sha256"):
        raise ReleaseError("release Python lock attestation is inconsistent")
    lock_sha = _require_sha256(lock.get("sha256"), "release Python lock digest")
    lock_artifact_path = lock_record.get("artifact_path")
    input_artifact_path = input_record.get("artifact_path")
    if not isinstance(lock_artifact_path, str) or not isinstance(input_artifact_path, str):
        raise ReleaseError("release Python role artifact paths are invalid")
    _verify_hashed_python_lock(release_dir / lock_artifact_path)
    source_bundle_path = (manifest.get("source") or {}).get("bundle_path")
    if not isinstance(source_bundle_path, str):
        raise ReleaseError("release source bundle path is missing for Python role verification")
    source_bundle_relative = PurePosixPath(source_bundle_path)
    if source_bundle_relative.is_absolute() or ".." in source_bundle_relative.parts:
        raise ReleaseError("release source bundle path is unsafe")
    source_bundle = release_dir / Path(*source_bundle_relative.parts)
    source_role_input = source_bundle / PYTHON_ROLE_INPUT
    source_role_lock = source_bundle / PYTHON_ROLE_LOCK
    if (
        not source_role_input.is_file()
        or not source_role_lock.is_file()
        or sha256_file(source_role_input) != role_input.get("sha256")
        or sha256_file(source_role_lock) != lock_sha
    ):
        raise ReleaseError("archived source does not contain the attested Python role inputs")

    runtime_manifest_record = attestation.get("runtime_manifest")
    if not isinstance(runtime_manifest_record, dict):
        raise ReleaseError("release Python runtime manifest record is missing")
    runtime_artifact = runtime_manifest_record.get("artifact_path")
    if runtime_artifact != f"{PYTHON_RUNTIME_ARCHIVE_ROOT}/{PYTHON_RUNTIME_MANIFEST_NAME}":
        raise ReleaseError("release Python runtime manifest artifact path is invalid")
    if runtime_artifact not in entries:
        raise ReleaseError("release Python runtime manifest is absent from artifact checksums")
    archived_manifest = release_dir / runtime_artifact
    if sha256_file(archived_manifest) != runtime_manifest_record.get("sha256"):
        raise ReleaseError("release Python runtime manifest digest mismatch")
    runtime_payload = load_json(archived_manifest)
    if runtime_manifest_record.get("schema_version") != runtime_payload.get("schema_version"):
        raise ReleaseError("release Python runtime manifest schema attestation is inconsistent")
    _validate_runtime_manifest_payload(runtime_payload, lock_sha256=lock_sha, production=production)
    if runtime_payload.get("version") != version:
        raise ReleaseError("release and runtime manifest versions differ")
    if runtime_payload.get("runtime_fingerprint") != attestation.get("runtime_fingerprint"):
        raise ReleaseError("release Python runtime fingerprint is inconsistent")
    if runtime_payload.get("build_input_fingerprint") != attestation.get("build_input_fingerprint"):
        raise ReleaseError("release Python build input fingerprint is inconsistent")

    evidence_records = attestation.get("evidence")
    if not isinstance(evidence_records, dict) or set(evidence_records) != set(
        PYTHON_RUNTIME_EVIDENCE_FILES
    ):
        raise ReleaseError("release Python runtime evidence set is incomplete")
    evidence_paths: dict[str, Path] = {}
    for name, filename in PYTHON_RUNTIME_EVIDENCE_FILES.items():
        record = evidence_records.get(name)
        expected_path = f"{PYTHON_RUNTIME_ARCHIVE_ROOT}/{filename}"
        if not isinstance(record, dict) or record.get("artifact_path") != expected_path:
            raise ReleaseError(f"release Python runtime evidence path is invalid: {name}")
        if expected_path not in entries:
            raise ReleaseError(f"release Python runtime evidence is absent: {name}")
        path = release_dir / expected_path
        if sha256_file(path) != record.get("sha256") or record.get("status") != "passed":
            raise ReleaseError(f"release Python runtime evidence is invalid: {name}")
        evidence_paths[name] = path
    validated_records = _validate_runtime_evidence(runtime_payload, evidence_paths)
    for name, record in validated_records.items():
        if record["sha256"] != evidence_records[name].get("sha256"):
            raise ReleaseError(f"release Python evidence metadata differs: {name}")

    python = attestation.get("python")
    runtime_python = runtime_payload.get("python")
    if not isinstance(python, dict) or not isinstance(runtime_python, dict):
        raise ReleaseError("release Python ABI attestation is missing")
    for key in ("version", "implementation", "platform", "machine"):
        if python.get(key) != runtime_python.get(key):
            raise ReleaseError(f"release Python {key} attestation differs from runtime manifest")
    if runtime_python.get("soabi") not in (None, python.get("soabi")):
        raise ReleaseError("release Python ABI differs from runtime manifest")
    for key in ("soabi", "executable_sha256", "pip_freeze_sha256"):
        value = python.get(key)
        if key.endswith("sha256"):
            _require_sha256(value, f"release Python {key}")
        elif not isinstance(value, str) or not value:
            raise ReleaseError("release Python SOABI attestation is invalid")
    if python.get("pip_freeze_sha256") != runtime_payload.get("pip_freeze_sha256"):
        raise ReleaseError("release Python distribution fingerprint is inconsistent")
    if attestation.get("validation") != runtime_payload.get("validation"):
        raise ReleaseError("release Python validation summary is inconsistent")


def verify_external_python_runtime(
    release_dir: Path,
    runtime_dir: Path,
    *,
    runtime_manifest_path: Path | None = None,
    allowed_runtime_root: Path,
    production: bool = True,
    expected_version: str | None = None,
    expected_build_id: str | None = None,
    expected_git_sha: str | None = None,
) -> dict:
    release_dir = release_dir.resolve(strict=True)
    manifest = verify_release(
        release_dir,
        production=production,
        expected_version=expected_version,
        expected_build_id=expected_build_id,
        expected_git_sha=expected_git_sha,
    )
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseError("external Python runtimes are only valid for schema v3 releases")
    attestation = manifest["python_runtime"]
    runtime_dir = _validate_versioned_runtime_path(runtime_dir, allowed_runtime_root)
    runtime_manifest_path = runtime_manifest_path or (
        runtime_dir / "inventory" / PYTHON_RUNTIME_MANIFEST_NAME
    )
    runtime_payload, evidence = _runtime_inventory_files(runtime_dir, runtime_manifest_path)
    archived_manifest_record = attestation["runtime_manifest"]
    if sha256_file(runtime_manifest_path) != archived_manifest_record.get("sha256"):
        raise ReleaseError("selected Python runtime manifest differs from the release")
    lock_sha = _require_sha256(attestation["lock"].get("sha256"), "release Python lock digest")
    _validate_runtime_manifest_payload(runtime_payload, lock_sha256=lock_sha, production=production)
    if runtime_payload.get("runtime_fingerprint") != attestation.get("runtime_fingerprint"):
        raise ReleaseError("selected Python runtime fingerprint differs from the release")
    if Path(str(runtime_payload.get("install_prefix") or "")).resolve() != runtime_dir:
        raise ReleaseError("selected Python runtime install_prefix is invalid")
    if runtime_payload.get("version") != runtime_dir.name:
        raise ReleaseError("selected Python runtime version directory is invalid")
    evidence_records = _validate_runtime_evidence(runtime_payload, evidence)
    for name, record in evidence_records.items():
        if record["sha256"] != attestation["evidence"][name].get("sha256"):
            raise ReleaseError(f"selected Python runtime evidence differs: {name}")
    probe = probe_python_runtime(runtime_dir)
    for key in (
        "version",
        "implementation",
        "platform",
        "machine",
        "soabi",
        "executable_sha256",
        "pip_freeze_sha256",
    ):
        if probe.get(key) != attestation["python"].get(key):
            raise ReleaseError(f"selected Python runtime {key} differs from the release")
    for path in (
        runtime_dir,
        runtime_manifest_path,
        *evidence.values(),
        runtime_dir / "bin/python",
    ):
        if path.stat().st_mode & 0o022:
            raise ReleaseError("selected Python runtime contains writable attestation paths")
    return manifest


def verify_release(
    release_dir: Path,
    *,
    expected_version: str | None = None,
    expected_build_id: str | None = None,
    expected_git_sha: str | None = None,
    production: bool = False,
    allow_legacy: bool = False,
) -> dict:
    release_dir = release_dir.resolve()
    manifest = load_json(release_dir / "release.json")
    schema = manifest.get("schema_version")
    if isinstance(schema, bool):
        raise ReleaseError(f"unsupported release schema: {schema!r}")
    if schema == 1:
        if not allow_legacy:
            raise ReleaseError("schema v1 release requires explicit legacy rollback authorization")
        return _verify_legacy_v1_release(
            release_dir,
            manifest,
            expected_version=expected_version,
            expected_build_id=expected_build_id,
            expected_git_sha=expected_git_sha,
            production=production,
        )
    if schema == 2 and not allow_legacy:
        raise ReleaseError("schema v2 release requires explicit legacy rollback authorization")
    if schema not in ({SCHEMA_VERSION} | set(LEGACY_SCHEMA_VERSIONS)):
        raise ReleaseError(f"unsupported release schema: {schema!r}")
    if manifest.get("backend_entry") != "backend/serve_prod.py":
        raise ReleaseError("release backend entry is invalid")
    if manifest.get("frontend_dist") != "frontend-dist":
        raise ReleaseError("release frontend directory is invalid")
    build_id = manifest.get("build_id")
    git_sha = manifest.get("git_sha")
    if not isinstance(build_id, str) or not BUILD_ID_RE.fullmatch(build_id):
        raise ReleaseError("release build_id is invalid")
    if not isinstance(git_sha, str) or not GIT_SHA_RE.fullmatch(git_sha):
        raise ReleaseError("release git_sha is invalid")
    if production and release_dir.name != build_id:
        raise ReleaseError("production release directory must equal its build_id")
    version = (release_dir / "VERSION").read_text(encoding="ascii").strip()
    if manifest.get("version") != version or not VERSION_RE.fullmatch(version):
        raise ReleaseError("release VERSION and release.json do not match")
    missing_runtime = [
        name for name in required_runtime_files(version) if not (release_dir / name).is_file()
    ]
    if missing_runtime:
        raise ReleaseError(f"release runtime closure is incomplete: {missing_runtime}")
    expected = {
        "version": expected_version,
        "build_id": expected_build_id,
        "git_sha": expected_git_sha,
    }
    for key, value in expected.items():
        if value is not None and str(manifest.get(key) or "") != value:
            raise ReleaseError(
                f"release {key} mismatch: expected {value!r}, got {manifest.get(key)!r}"
            )

    entries, total_bytes = _verify_artifact_files(release_dir)

    artifact = manifest.get("artifact") or {}
    checksum_digest = sha256_file(release_dir / "SHA256SUMS")
    if artifact.get("manifest_sha256") != checksum_digest:
        raise ReleaseError("artifact manifest digest does not match release.json")
    if artifact.get("file_count") != len(entries) or artifact.get("total_bytes") != total_bytes:
        raise ReleaseError("artifact count or byte total does not match release.json")

    locks = manifest.get("dependency_locks") or []
    if production and not locks:
        raise ReleaseError("production releases require dependency lock metadata")
    expected_locks = LOCK_FILES if schema == SCHEMA_VERSION else LEGACY_LOCK_FILES
    if production and {lock.get("path") for lock in locks} != set(expected_locks):
        raise ReleaseError("production release dependency lock set is incomplete")
    for lock in locks:
        artifact_path = lock.get("artifact_path")
        if not isinstance(artifact_path, str) or artifact_path not in entries:
            raise ReleaseError(f"lock file is absent from artifact manifest: {artifact_path!r}")
        if sha256_file(release_dir / artifact_path) != lock.get("sha256"):
            raise ReleaseError(f"lock file digest mismatch: {artifact_path}")
    dependency_manifests = manifest.get("dependency_manifests") or []
    expected_manifests = (
        DEPENDENCY_MANIFEST_FILES if schema == SCHEMA_VERSION else LEGACY_DEPENDENCY_MANIFEST_FILES
    )
    if production and {item.get("path") for item in dependency_manifests} != set(
        expected_manifests
    ):
        raise ReleaseError("production release dependency manifest set is incomplete")
    for item in dependency_manifests:
        artifact_path = item.get("artifact_path")
        if not isinstance(artifact_path, str) or artifact_path not in entries:
            raise ReleaseError(f"dependency manifest is absent from artifact: {artifact_path!r}")
        if sha256_file(release_dir / artifact_path) != item.get("sha256"):
            raise ReleaseError(f"dependency manifest digest mismatch: {artifact_path}")

    if schema == SCHEMA_VERSION:
        _verify_release_python_runtime(
            release_dir,
            manifest,
            entries,
            production=production,
        )

    quality = manifest.get("quality_gate") or {}
    quality_path = quality.get("artifact_path")
    if not isinstance(quality_path, str) or quality_path not in entries:
        raise ReleaseError("quality gate metadata is absent from artifact manifest")
    quality_payload = load_json(release_dir / quality_path)
    if quality_payload.get("status") != quality.get("status"):
        raise ReleaseError("quality gate status does not match copied metadata")
    if sha256_file(release_dir / quality_path) != quality.get("sha256"):
        raise ReleaseError("quality gate metadata digest does not match release.json")
    source_snapshot = (manifest.get("source") or {}).get("snapshot")
    verify_quality_gate(
        quality_payload,
        production=production,
        allow_unverified=not production,
        expected_source_snapshot=source_snapshot,
        historical_release_version=version if schema == SCHEMA_VERSION else None,
    )
    dependency_mode = ((manifest.get("build") or {}).get("frontend") or {}).get("dependency_mode")
    if production and dependency_mode != "ci":
        raise ReleaseError("production releases require frontend dependency_mode=ci")
    if (manifest.get("build") or {}).get("source_unchanged") is not True:
        raise ReleaseError("release build did not prove that source inputs stayed unchanged")
    if (manifest.get("build") or {}).get("staged_source_unchanged") is not True:
        raise ReleaseError("release build did not prove that staged inputs stayed unchanged")
    source = manifest.get("source") or {}
    if not isinstance(manifest.get("source_dirty"), bool):
        raise ReleaseError("release source_dirty must be a boolean")
    if source.get("dirty") is not manifest.get("source_dirty"):
        raise ReleaseError("release source dirty metadata is inconsistent")
    provenance = source.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("dirty") is not manifest.get(
        "source_dirty"
    ):
        raise ReleaseError("release git provenance is missing or inconsistent")
    if provenance.get("head") != git_sha:
        raise ReleaseError("release git provenance HEAD does not match git_sha")
    if source.get("dirty_override") is True and manifest.get("source_dirty") is not True:
        raise ReleaseError("clean release cannot contain a dirty source override")
    if (
        production
        and manifest.get("source_dirty") is True
        and source.get("dirty_override") is not True
    ):
        raise ReleaseError("dirty production release lacks an explicit override attestation")
    source_bundle_path = source.get("bundle_path")
    if not isinstance(source_bundle_path, str) or not source_bundle_path:
        raise ReleaseError("release source bundle path is missing")
    bundle_relative = PurePosixPath(source_bundle_path)
    if bundle_relative.is_absolute() or ".." in bundle_relative.parts:
        raise ReleaseError("release source bundle path is unsafe")
    source_bundle = release_dir / Path(*bundle_relative.parts)
    if not source_bundle.is_dir():
        raise ReleaseError("release source bundle is missing")
    missing_bundle_runtime = [
        name for name in required_runtime_files(version) if not (source_bundle / name).is_file()
    ]
    if missing_bundle_runtime:
        raise ReleaseError(
            f"archived source runtime closure is incomplete: {missing_bundle_runtime}"
        )
    bundle_snapshot = digest_inputs(source_bundle, legacy_exclusions=version.startswith("0."))
    if bundle_snapshot.as_dict() != source_snapshot:
        raise ReleaseError("archived source bundle does not match the attested source snapshot")
    if source.get("bundle_snapshot") != source_snapshot:
        raise ReleaseError("source bundle metadata does not match the source snapshot")

    release_major = int(version.split(".", 1)[0])
    if release_major >= 1:
        verify_runtime_catalog_artifact_copies(release_dir, source_bundle)
    verify_release_content_bundles(
        release_dir,
        source_bundle,
        manifest.get("content_bundles"),
        required=release_major >= 1,
    )
    verify_release_frontend_budget(
        release_dir,
        source_bundle,
        entries,
        (manifest.get("build") or {}).get("frontend_budget"),
        required=release_major >= 1,
    )

    verify_frontend_assets(release_dir / "frontend-dist")
    findings = scan_secrets(release_dir)
    if findings:
        raise ReleaseError(f"secret scan failed: {findings}")

    _verify_immutable_release(release_dir)
    return manifest
