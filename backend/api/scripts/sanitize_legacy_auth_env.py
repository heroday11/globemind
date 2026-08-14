#!/usr/bin/env python3
"""Atomically remove retired plaintext authentication variables from an env file."""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


RETIRED_KEYS = frozenset({"ADMIN_PASSWORD", "UNIFY_APP_USER_PASSWORD"})
ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def sanitize(path: Path) -> tuple[str, ...]:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"expected a regular env file: {path}")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError(f"env file must be owner-only before sanitizing: {path} mode={mode:o}")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    removed: list[str] = []
    kept: list[str] = []
    for line in lines:
        match = ENV_ASSIGNMENT.match(line)
        if match and match.group(1) in RETIRED_KEYS:
            removed.append(match.group(1))
            continue
        kept.append(line)
    if not removed:
        return ()

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(kept)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return tuple(sorted(set(removed)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required")
    removed = sanitize(args.env_file)
    print(f"sanitized={args.env_file.resolve()} removed={','.join(removed) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
