#!/usr/bin/env python3
"""Check repository data, generated-artifact, and path-migration contracts.

This check intentionally inspects Git's tracked view.  Local runtime data is
allowed to exist on a developer machine, but it must not enter the source
tree's versioned asset set.  The check is read-only and does not inspect or
touch deployed releases.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_MANIFEST = ROOT / "quality" / "data-assets-manifest.json"
PATH_POLICY = ROOT / "quality" / "runtime-path-policy.json"
SCRIPTS_MANIFEST = ROOT / "scripts" / "manifest.json"
DOC_LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)\s]+)(?:\s+[^)]*)?\)")
WORKSPACE_ABSOLUTE_RE = re.compile(
    r"(?<![\w])/(?:root|home|opt|var|tmp|Users|Volumes)(?:/[A-Za-z0-9_.-]+)+"
)
CURRENT_DOC_GLOBS = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/README.md",
    "docs/REPOSITORY_GOVERNANCE.md",
    "docs/word/README.md",
    "docs/architecture/*.md",
    ".github/GOVERNANCE.md",
    "config/README.md",
    "config/runtime/README.md",
    "logs/README.md",
    "ops/README.md",
    "quality/README.md",
    "requirements/README.md",
    "scripts/README.md",
    "deploy/README.md",
    "remotion-edit/README.md",
)


class HygieneError(RuntimeError):
    """Raised when a repository governance manifest is invalid."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HygieneError(f"cannot read JSON manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HygieneError(f"manifest must contain an object: {path}")
    return value


def tracked_paths(project: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def check_data_assets(project: Path, paths: list[str]) -> list[str]:
    manifest = _load_json(project / DATA_MANIFEST.relative_to(ROOT))
    policy = manifest.get("tracked_path_policy")
    if not isinstance(policy, dict):
        raise HygieneError("data manifest tracked_path_policy is missing")
    allowed = policy.get("allowed_globs")
    forbidden = policy.get("forbidden_globs")
    if not isinstance(allowed, list) or not isinstance(forbidden, list):
        raise HygieneError("data manifest glob policies must be lists")
    allowed_patterns = [item.get("pattern") for item in allowed if isinstance(item, dict)]
    forbidden_patterns = [item for item in forbidden if isinstance(item, str)]
    issues: list[str] = []
    for path in paths:
        if not path.startswith("data/"):
            continue
        if _matches(path, forbidden_patterns):
            issues.append(f"tracked generated/runtime data is forbidden: {path}")
        elif not _matches(path, allowed_patterns):
            issues.append(f"tracked data path has no manifest classification: {path}")

    large_policy = manifest.get("large_file_policy")
    large_files = manifest.get("large_files")
    if not isinstance(large_policy, dict) or not isinstance(large_files, list):
        raise HygieneError("data manifest large-file policy is incomplete")
    threshold = large_policy.get("threshold_bytes")
    if not isinstance(threshold, int) or threshold <= 0:
        raise HygieneError("data manifest threshold_bytes must be positive")
    declared: dict[str, dict[str, Any]] = {}
    for entry in large_files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise HygieneError("every large_files entry needs a path")
        path = str(entry["path"])
        if path in declared:
            raise HygieneError(f"duplicate large-file manifest entry: {path}")
        declared[path] = entry
        if not isinstance(entry.get("max_bytes"), int) or entry["max_bytes"] < threshold:
            raise HygieneError(f"large-file entry has invalid max_bytes: {path}")

    for path in paths:
        if not _matches(path, list(large_policy.get("scope_globs", []))):
            continue
        full_path = project / path
        try:
            size = full_path.stat().st_size
        except OSError as exc:
            issues.append(f"cannot stat tracked data asset {path}: {exc}")
            continue
        if size >= threshold:
            entry = declared.get(path)
            if entry is None:
                issues.append(f"large tracked data asset is not declared: {path} ({size} bytes)")
            elif size > entry["max_bytes"]:
                issues.append(
                    f"large tracked data asset exceeds declared ceiling: {path} "
                    f"({size} > {entry['max_bytes']} bytes)"
                )
    return issues


def check_scripts_manifest(project: Path, paths: list[str]) -> list[str]:
    manifest = _load_json(project / SCRIPTS_MANIFEST.relative_to(ROOT))
    categories = manifest.get("categories")
    if not isinstance(categories, list) or not categories:
        raise HygieneError("scripts manifest must contain categories")
    patterns: list[tuple[str, str]] = []
    ids: set[str] = set()
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("id"), str):
            raise HygieneError("every scripts category needs an id")
        if category["id"] in ids:
            raise HygieneError(f"duplicate scripts category: {category['id']}")
        ids.add(category["id"])
        globs = category.get("globs")
        if (
            not isinstance(globs, list)
            or not globs
            or not all(isinstance(item, str) for item in globs)
        ):
            raise HygieneError(f"scripts category has invalid globs: {category['id']}")
        patterns.extend((category["id"], pattern) for pattern in globs)
    issues: list[str] = []
    script_paths = [
        path
        for path in paths
        if path.startswith("scripts/") and path.endswith((".py", ".sh"))
    ]
    for path in script_paths:
        matches = [category_id for category_id, pattern in patterns if _matches(path, [pattern])]
        if not matches:
            issues.append(f"script is absent from scripts/manifest.json: {path}")
        elif len(matches) != 1:
            issues.append(
                f"script must belong to exactly one manifest category: {path} "
                f"({', '.join(matches)})"
            )
    return issues


def _is_current_document(path: str) -> bool:
    if "/archive/" in f"/{path}" or path.startswith("docs/archive/"):
        return False
    return _matches(path, list(CURRENT_DOC_GLOBS))


def _changed_document_lines(project: Path) -> dict[str, list[str]]:
    result = subprocess.run(
        ["git", "-C", str(project), "diff", "--unified=0", "--", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    changed: dict[str, list[str]] = {}
    current_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        if current_path and line.startswith("+") and not line.startswith("+++"):
            changed.setdefault(current_path, []).append(line[1:])
    return changed


def check_document_hygiene(project: Path) -> list[str]:
    """Check links and only newly changed current-document policy lines.

    Existing historical/current debt is not batch-rewritten here. The
    incremental checks apply to additions visible in a pull-request diff.
    """
    issues: list[str] = []
    paths = tracked_paths(project)
    for raw_path in paths:
        if not _is_current_document(raw_path):
            continue
        path = project / raw_path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            issues.append(f"cannot read document {raw_path}: {exc}")
            continue
        for match in DOC_LINK_RE.finditer(text):
            target = match.group(1).strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            if target.startswith("/"):
                issues.append(f"document link must be repository-relative: {raw_path}: {target}")
                continue
            target = re.sub(r":\d+$", "", target)
            target_path = (path.parent / target).resolve()
            if not target_path.is_file() and not target_path.is_dir():
                issues.append(f"document link target does not exist: {raw_path}: {target}")

    for raw_path, lines in _changed_document_lines(project).items():
        if not _is_current_document(raw_path):
            continue
        absolute_lines = [line for line in lines if WORKSPACE_ABSOLUTE_RE.search(line)]
        if absolute_lines:
            issues.append(
                f"new current-document lines contain an absolute workspace path: {raw_path}"
            )
        if raw_path.startswith("docs/") and raw_path != "docs/archive/README.md":
            header = (project / raw_path).read_text(encoding="utf-8").splitlines()[:24]
            if not any(
                re.search(r"(?:^|\s)(?:status|状态)\s*[:：]", line, re.IGNORECASE)
                for line in header
            ):
                issues.append(
                    "changed current document needs a Status/状态 metadata line: "
                    f"{raw_path}"
                )
    return issues


def check_github_configuration(project: Path) -> list[str]:
    issues: list[str] = []
    codeowners = project / ".github" / "CODEOWNERS"
    try:
        lines = codeowners.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read .github/CODEOWNERS: {exc}"]
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 2 or (not fields[0].startswith("/") and fields[0] != "*"):
            issues.append(f"invalid CODEOWNERS rule at line {number}")
        if any(not owner.startswith("@") for owner in fields[1:]):
            issues.append(f"CODEOWNERS owners must be @handles or @teams at line {number}")

    dependabot = project / ".github" / "dependabot.yml"
    try:
        dependabot_text = dependabot.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read .github/dependabot.yml: {exc}")
    else:
        required_fields = (
            "version: 2",
            "package-ecosystem:",
            "directory:",
            "schedule:",
            "interval:",
        )
        for required in required_fields:
            if required not in dependabot_text:
                issues.append(f"Dependabot config is missing {required}")
        for directory in ('directory: "/"', "/remotion-edit", "/requirements/roles"):
            if directory not in dependabot_text:
                issues.append(f"Dependabot config is missing {directory}")

    templates = project / ".github" / "ISSUE_TEMPLATE"
    for name in ("bug_report.yml", "feature_request.yml"):
        path = templates / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            issues.append(f"cannot read issue template {name}: {exc}")
            continue
        for required in ("name:", "description:", "body:", "validations:"):
            if required not in text:
                issues.append(f"issue template {name} is missing {required}")
    return issues


def check_runtime_path_policy(project: Path) -> list[str]:
    policy = _load_json(project / PATH_POLICY.relative_to(ROOT))
    issues: list[str] = []
    if policy.get("status") != "compatibility-plan-only":
        issues.append(
            "runtime path policy must remain a compatibility plan until explicitly approved"
        )
    if policy.get("activation") != "not-active":
        issues.append(
            "runtime path policy must not activate a migration in a repository hygiene change"
        )
    for key in ("legacy_paths", "target_paths", "compatibility_contract"):
        if not isinstance(policy.get(key), dict):
            issues.append(f"runtime path policy is missing {key}")
    contract = policy.get("compatibility_contract")
    if (
        isinstance(contract, dict)
        and contract.get("default_behavior_changes_in_this_commit") is not False
    ):
        issues.append("runtime path policy must declare that defaults do not change in this commit")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    args = parser.parse_args()
    project = args.project.resolve()
    paths = tracked_paths(project)
    issues = []
    issues.extend(check_data_assets(project, paths))
    issues.extend(check_scripts_manifest(project, paths))
    issues.extend(check_runtime_path_policy(project))
    issues.extend(check_document_hygiene(project))
    issues.extend(check_github_configuration(project))
    if issues:
        print("repository hygiene check failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("repository hygiene check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
