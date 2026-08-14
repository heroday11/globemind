from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_discovers_all_runtime_package_roots() -> None:
    import tomllib

    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_dir = payload["tool"]["setuptools"]["package-dir"]
    include = set(payload["tool"]["setuptools"]["packages"]["find"]["include"])

    assert payload["project"]["version"] == (ROOT / "VERSION").read_text(encoding="ascii").strip()
    assert package_dir["api"] == "backend/api"
    assert package_dir["agentic_rag"] == "backend/agentic_rag"
    assert package_dir["runtime_control"] == "backend/runtime_control"
    assert "cc_integration" in payload["tool"]["setuptools"]["py-modules"]
    assert {"api*", "agentic_rag*", "runtime_control*", "core_pipeline*", "config*"} <= include


def test_production_package_sources_do_not_mutate_sys_path() -> None:
    roots = (
        ROOT / "backend" / "agentic_rag",
        ROOT / "backend" / "ai_search",
        ROOT / "core_pipeline",
    )
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            assert not any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys"
                and node.func.value.attr == "path"
                and node.func.attr in {"insert", "append"}
                for node in ast.walk(tree)
            ), path


def test_settings_source_has_safe_defaults_and_clean_utf8() -> None:
    source = (ROOT / "config" / "settings.py").read_bytes()
    assert not source.startswith(b"\xef\xbb\xbf")
    text = source.decode("utf-8")
    assert 'default=""' in text[text.index("pg_password"): text.index("pg_database")]
    assert "/root/data/models/" not in text
    assert "鍗" not in text
    assert "南海" in text


def test_event_pipeline_defers_credentials_and_uses_portable_defaults() -> None:
    path = ROOT / "scripts" / "run_event_level_pipeline.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    top_level_calls = [
        child
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    ]
    assert not any(
        isinstance(call.func, ast.Name)
        and call.func.id == "require_database_password"
        for call in top_level_calls
    )
    assert "192.168.207.171" not in text
    assert "/root/data/models/" not in text


def test_story_image_helpers_do_not_import_optional_image_runtime_eagerly() -> None:
    path = ROOT / "scripts" / "backfill_story_images.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    top_level_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "PIL" not in top_level_imports
    assert "ImageFile" not in top_level_imports
    assert "192.168.207.171" not in text
