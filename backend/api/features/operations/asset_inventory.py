"""Auditable V1 API, dependency, environment, and processing inventory.

The inventory deliberately contains contracts and metadata only. It never
reads environment values, secret files, process arguments, database rows, or
user content.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi.routing import APIRoute

ASSET_INVENTORY_SCHEMA_VERSION = "globemind.asset-inventory.v1"

_DEPENDENCY_MANIFESTS = (
    ("python-web", "requirements/roles/web.lock", "python-lock"),
    ("frontend-workspaces", "package-lock.json", "npm-lock"),
)
_ENVIRONMENT_MANIFEST = "config/runtime/env-manifest.json"
_PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?"
    r"==([^\s;\\]+)(?:\s*;.*)?$"
)
_DIRECT_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s+@\s+(https://[^\s;]+)(?:\s*;.*)?$"
)


class InventoryManifestError(ValueError):
    """A checked-in inventory manifest is missing, ambiguous, or unsafe."""


def _manifest_path(
    repository_root: Path,
    relative_path: str,
) -> tuple[Path | None, str]:
    root = repository_root.resolve()
    candidate = root / relative_path
    current = root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            return None, "invalid_path"
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return None, "missing"
    if not resolved.is_file():
        return None, "missing"
    return resolved, "available"


def _load_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                raise InventoryManifestError("duplicate JSON key")
            payload[key] = value
        return payload

    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(payload, dict):
        raise InventoryManifestError("manifest root must be an object")
    return payload

PROCESSING_ACTIVITIES: tuple[dict[str, Any], ...] = (
    {
        "id": "account-identity",
        "label": "账号与身份验证",
        "data_categories": [
            "username",
            "full_name",
            "email",
            "phone",
            "password_hash",
            "login_timestamps",
        ],
        "confirmed_purposes": ["建立账号", "登录验证", "唯一性校验", "密码重置"],
        "storage": ["postgres:app_user"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_assessed",
        "rights_workflow_status": "manual_intake_only",
    },
    {
        "id": "password-reset",
        "label": "密码重置",
        "data_categories": ["user_id", "token_hash", "expiry", "used_at"],
        "confirmed_purposes": ["一次性账号恢复"],
        "storage": ["postgres:password_reset_token"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_applicable",
        "rights_workflow_status": "manual_intake_only",
    },
    {
        "id": "research-history-and-favorites",
        "label": "检索历史与收藏",
        "data_categories": ["user_id", "search_terms", "news_ids", "folder_labels", "timestamps"],
        "confirmed_purposes": ["恢复用户研究操作", "管理收藏"],
        "storage": ["postgres:user_search_history", "postgres:user_favorite", "browser:guest-storage"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_assessed",
        "rights_workflow_status": "manual_intake_only",
    },
    {
        "id": "assistant-conversations-and-memory",
        "label": "数据助手会话与长期记忆",
        "data_categories": ["user_id", "prompts", "responses", "context", "memory_summary", "model_metadata"],
        "confirmed_purposes": ["响应用户研究请求", "在用户账号内恢复会话上下文"],
        "storage": ["postgres:assistant_chat_session", "postgres:assistant_chat_message", "postgres:assistant_user_memory"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_assessed",
        "rights_workflow_status": "manual_intake_only",
    },
    {
        "id": "workspace-files-and-reports",
        "label": "用户工作区文件与报告",
        "data_categories": ["uploaded_files", "notes", "generated_reports", "workspace_metadata"],
        "confirmed_purposes": ["用户私有研究资料管理", "生成用户请求的报告"],
        "storage": ["filesystem:user-workspace"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_assessed",
        "rights_workflow_status": "manual_intake_only",
    },
    {
        "id": "user-model-provider-settings",
        "label": "用户模型供应商配置",
        "data_categories": ["encrypted_api_keys", "provider", "model", "custom_base_url"],
        "confirmed_purposes": ["按用户选择调用模型服务"],
        "storage": ["postgres:app_user"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_assessed",
        "rights_workflow_status": "manual_intake_only",
    },
    {
        "id": "operational-telemetry",
        "label": "运行与安全遥测",
        "data_categories": ["bounded_route_path", "pseudonymous_client_id", "timestamps", "error_and_security_events"],
        "confirmed_purposes": ["可用性监测", "故障排查", "安全防护"],
        "storage": ["runtime:bounded-operational-state", "external:service-logs"],
        "owner": "待指定",
        "retention_status": "not_approved",
        "legal_basis_status": "not_approved",
        "processor_inventory_status": "not_complete",
        "training_use_status": "not_applicable",
        "rights_workflow_status": "manual_intake_only",
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _python_dependencies(path: Path) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    logical = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--") and not logical:
            continue
        logical = f"{logical} {line}".strip() if logical else line
        if logical.endswith("\\"):
            logical = logical[:-1].strip()
            continue
        requirement = logical.split(" --hash=", 1)[0].strip()
        logical = ""
        match = _PINNED_REQUIREMENT.fullmatch(requirement)
        if match is None:
            direct = _DIRECT_REQUIREMENT.fullmatch(requirement)
            if direct is None:
                raise InventoryManifestError("unrecognized Python lock entry")
            parsed = urllib.parse.urlsplit(direct.group(2))
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise InventoryManifestError("unsafe direct Python lock entry")
            package_name = direct.group(1).lower().replace("_", "-")
            filename = urllib.parse.unquote(Path(parsed.path).name)
            normalized_filename = filename.lower().replace("_", "-")
            prefix = f"{package_name}-"
            if not normalized_filename.startswith(prefix) or not filename.endswith(".whl"):
                raise InventoryManifestError("unversioned direct Python lock entry")
            version = normalized_filename[len(prefix) :].split("-", 1)[0]
            if not version:
                raise InventoryManifestError("unversioned direct Python lock entry")
            dependencies.append(
                {"name": package_name, "version": version, "license": "unknown"}
            )
            continue
        dependencies.append(
            {
                "name": match.group(1).lower().replace("_", "-"),
                "version": match.group(2),
                "license": "unknown",
            }
        )
    if logical:
        raise InventoryManifestError("unterminated Python lock entry")
    return sorted(dependencies, key=lambda item: item["name"])


def _npm_dependencies(path: Path) -> list[dict[str, Any]]:
    payload = _load_json_object(path)
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        return []
    dependencies: list[dict[str, Any]] = []
    for package_path, raw in packages.items():
        if not package_path or not isinstance(raw, dict):
            continue
        marker = "node_modules/"
        if marker not in package_path:
            continue
        name = package_path.rsplit(marker, 1)[-1].strip()
        version = str(raw.get("version") or "").strip()
        if not name or not version:
            continue
        license_value = raw.get("license")
        dependencies.append(
            {
                "name": name,
                "version": version,
                "license": (
                    str(license_value).strip() if license_value else "unknown"
                ),
            }
        )
    return sorted(dependencies, key=lambda item: (item["name"], item["version"]))


def build_dependency_inventory(repository_root: Path) -> list[dict[str, Any]]:
    root = repository_root.resolve()
    manifests: list[dict[str, Any]] = []
    for inventory_id, relative_path, kind in _DEPENDENCY_MANIFESTS:
        path, path_status = _manifest_path(root, relative_path)
        if path is None:
            manifests.append(
                {
                    "id": inventory_id,
                    "path": relative_path,
                    "kind": kind,
                    "status": path_status,
                    "sha256": None,
                    "dependency_count": 0,
                    "dependencies": [],
                }
            )
            continue
        try:
            dependencies = (
                _python_dependencies(path)
                if kind == "python-lock"
                else _npm_dependencies(path)
            )
            manifest_hash = _sha256(path)
        except (InventoryManifestError, json.JSONDecodeError, OSError, UnicodeError):
            dependencies = []
            manifest_hash = None
        manifests.append(
            {
                "id": inventory_id,
                "path": relative_path,
                "kind": kind,
                "status": "available" if dependencies else "invalid_or_empty",
                "sha256": manifest_hash,
                "dependency_count": len(dependencies),
                "license_completion": {
                    "known": sum(item["license"] != "unknown" for item in dependencies),
                    "unknown": sum(item["license"] == "unknown" for item in dependencies),
                },
                "dependencies": dependencies,
            }
        )
    return manifests


def _dependency_names(items: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for item in items:
        call = getattr(item, "call", None)
        name = getattr(call, "__name__", None)
        if isinstance(name, str):
            names.add(name)
        names.update(_dependency_names(getattr(item, "dependencies", ()) or ()))
    return names


def _access_contract(route: APIRoute) -> dict[str, str]:
    names = _dependency_names(route.dependant.dependencies)
    if "get_current_admin_user" in names:
        return {"level": "administrator", "evidence": "fastapi_dependency"}
    if "get_current_user_required" in names:
        return {"level": "authenticated", "evidence": "fastapi_dependency"}
    if "get_current_user_optional" in names:
        return {"level": "optional_identity", "evidence": "fastapi_dependency"}
    return {
        "level": "manual_review_required",
        "evidence": "no_recognized_auth_dependency",
    }


def build_api_inventory(routes: Iterable[Any]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        inventory.append(
            {
                "path": route.path,
                "methods": sorted(
                    method for method in (route.methods or ()) if method != "HEAD"
                ),
                "name": route.name,
                "tags": [str(tag) for tag in route.tags],
                "access": _access_contract(route),
            }
        )
    return sorted(inventory, key=lambda item: (item["path"], item["methods"]))


def build_environment_inventory(repository_root: Path) -> dict[str, Any]:
    relative_path = _ENVIRONMENT_MANIFEST
    path, path_status = _manifest_path(repository_root, relative_path)
    if path is None:
        return {
            "path": relative_path,
            "status": path_status,
            "sha256": None,
            "services": [],
            "variables": [],
        }
    try:
        payload = _load_json_object(path)
        manifest_hash = _sha256(path)
    except (InventoryManifestError, json.JSONDecodeError, OSError, UnicodeError):
        return {
            "path": relative_path,
            "status": "invalid_or_empty",
            "sha256": None,
            "services": [],
            "variables": [],
        }
    services = payload.get("services") if isinstance(payload, dict) else None
    variables = payload.get("variables") if isinstance(payload, dict) else None
    safe_services = [
        {
            "id": str(service_id),
            "owner": str(raw.get("owner") or "待指定"),
            "description": str(raw.get("description") or ""),
        }
        for service_id, raw in (services.items() if isinstance(services, dict) else ())
        if isinstance(raw, dict)
    ]
    safe_variables = [
        {
            "name": str(raw.get("name") or ""),
            "scope": str(raw.get("scope") or ""),
            "owner": str(raw.get("owner") or "待指定"),
            "sensitivity": str(raw.get("sensitivity") or "unknown"),
            "services": [
                str(item)
                for item in (
                    raw.get("services", [])
                    if isinstance(raw.get("services"), list)
                    else []
                )
            ],
            "restart_required": raw.get("restart_required") is True,
            "activation": str(raw.get("activation") or "unknown"),
        }
        for raw in (variables if isinstance(variables, list) else [])
        if isinstance(raw, dict) and raw.get("name")
    ]
    return {
        "path": relative_path,
        "status": "available" if safe_variables else "invalid_or_empty",
        "sha256": manifest_hash,
        "services": sorted(safe_services, key=lambda item: item["id"]),
        "variables": sorted(safe_variables, key=lambda item: item["name"]),
    }


def build_asset_inventory(
    app: Any,
    *,
    repository_root: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    api_routes = build_api_inventory(getattr(app, "routes", ()))
    dependencies = build_dependency_inventory(repository_root)
    environment = build_environment_inventory(repository_root)
    return {
        "schema_version": ASSET_INVENTORY_SCHEMA_VERSION,
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "scope": "source_and_running_application_contracts",
        "api": {
            "status": "inventory_complete_access_review_incomplete",
            "route_count": len(api_routes),
            "routes": api_routes,
            "access_review_gap_count": sum(
                route["access"]["level"] == "manual_review_required"
                for route in api_routes
            ),
        },
        "dependencies": {
            "status": (
                "available_with_license_gaps"
                if all(item["status"] == "available" for item in dependencies)
                else "incomplete"
            ),
            "manifests": dependencies,
        },
        "environment": environment,
        "processing_activities": copy.deepcopy(PROCESSING_ACTIVITIES),
        "processing_inventory_status": "engineering_inventory_requires_privacy_and_legal_approval",
        "exclusions": [
            "environment values",
            "secret-file contents",
            "process arguments",
            "database rows",
            "user content",
        ],
    }


__all__ = (
    "ASSET_INVENTORY_SCHEMA_VERSION",
    "InventoryManifestError",
    "PROCESSING_ACTIVITIES",
    "build_api_inventory",
    "build_asset_inventory",
    "build_dependency_inventory",
    "build_environment_inventory",
)
