#!/usr/bin/env python3
"""Reject unexplained tracked files at the repository root."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

ALLOWED_ROOT_FILES = frozenset(
    {
        ".editorconfig",
        ".env.example",
        ".gitattributes",
        ".gitignore",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "LICENSE_DECISION.md",
        "Makefile",
        "README.md",
        "SECURITY.md",
        "VERSION",
        "docker-compose.yml",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "requirements-dev.txt",
        "requirements.txt",
    }
)


def unexpected_root_files(tracked_paths: Iterable[str]) -> list[str]:
    """Return tracked root files that are not part of the public contract."""
    root_files = {
        path
        for raw_path in tracked_paths
        if (path := PurePosixPath(raw_path)).parent == PurePosixPath(".")
    }
    return sorted(str(path) for path in root_files if path.name not in ALLOWED_ROOT_FILES)


def tracked_paths(project: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(project), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (defaults to the current GlobeMind source tree)",
    )
    args = parser.parse_args()

    unexpected = unexpected_root_files(tracked_paths(args.project.resolve()))
    if unexpected:
        print("unexpected tracked files at repository root:")
        for path in unexpected:
            print(f"- {path}")
        print("move each file to its owning module or update the reviewed allowlist")
        return 1

    print("root layout check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
