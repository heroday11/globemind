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
