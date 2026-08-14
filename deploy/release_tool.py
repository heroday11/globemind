#!/usr/bin/env python3
"""Internal CLI used by create_release.sh.

Operators should use verify_release.py to validate an existing artifact.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

from release_lib import (  # noqa: E402
    SCHEMA_VERSION,
    SOURCE_INPUTS,
    ReleaseError,
    archive_python_runtime_attestation,
    copy_dependency_manifests,
    copy_inputs,
    copy_lock_files,
    copy_release_backend,
    digest_inputs,
    digest_tree,
    is_source_input_path,
    iter_input_files,
    load_json,
    read_version,
    scan_secrets,
    scan_source_inputs,
    sha256_file,
    stage_content_bundles,
    tool_versions,
    verify_content_bundles,
    verify_quality_gate,
    verify_release,
    verify_staged_content_bundles,
    write_checksums,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def snapshot_command(args: argparse.Namespace) -> None:
    payload = digest_inputs(args.project).as_dict()
    write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))


def stage_command(args: argparse.Namespace) -> None:
    payload = copy_inputs(args.project, args.destination).as_dict()
    write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))


def provenance_command(args: argparse.Namespace) -> None:
    project = args.project.resolve()
    included = {relative.as_posix() for _path, relative in iter_input_files(project)}
    try:
        tracked_result = subprocess.run(
            ["git", "-C", str(project), "ls-files", "--cached", "-z"],
            check=True,
            stdout=subprocess.PIPE,
        )
        changed_result = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "diff",
                "--no-renames",
                "--name-only",
                "-z",
                "HEAD",
                "--",
                *SOURCE_INPUTS,
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        head_result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseError(f"cannot determine git provenance: {exc}") from exc
    tracked = {
        value.decode("utf-8", errors="surrogateescape")
        for value in tracked_result.stdout.split(b"\0")
        if value
    }
    untracked_inputs = sorted(included - tracked)
    changed_inputs = sorted(
        value.decode("utf-8", errors="surrogateescape")
        for value in changed_result.stdout.split(b"\0")
        if value
        and is_source_input_path(value.decode("utf-8", errors="surrogateescape"))
    )
    payload = {
        "head": head_result.stdout.strip(),
        "scope": "release_inputs",
        "scope_paths": sorted(SOURCE_INPUTS),
        "dirty": bool(changed_inputs or untracked_inputs),
        "included_input_count": len(included),
        "tracked_input_count": len(included & tracked),
        "untracked_or_ignored_input_count": len(untracked_inputs),
        "untracked_or_ignored_input_sample": untracked_inputs[:100],
        "git_status_entry_count": len(changed_inputs),
        "git_status_entry_sample": changed_inputs[:100],
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))


def source_secret_scan_command(args: argparse.Namespace) -> None:
    findings = scan_source_inputs(args.project)
    payload = {
        "status": "failed" if findings else "passed",
        "finding_count": len(findings),
        "findings": findings,
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))
    if findings:
        raise ReleaseError(f"source secret scan failed: {findings}")


def content_bundles_command(args: argparse.Namespace) -> None:
    bundles = verify_content_bundles(args.project)
    payload = {"schema_version": 1, "status": "passed", "bundles": bundles}
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))


def stage_content_bundles_command(args: argparse.Namespace) -> None:
    bundles = stage_content_bundles(args.project, args.destination)
    payload = {"schema_version": 1, "status": "passed", "bundles": bundles}
    write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))


def assemble_command(args: argparse.Namespace) -> None:
    release_dir = args.release_dir
    if any(release_dir.iterdir()):
        raise ReleaseError(f"release staging directory is not empty: {release_dir}")
    version = read_version(args.staged_project)
    quality = load_json(args.quality_metadata)
    content_bundle_payload = load_json(args.content_bundles)
    if (
        set(content_bundle_payload) != {"schema_version", "status", "bundles"}
        or content_bundle_payload.get("schema_version") != 1
        or content_bundle_payload.get("status") != "passed"
        or not isinstance(content_bundle_payload.get("bundles"), list)
    ):
        raise ReleaseError("content bundle staging evidence is invalid")
    content_bundles = verify_staged_content_bundles(
        args.staged_project,
        args.frontend_dist,
        content_bundle_payload["bundles"],
    )
    frontend_budget = load_json(args.frontend_budget)
    if (
        frontend_budget.get("schema_version") != 1
        or frontend_budget.get("status") != "passed"
        or frontend_budget.get("failures") != []
        or not isinstance(frontend_budget.get("surfaces"), dict)
    ):
        raise ReleaseError("frontend budget evidence is invalid or failed")
    verify_quality_gate(
        quality,
        production=args.production,
        allow_unverified=args.allow_unverified,
    )

    shutil.copy2(args.staged_project / "VERSION", release_dir / "VERSION")
    copy_release_backend(args.staged_project, release_dir)
    shutil.copytree(args.frontend_dist, release_dir / "frontend-dist")
    metadata_dir = release_dir / "build-metadata"
    metadata_dir.mkdir(parents=True)
    source_bundle = copy_inputs(args.staged_project, metadata_dir / "source").as_dict()
    shutil.copy2(args.quality_metadata, metadata_dir / "quality-gate.json")
    frontend_budget_path = metadata_dir / "frontend-budget.json"
    shutil.copy2(args.frontend_budget, frontend_budget_path)
    locks = copy_lock_files(args.staged_project, metadata_dir)
    dependency_manifests = copy_dependency_manifests(args.staged_project, metadata_dir)
    python_runtime = archive_python_runtime_attestation(
        args.staged_project,
        metadata_dir,
        runtime_dir=args.python_runtime_dir,
        runtime_manifest_path=args.python_runtime_manifest,
        allowed_runtime_root=args.python_runtime_root,
        production=args.production,
    )
    if python_runtime.get("version") != version:
        raise ReleaseError("Python runtime version must match the application release version")
    write_json(
        args.output,
        {
            "version": version,
            "dependency_locks": locks,
            "dependency_manifests": dependency_manifests,
            "python_runtime": python_runtime,
            "source_bundle": source_bundle,
            "content_bundles": content_bundles,
            "frontend_budget": {
                "status": "passed",
                "artifact_path": "build-metadata/frontend-budget.json",
                "sha256": sha256_file(frontend_budget_path),
                "config_sha256": frontend_budget.get("config_sha256"),
            },
        },
    )


def finalize_command(args: argparse.Namespace) -> None:
    release_dir = args.release_dir
    assembly = load_json(args.assembly_metadata)
    before = load_json(args.source_before)
    staged = load_json(args.source_staged)
    staged_after = load_json(args.source_staged_after)
    after = load_json(args.source_after)
    quality_path = release_dir / "build-metadata" / "quality-gate.json"
    quality = load_json(quality_path)
    provenance = load_json(args.provenance)

    if args.dirty_override and not args.source_dirty:
        raise ReleaseError("dirty override cannot be recorded for a clean source")
    if provenance.get("dirty") is not args.source_dirty:
        raise ReleaseError("git provenance and source dirty flag disagree")
    if provenance.get("head") != args.git_sha:
        raise ReleaseError("git provenance HEAD does not match the release git SHA")

    if before != staged:
        raise ReleaseError(
            f"staged source differs from source snapshot: source={before} staged={staged}"
        )
    if staged != staged_after:
        raise ReleaseError(
            f"staged source changed during frontend build: before={staged} after={staged_after}"
        )
    if before != after:
        raise ReleaseError(f"source changed during release build: before={before} after={after}")
    verify_quality_gate(
        quality,
        production=args.production,
        allow_unverified=not args.production,
        expected_source_snapshot=before,
    )
    findings = scan_secrets(release_dir)
    if findings:
        raise ReleaseError(f"secret scan failed before manifest generation: {findings}")

    artifact = write_checksums(release_dir)
    frontend = digest_tree(release_dir / "frontend-dist")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": assembly["version"],
        "build_id": args.build_id,
        "git_sha": args.git_sha,
        "source_dirty": args.source_dirty,
        "created_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "backend_entry": "backend/serve_prod.py",
        "frontend_dist": "frontend-dist",
        "source": {
            "dirty": args.source_dirty,
            "dirty_override": args.dirty_override,
            "production_dirty_policy": "reject_unless_explicitly_overridden",
            "snapshot": before,
            "staged_snapshot": staged,
            "bundle_path": "build-metadata/source",
            "bundle_snapshot": assembly["source_bundle"],
            "provenance": provenance,
        },
        "dependency_locks": assembly["dependency_locks"],
        "dependency_manifests": assembly["dependency_manifests"],
        "content_bundles": assembly["content_bundles"],
        "python_runtime": assembly["python_runtime"],
        "tools": tool_versions(),
        "build": {
            "started_at": args.build_started_at,
            "finished_at": args.build_finished_at,
            "source_unchanged": True,
            "staged_source_unchanged": True,
            "frontend": {
                "dependency_mode": args.dependency_mode,
                **frontend.as_dict(),
            },
            "frontend_budget": assembly["frontend_budget"],
        },
        "quality_gate": {
            "status": quality.get("status"),
            "artifact_path": "build-metadata/quality-gate.json",
            "sha256": sha256_file(quality_path),
            "tests": quality.get("tests", {}),
            "ratchets": quality.get("ratchets", {}),
        },
        "secret_scan": {
            "status": "passed",
            "scanner": "globemind-release-lib-v1",
        },
        "artifact": {
            "manifest": "SHA256SUMS",
            "manifest_sha256": artifact.sha256,
            "file_count": artifact.file_count,
            "total_bytes": artifact.total_bytes,
        },
    }
    write_json(release_dir / "release.json", manifest)
    findings = scan_secrets(release_dir)
    if findings:
        raise ReleaseError(f"secret scan failed after manifest generation: {findings}")

    for path in sorted(release_dir.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o444 | (path.stat().st_mode & 0o111))
    release_dir.chmod(0o555)
    verify_release(release_dir, production=args.production)
    print(release_dir)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subcommands = root.add_subparsers(dest="command", required=True)

    snapshot = subcommands.add_parser("snapshot")
    snapshot.add_argument("--project", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.set_defaults(handler=snapshot_command)

    stage = subcommands.add_parser("stage")
    stage.add_argument("--project", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.set_defaults(handler=stage_command)

    provenance = subcommands.add_parser("provenance")
    provenance.add_argument("--project", type=Path, required=True)
    provenance.add_argument("--output", type=Path)
    provenance.set_defaults(handler=provenance_command)

    source_secrets = subcommands.add_parser("source-secret-scan")
    source_secrets.add_argument("--project", type=Path, required=True)
    source_secrets.add_argument("--output", type=Path)
    source_secrets.set_defaults(handler=source_secret_scan_command)

    content_bundles = subcommands.add_parser("content-bundles")
    content_bundles.add_argument("--project", type=Path, required=True)
    content_bundles.add_argument("--output", type=Path)
    content_bundles.set_defaults(handler=content_bundles_command)

    stage_content = subcommands.add_parser("stage-content-bundles")
    stage_content.add_argument("--project", type=Path, required=True)
    stage_content.add_argument("--destination", type=Path, required=True)
    stage_content.add_argument("--output", type=Path, required=True)
    stage_content.set_defaults(handler=stage_content_bundles_command)

    assemble = subcommands.add_parser("assemble")
    assemble.add_argument("--staged-project", type=Path, required=True)
    assemble.add_argument("--frontend-dist", type=Path, required=True)
    assemble.add_argument("--release-dir", type=Path, required=True)
    assemble.add_argument("--quality-metadata", type=Path, required=True)
    assemble.add_argument("--content-bundles", type=Path, required=True)
    assemble.add_argument("--frontend-budget", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    assemble.add_argument("--allow-unverified", action="store_true")
    assemble.add_argument("--production", action="store_true")
    assemble.add_argument("--python-runtime-dir", type=Path, required=True)
    assemble.add_argument("--python-runtime-manifest", type=Path, required=True)
    assemble.add_argument("--python-runtime-root", type=Path, required=True)
    assemble.set_defaults(handler=assemble_command)

    finalize = subcommands.add_parser("finalize")
    finalize.add_argument("--release-dir", type=Path, required=True)
    finalize.add_argument("--assembly-metadata", type=Path, required=True)
    finalize.add_argument("--source-before", type=Path, required=True)
    finalize.add_argument("--source-staged", type=Path, required=True)
    finalize.add_argument("--source-staged-after", type=Path, required=True)
    finalize.add_argument("--source-after", type=Path, required=True)
    finalize.add_argument("--build-id", required=True)
    finalize.add_argument("--git-sha", required=True)
    finalize.add_argument("--source-dirty", action="store_true")
    finalize.add_argument("--dirty-override", action="store_true")
    finalize.add_argument("--dependency-mode", choices=("ci", "linked"), required=True)
    finalize.add_argument("--build-started-at", required=True)
    finalize.add_argument("--build-finished-at", required=True)
    finalize.add_argument("--production", action="store_true")
    finalize.add_argument("--provenance", type=Path, required=True)
    finalize.set_defaults(handler=finalize_command)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args)
    except ReleaseError as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
