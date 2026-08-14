"""Read-only service catalog projection and artifact drift checks."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .constants import DATA_ROOT, PROJECT_ROOT, SCHEMA_VERSION
from .manifest import InventoryError, ensure_trusted_path, service_dependency_order
from .redaction import sanitize

MAX_CATALOG_ARTIFACT_BYTES = 2 * 1024 * 1024


def _issue(code: str, role: str, message: str) -> dict[str, str]:
    return {"code": code, "role": role, "message": message}


def _trusted_roots(inventory: Mapping[str, Any]) -> tuple[Path, ...]:
    roots = getattr(inventory, "trusted_roots", None)
    if roots:
        return tuple(Path(root).resolve() for root in roots)
    return (PROJECT_ROOT.resolve(), DATA_ROOT.resolve())


def _read_artifact(
    path_value: str,
    roots: Sequence[Path],
    *,
    role: str,
    require_executable: bool = False,
    read_content: bool = False,
) -> tuple[bytes | None, list[dict[str, str]]]:
    try:
        path = ensure_trusted_path(path_value, roots)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                return None, [_issue("artifact-not-regular", role, "artifact is not a regular file")]
            if metadata.st_uid != os.geteuid():
                return None, [_issue("artifact-owner-drift", role, "artifact owner differs from caller")]
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                return None, [
                    _issue(
                        "artifact-permission-drift",
                        role,
                        "artifact is group/world writable",
                    )
                ]
            if require_executable and not stat.S_IMODE(metadata.st_mode) & 0o111:
                return None, [
                    _issue("artifact-not-executable", role, "controller artifact is not executable")
                ]
            if metadata.st_size > MAX_CATALOG_ARTIFACT_BYTES:
                return None, [_issue("artifact-too-large", role, "artifact exceeds the read limit")]
            content = handle.read(MAX_CATALOG_ARTIFACT_BYTES + 1) if read_content else None
        if content is not None and len(content) > MAX_CATALOG_ARTIFACT_BYTES:
            return None, [_issue("artifact-too-large", role, "artifact exceeds the read limit")]
        return content, []
    except (InventoryError, OSError):
        return None, [_issue("artifact-unavailable", role, "artifact is unavailable or untrusted")]


def catalog_drift_issues(
    inventory: Mapping[str, Any], service: Mapping[str, Any]
) -> list[dict[str, str]]:
    """Return bounded, non-sensitive drift findings without inspecting processes."""

    roots = _trusted_roots(inventory)
    issues: list[dict[str, str]] = []
    controller = service["controller"]
    controller_paths = [("controller", controller["path"])]
    if controller.get("entrypoint"):
        controller_paths.append(("controller-entrypoint", controller["entrypoint"]))
    for role, path in controller_paths:
        _content, artifact_issues = _read_artifact(
            path,
            roots,
            role=role,
            require_executable=controller["type"] != "python-script",
        )
        issues.extend(artifact_issues)

    runbook = service["runbook"]
    content, artifact_issues = _read_artifact(
        runbook["path"], roots, role="runbook", read_content=True
    )
    issues.extend(artifact_issues)
    if content is not None:
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            issues.append(_issue("artifact-not-utf8", "runbook", "runbook is not UTF-8"))
        else:
            section = re.escape(runbook["section"])
            if re.search(rf"(?m)^#{{1,6}}[ \t]+`?{section}`?[ \t]*$", text) is None:
                issues.append(
                    _issue(
                        "runbook-section-missing",
                        "runbook",
                        "declared runbook section is missing",
                    )
                )

    for index, evidence in enumerate(service["replay"]["evidence"]):
        role = f"replay-evidence-{index}"
        content, artifact_issues = _read_artifact(
            evidence["path"], roots, role=role, read_content=True
        )
        issues.extend(artifact_issues)
        if content is None:
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            issues.append(_issue("artifact-not-utf8", role, "replay evidence is not UTF-8"))
            continue
        if re.search(rf"\b{re.escape(evidence['selector'])}\b", text) is None:
            issues.append(
                _issue(
                    "replay-selector-missing",
                    role,
                    "declared replay evidence selector is missing",
                )
            )
    return issues


def _management_blockers(
    service: Mapping[str, Any], drift: Sequence[Mapping[str, str]]
) -> list[str]:
    blockers = [f"catalog-drift:{item['code']}" for item in drift]
    if service["lifecycle_authorization"]["state"] != "authorized":
        blockers.append("lifecycle-not-authorized")
    pid = service["pid"]
    if pid["kind"] != "single" or not pid.get("meta_path") or not pid.get("meta"):
        blockers.append("strong-process-identity-not-evidenced")
    if service["kind"] == "pipeline":
        checkpoint_mode = service["checkpoint"]["mode"]
        if checkpoint_mode != "durable":
            blockers.append(f"checkpoint-{checkpoint_mode}")
        replay_assurance = service["replay"]["assurance"]
        if replay_assurance != "verified":
            blockers.append(f"replay-{replay_assurance}")
        if service["checkpoint"]["takeover_ready"] is not True:
            blockers.append("takeover-not-declared")
    if any(
        not isinstance(dependency, Mapping)
        or dependency.get("verification") in {None, "manual", "unverified"}
        for dependency in service["external_dependencies"]
    ):
        blockers.append("external-dependency-unverified")
    if service["health_policy"]["mode"] in {"process-only", "log-freshness"}:
        blockers.append("business-health-not-evidenced")
    return list(dict.fromkeys(blockers))


def public_catalog_definition(
    inventory: Mapping[str, Any], service: Mapping[str, Any]
) -> dict[str, Any]:
    drift = catalog_drift_issues(inventory, service)
    blockers = _management_blockers(service, drift)
    checkpoint = service["checkpoint"]
    declared_takeover_ready = checkpoint["takeover_ready"] is True
    return {
        "id": service["id"],
        "name": service["name"],
        "kind": service["kind"],
        "owner": service["owner"],
        "criticality": service["criticality"],
        "dependencies": service["dependencies"],
        "external_dependencies": service["external_dependencies"],
        "controller": {
            "type": service["controller"]["type"],
            "path": service["controller"]["path"],
            "interface": service["controller"]["interface"],
            "adoption": service["controller"]["adoption"],
        },
        "health_policy": service["health_policy"],
        "checkpoint": checkpoint,
        "replay": {
            "mode": service["replay"]["mode"],
            "assurance": service["replay"]["assurance"],
            "evidence_count": len(service["replay"]["evidence"]),
        },
        "secret_refs": [
            {"name": item["name"], "policy_file_index": item["file_index"]}
            for item in service["secret_refs"]
        ],
        "secret_transport": service["secret_policy"]["environment"],
        "lifecycle_authorization": service["lifecycle_authorization"],
        "runbook": service["runbook"],
        "catalog_status": "drifted" if drift else "current",
        "catalog_drift": drift,
        "takeover_ready": declared_takeover_ready and not blockers,
        "management_blockers": blockers,
    }


def catalog_payload(
    inventory: Mapping[str, Any], service_ids: Sequence[str]
) -> dict[str, Any]:
    """Build a deterministic, read-only catalog summary with no process access."""

    order = service_dependency_order(inventory, service_ids)
    definitions = {service["id"]: service for service in inventory["services"]}
    services = [public_catalog_definition(inventory, definitions[identifier]) for identifier in order]
    drifted = sum(service["catalog_status"] == "drifted" for service in services)
    authorized = sum(
        service["lifecycle_authorization"]["state"] == "authorized" for service in services
    )
    takeover_ready = sum(service["takeover_ready"] is True for service in services)
    return sanitize(
        {
            "schema_version": SCHEMA_VERSION,
            "inventory_version": inventory.get("inventory_version"),
            "operation": "catalog",
            "read_only": True,
            "process_inspection": False,
            "requested_services": list(service_ids),
            "dependency_closure": order,
            "summary": {
                "service_count": len(services),
                "catalog_current": len(services) - drifted,
                "catalog_drifted": drifted,
                "lifecycle_authorized": authorized,
                "takeover_ready": takeover_ready,
                "takeover_blocked": len(services) - takeover_ready,
            },
            "services": services,
        }
    )
