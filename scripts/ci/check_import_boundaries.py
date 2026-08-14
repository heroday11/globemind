#!/usr/bin/env python3
"""Enforce GlobeMind module boundaries with a debt-reducing baseline.

The baseline records the number of existing violations per rule and file.
Moving debt to a new file or adding another violation fails. Removing debt
requires lowering the baseline in the same change, and the update command
refuses to write a baseline that increases any existing ceiling.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = REPOSITORY_ROOT / "quality" / "import-boundaries-baseline.json"

RULE_CORE_TO_SERVICES = "backend-core-imports-services"
RULE_PIPELINE_TO_SCRIPTS = "core-pipeline-imports-scripts"
RULE_SHARED_TO_VIEWS = "frontend-shared-imports-views"
RULE_ROUTE_DOTENV = "route-loads-dotenv"
RULE_DIRECT_ENV = "backend-direct-environment-read"
RULE_BACKEND_FEATURE_PUBLIC_API = "backend-feature-public-api"
RULE_FRONTEND_FEATURE_PUBLIC_API = "frontend-feature-public-api"

RULES = (
    RULE_CORE_TO_SERVICES,
    RULE_PIPELINE_TO_SCRIPTS,
    RULE_SHARED_TO_VIEWS,
    RULE_ROUTE_DOTENV,
    RULE_DIRECT_ENV,
    RULE_BACKEND_FEATURE_PUBLIC_API,
    RULE_FRONTEND_FEATURE_PUBLIC_API,
)

RULE_DESCRIPTIONS = {
    RULE_CORE_TO_SERVICES: "backend/api/core must not depend on feature services",
    RULE_PIPELINE_TO_SCRIPTS: "core_pipeline must not depend on executable scripts",
    RULE_SHARED_TO_VIEWS: "frontend shared code must not depend on route views",
    RULE_ROUTE_DOTENV: "route modules must not load .env files",
    RULE_DIRECT_ENV: "backend runtime modules must use the central configuration boundary",
    RULE_BACKEND_FEATURE_PUBLIC_API: "backend callers must use feature public APIs",
    RULE_FRONTEND_FEATURE_PUBLIC_API: "frontend callers must use feature public APIs",
}

FRONTEND_SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".vue"}
FRONTEND_SHARED_DIRS = ("shared", "components", "utils", "config")
IMPORT_SOURCE_RE = re.compile(
    r"(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)"
    r"(?P<quote>['\"])(?P<source>[^'\"]+)(?P=quote)"
)


class BoundaryCheckError(RuntimeError):
    """Raised when source or baseline data cannot be scanned safely."""


@dataclass(frozen=True, order=True)
class Violation:
    rule: str
    path: str
    line: int
    detail: str


@dataclass(frozen=True)
class RatchetResult:
    new_debt: dict[str, dict[str, int]]
    resolved_debt: dict[str, dict[str, int]]
    current_counts: dict[str, dict[str, int]]

    @property
    def passed(self) -> bool:
        return not self.new_debt and not self.resolved_debt

    @property
    def regression_free(self) -> bool:
        return not self.new_debt


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _python_tree(path: Path) -> ast.AST:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise BoundaryCheckError(f"cannot parse {path}: {exc}") from exc


def _imported_modules(node: ast.Import | ast.ImportFrom) -> Iterable[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    module = node.module or ""
    yield module
    if module:
        yield from (f"{module}.{alias.name}" for alias in node.names)
    else:
        yield from (alias.name for alias in node.names)


def _module_is_services(module: str) -> bool:
    parts = module.split(".")
    return "services" in parts and (
        parts[0] in {"api", "backend", "services"} or module == "services"
    )


def _module_is_scripts(module: str) -> bool:
    return module == "scripts" or module.startswith("scripts.")


def _scan_for_forbidden_imports(
    root: Path,
    source_root: Path,
    rule: str,
    predicate,
) -> list[Violation]:
    violations: list[Violation] = []
    if not source_root.is_dir():
        return violations
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = _python_tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            matches = sorted({module for module in _imported_modules(node) if predicate(module)})
            if matches:
                violations.append(
                    Violation(rule, _relative(path, root), node.lineno, ", ".join(matches))
                )
    return violations


def _scan_route_dotenv(root: Path) -> list[Violation]:
    route_root = root / "backend" / "api" / "routes"
    violations: list[Violation] = []
    if not route_root.is_dir():
        return violations
    for path in sorted(route_root.rglob("*.py")):
        tree = _python_tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
                violations.append(
                    Violation(
                        RULE_ROUTE_DOTENV,
                        _relative(path, root),
                        node.lineno,
                        "route imports dotenv",
                    )
                )
            elif isinstance(node, ast.Import) and any(
                alias.name == "dotenv" or alias.name.startswith("dotenv.")
                for alias in node.names
            ):
                violations.append(
                    Violation(
                        RULE_ROUTE_DOTENV,
                        _relative(path, root),
                        node.lineno,
                        "route imports dotenv",
                    )
                )
    return violations


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _environment_key(call: ast.Call) -> str:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return "<dynamic>"


def _environment_import_aliases(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    os_names = {"os"}
    getenv_names: set[str] = set()
    environ_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "getenv":
                    getenv_names.add(alias.asname or alias.name)
                elif alias.name == "environ":
                    environ_names.add(alias.asname or alias.name)
    return os_names, getenv_names, environ_names


def _runtime_python_paths(root: Path) -> Iterable[Path]:
    api_root = root / "backend" / "api"
    if api_root.is_dir():
        for path in sorted(api_root.rglob("*.py")):
            relative_parts = path.relative_to(api_root).parts
            if relative_parts and relative_parts[0] == "scripts":
                continue
            yield path

    backend_root = root / "backend"
    for dirname in ("features", "domains", "platform"):
        source_root = backend_root / dirname
        if not source_root.is_dir():
            continue
        for path in sorted(source_root.rglob("*.py")):
            if dirname == "platform" and path.is_relative_to(source_root / "config"):
                continue
            yield path


def _scan_direct_environment_reads(root: Path) -> list[Violation]:
    api_root = root / "backend" / "api"
    allowed_reader = api_root / "core" / "environment.py"
    violations: list[Violation] = []

    for path in _runtime_python_paths(root):
        if path == allowed_reader or "__pycache__" in path.parts:
            continue
        tree = _python_tree(path)
        os_names, getenv_names, environ_names = _environment_import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                chain = _attribute_chain(node.func)
                direct_getenv = isinstance(node.func, ast.Name) and node.func.id in getenv_names
                direct_environ_get = (
                    len(chain) == 2
                    and chain[0] in environ_names
                    and chain[1] == "get"
                )
                module_getenv = (
                    len(chain) == 2 and chain[0] in os_names and chain[1] == "getenv"
                )
                module_environ_get = (
                    len(chain) == 3
                    and chain[0] in os_names
                    and chain[1:] == ("environ", "get")
                )
                if direct_getenv or direct_environ_get or module_getenv or module_environ_get:
                    violations.append(
                        Violation(
                            RULE_DIRECT_ENV,
                            _relative(path, root),
                            node.lineno,
                            _environment_key(node),
                        )
                    )
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                chain = _attribute_chain(node.value)
                direct_environ = isinstance(node.value, ast.Name) and node.value.id in environ_names
                module_environ = (
                    len(chain) == 2 and chain[0] in os_names and chain[1] == "environ"
                )
                if direct_environ or module_environ:
                    key = node.slice
                    detail = key.value if isinstance(key, ast.Constant) else "<dynamic>"
                    violations.append(
                        Violation(
                            RULE_DIRECT_ENV,
                            _relative(path, root),
                            node.lineno,
                            str(detail),
                        )
                    )
    return violations


def _source_targets_views(source: str, path: Path, frontend_root: Path) -> bool:
    normalized = source.replace("\\", "/")
    if normalized == "@/views" or normalized.startswith("@/views/"):
        return True
    if normalized == "~/views" or normalized.startswith("~/views/"):
        return True
    if not normalized.startswith("."):
        return False
    target = (path.parent / normalized).resolve()
    views_root = (frontend_root / "views").resolve()
    try:
        target.relative_to(views_root)
        return True
    except ValueError:
        return False


def _scan_frontend_shared(root: Path) -> list[Violation]:
    frontend_root = root / "frontend" / "vue_project" / "src"
    violations: list[Violation] = []
    for dirname in FRONTEND_SHARED_DIRS:
        shared_root = frontend_root / dirname
        if not shared_root.is_dir():
            continue
        for path in sorted(item for item in shared_root.rglob("*") if item.is_file()):
            if path.suffix.lower() not in FRONTEND_SOURCE_SUFFIXES:
                continue
            try:
                source_text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise BoundaryCheckError(f"cannot read {path}: {exc}") from exc
            for match in IMPORT_SOURCE_RE.finditer(source_text):
                source = match.group("source")
                if not _source_targets_views(source, path, frontend_root):
                    continue
                line = source_text.count("\n", 0, match.start()) + 1
                violations.append(
                    Violation(
                        RULE_SHARED_TO_VIEWS,
                        _relative(path, root),
                        line,
                        source,
                    )
                )
    return violations


def _backend_feature_for_path(path: Path, root: Path) -> str | None:
    for feature_root in (
        root / "backend" / "api" / "features",
        root / "backend" / "features",
    ):
        try:
            relative = path.relative_to(feature_root)
        except ValueError:
            continue
        return relative.parts[0] if len(relative.parts) > 1 else None
    return None


def _backend_feature_import(module: str) -> tuple[str, bool] | None:
    parts = tuple(part for part in module.split(".") if part)
    prefixes = (
        ("api", "features"),
        ("backend", "api", "features"),
        ("backend", "features"),
    )
    for prefix in prefixes:
        if parts[: len(prefix)] != prefix or len(parts) <= len(prefix):
            continue
        return parts[len(prefix)], len(parts) > len(prefix) + 1
    return None


def _backend_feature_child_exists(root: Path, feature: str, child: str) -> bool:
    for feature_root in (
        root / "backend" / "api" / "features" / feature,
        root / "backend" / "features" / feature,
    ):
        if (feature_root / f"{child}.py").is_file() or (feature_root / child).is_dir():
            return True
    return False


def _scan_backend_feature_imports(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    source_roots = (
        root / "backend" / "api",
        root / "backend" / "features",
        root / "backend" / "bootstrap",
    )
    seen: set[Path] = set()
    for source_root in source_roots:
        if not source_root.is_dir():
            continue
        for path in sorted(source_root.rglob("*.py")):
            if path in seen or "__pycache__" in path.parts:
                continue
            seen.add(path)
            importer_feature = _backend_feature_for_path(path, root)
            tree = _python_tree(path)
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level >= 2 and importer_feature and node.module:
                        relative_parts = tuple(
                            part for part in node.module.split(".") if part
                        )
                        if relative_parts:
                            target_feature = relative_parts[0]
                            if target_feature != importer_feature and len(relative_parts) > 1:
                                violations.append(
                                    Violation(
                                        RULE_BACKEND_FEATURE_PUBLIC_API,
                                        _relative(path, root),
                                        node.lineno,
                                        f"relative deep import: {node.module}",
                                    )
                                )
                        continue
                    if node.module:
                        modules.append(node.module)
                        imported = _backend_feature_import(node.module)
                        if imported is not None:
                            target_feature, is_deep = imported
                            if not is_deep and target_feature != importer_feature:
                                for alias in node.names:
                                    if alias.name != "*" and _backend_feature_child_exists(
                                        root, target_feature, alias.name
                                    ):
                                        violations.append(
                                            Violation(
                                                RULE_BACKEND_FEATURE_PUBLIC_API,
                                                _relative(path, root),
                                                node.lineno,
                                                f"{node.module}.{alias.name}",
                                            )
                                        )
                else:
                    continue
                for module in modules:
                    imported = _backend_feature_import(module)
                    if imported is None:
                        continue
                    target_feature, is_deep = imported
                    if is_deep and target_feature != importer_feature:
                        violations.append(
                            Violation(
                                RULE_BACKEND_FEATURE_PUBLIC_API,
                                _relative(path, root),
                                node.lineno,
                                module,
                            )
                        )
    return violations


def _frontend_feature_for_path(path: Path, frontend_root: Path) -> str | None:
    feature_root = frontend_root / "features"
    try:
        relative = path.relative_to(feature_root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else None


def _frontend_feature_import(
    source: str,
    path: Path,
    frontend_root: Path,
) -> tuple[str, bool] | None:
    normalized = source.replace("\\", "/")
    parts: tuple[str, ...] | None = None
    for prefix in ("@/features/", "~/features/"):
        if normalized.startswith(prefix):
            parts = tuple(part for part in normalized[len(prefix) :].split("/") if part)
            break
    if parts is None and normalized.startswith("."):
        target = (path.parent / normalized).resolve()
        try:
            relative = target.relative_to((frontend_root / "features").resolve())
        except ValueError:
            return None
        parts = relative.parts
    if not parts:
        return None
    is_index = len(parts) == 2 and parts[1].split(".", 1)[0] == "index"
    return parts[0], not (len(parts) == 1 or is_index)


def _scan_frontend_feature_imports(root: Path) -> list[Violation]:
    frontend_root = root / "frontend" / "vue_project" / "src"
    violations: list[Violation] = []
    if not frontend_root.is_dir():
        return violations
    for path in sorted(item for item in frontend_root.rglob("*") if item.is_file()):
        if path.suffix.lower() not in FRONTEND_SOURCE_SUFFIXES:
            continue
        try:
            source_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BoundaryCheckError(f"cannot read {path}: {exc}") from exc
        importer_feature = _frontend_feature_for_path(path, frontend_root)
        for match in IMPORT_SOURCE_RE.finditer(source_text):
            source = match.group("source")
            imported = _frontend_feature_import(source, path, frontend_root)
            if imported is None:
                continue
            target_feature, is_deep = imported
            if is_deep and target_feature != importer_feature:
                violations.append(
                    Violation(
                        RULE_FRONTEND_FEATURE_PUBLIC_API,
                        _relative(path, root),
                        source_text.count("\n", 0, match.start()) + 1,
                        source,
                    )
                )
    return violations


def scan_repository(root: Path) -> list[Violation]:
    root = root.resolve()
    violations: list[Violation] = []
    violations.extend(
        _scan_for_forbidden_imports(
            root,
            root / "backend" / "api" / "core",
            RULE_CORE_TO_SERVICES,
            _module_is_services,
        )
    )
    violations.extend(
        _scan_for_forbidden_imports(
            root,
            root / "core_pipeline",
            RULE_PIPELINE_TO_SCRIPTS,
            _module_is_scripts,
        )
    )
    violations.extend(_scan_frontend_shared(root))
    violations.extend(_scan_backend_feature_imports(root))
    violations.extend(_scan_frontend_feature_imports(root))
    violations.extend(_scan_route_dotenv(root))
    violations.extend(_scan_direct_environment_reads(root))
    return sorted(violations)


def counts_by_rule_and_path(violations: Iterable[Violation]) -> dict[str, dict[str, int]]:
    counts = Counter((item.rule, item.path) for item in violations)
    result: dict[str, dict[str, int]] = {}
    for rule in RULES:
        paths = {
            path: count
            for (candidate_rule, path), count in sorted(counts.items())
            if candidate_rule == rule
        }
        result[rule] = paths
    return result


def baseline_payload(violations: Iterable[Violation]) -> dict:
    return {
        "schema_version": 1,
        "policy": "per-file ceilings; current counts may only stay equal or decrease",
        "rules": counts_by_rule_and_path(violations),
    }


def write_baseline(path: Path, violations: Iterable[Violation]) -> None:
    violation_list = list(violations)
    if path.is_file():
        existing = load_baseline(path)
        result = compare_to_baseline(violation_list, existing)
        if result.new_debt:
            raise BoundaryCheckError(
                "refusing to increase boundary debt while updating the baseline"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline_payload(violation_list), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_baseline(path: Path) -> dict[str, dict[str, int]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoundaryCheckError(f"cannot read baseline {path}: {exc}") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("rules"), dict):
        raise BoundaryCheckError("baseline must have schema_version=1 and a rules object")

    result: dict[str, dict[str, int]] = {}
    for rule in RULES:
        raw_paths = payload["rules"].get(rule, {})
        if not isinstance(raw_paths, dict):
            raise BoundaryCheckError(f"baseline rule {rule!r} must be an object")
        parsed: dict[str, int] = {}
        for path, count in raw_paths.items():
            if not isinstance(path, str) or not isinstance(count, int) or count < 0:
                raise BoundaryCheckError(
                    f"baseline entry {rule}:{path!r} must be a non-negative integer"
                )
            parsed[path] = count
        result[rule] = parsed
    return result


def compare_to_baseline(
    violations: Iterable[Violation], baseline: dict[str, dict[str, int]]
) -> RatchetResult:
    current = counts_by_rule_and_path(violations)
    new_debt: dict[str, dict[str, int]] = {}
    resolved_debt: dict[str, dict[str, int]] = {}
    for rule in RULES:
        current_paths = current.get(rule, {})
        baseline_paths = baseline.get(rule, {})
        for path in sorted(set(current_paths) | set(baseline_paths)):
            delta = current_paths.get(path, 0) - baseline_paths.get(path, 0)
            if delta > 0:
                new_debt.setdefault(rule, {})[path] = delta
            elif delta < 0:
                resolved_debt.setdefault(rule, {})[path] = -delta
    return RatchetResult(new_debt, resolved_debt, current)


def _text_report(result: RatchetResult, violations: Sequence[Violation]) -> str:
    current_total = sum(sum(paths.values()) for paths in result.current_counts.values())
    resolved_total = sum(sum(paths.values()) for paths in result.resolved_debt.values())
    if result.new_debt:
        status = "FAIL"
    elif result.resolved_debt:
        status = "BASELINE UPDATE REQUIRED"
    else:
        status = "PASS"
    lines = [
        f"import boundary ratchet: {status}",
        f"current debt: {current_total}; resolved since baseline: {resolved_total}",
    ]
    if result.new_debt:
        lines.append("new boundary debt:")
        violation_lookup: dict[tuple[str, str], list[Violation]] = {}
        for item in violations:
            violation_lookup.setdefault((item.rule, item.path), []).append(item)
        for rule, paths in sorted(result.new_debt.items()):
            lines.append(f"  {rule}: {RULE_DESCRIPTIONS[rule]}")
            for path, count in sorted(paths.items()):
                details = violation_lookup.get((rule, path), [])
                examples = "; ".join(
                    f"line {item.line} ({item.detail})" for item in details[-count:]
                )
                lines.append(f"    {path}: +{count}" + (f"; {examples}" if examples else ""))
    elif result.resolved_debt:
        lines.append(
            "boundary debt decreased; run --write-baseline to persist the lower ceilings"
        )
        for rule, paths in sorted(result.resolved_debt.items()):
            for path, count in sorted(paths.items()):
                lines.append(f"  {rule}: {path}: -{count}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="replace the selected baseline with current per-file ceilings",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        violations = scan_repository(args.root)
        if args.write_baseline:
            write_baseline(args.baseline, violations)
            print(f"wrote {args.baseline} with {len(violations)} existing violations")
            return 0
        baseline = load_baseline(args.baseline)
        result = compare_to_baseline(violations, baseline)
    except BoundaryCheckError as exc:
        print(f"boundary check error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        if result.new_debt:
            status = "failed"
        elif result.resolved_debt:
            status = "baseline_update_required"
        else:
            status = "passed"
        print(
            json.dumps(
                {
                    "status": status,
                    "new_debt": result.new_debt,
                    "resolved_debt": result.resolved_debt,
                    "current_counts": result.current_counts,
                    "violations": [asdict(item) for item in violations],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_text_report(result, violations))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
