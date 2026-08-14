#!/usr/bin/env python3
"""Verify a GlobeMind immutable release without starting any service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from release_lib import (  # noqa: E402
    ReleaseError,
    verify_external_python_runtime,
    verify_release,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-build-id")
    parser.add_argument("--expected-git-sha")
    parser.add_argument(
        "--production",
        action="store_true",
        help="require a passed quality gate and npm-ci dependency installation",
    )
    parser.add_argument("--python-runtime-dir", type=Path)
    parser.add_argument("--python-runtime-manifest", type=Path)
    parser.add_argument(
        "--python-runtime-root",
        type=Path,
        default=Path("/root/data/python-runtimes/globemind-web"),
    )
    args = parser.parse_args()
    try:
        if args.python_runtime_manifest and not args.python_runtime_dir:
            raise ReleaseError("--python-runtime-manifest requires --python-runtime-dir")
        if args.python_runtime_dir:
            manifest = verify_external_python_runtime(
                args.release_dir,
                args.python_runtime_dir,
                runtime_manifest_path=args.python_runtime_manifest,
                allowed_runtime_root=args.python_runtime_root,
                expected_version=args.expected_version,
                expected_build_id=args.expected_build_id,
                expected_git_sha=args.expected_git_sha,
                production=args.production,
            )
        else:
            manifest = verify_release(
                args.release_dir,
                expected_version=args.expected_version,
                expected_build_id=args.expected_build_id,
                expected_git_sha=args.expected_git_sha,
                production=args.production,
                allow_legacy=os.environ.get("ALLOW_LEGACY_RELEASE") == "1",
            )
    except (OSError, UnicodeError, ReleaseError) as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "verified",
                "version": manifest["version"],
                "build_id": manifest["build_id"],
                "git_sha": manifest["git_sha"],
                "artifact_manifest_sha256": manifest["artifact"]["manifest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
