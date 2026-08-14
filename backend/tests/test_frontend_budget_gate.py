from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "deploy/check_frontend_budgets.mjs"


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _fixture(tmp_path: Path, *, main_entry_bytes: int = 10) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    _write(dist / "index.html", '<script type="module" src="/assets/app.js"></script>')
    _write(dist / "assets/app.js", b"x" * main_entry_bytes)
    _write(dist / "assets/app.css", b"x" * 5)
    _write(
        dist / "fin-terminal/index.html",
        '<script type="module" src="/fin-terminal/assets/terminal.js"></script>',
    )
    _write(dist / "fin-terminal/assets/terminal.js", b"x" * 8)
    _write(dist / "fin-terminal/assets/terminal.css", b"x" * 4)
    config = tmp_path / "budgets.json"
    surface = {
        "assets_path": "assets",
        "index_path": "index.html",
        "entry_prefix": "/assets/",
        "max_asset_files": 3,
        "max_total_asset_bytes": 30,
        "max_total_js_bytes": 20,
        "max_total_css_bytes": 10,
        "max_single_asset_bytes": 20,
        "max_entry_js_bytes": 20,
    }
    financial = {
        **surface,
        "assets_path": "fin-terminal/assets",
        "index_path": "fin-terminal/index.html",
        "entry_prefix": "/fin-terminal/assets/",
    }
    _write(
        config,
        json.dumps({"schema_version": 1, "main": surface, "financial": financial}),
    )
    return dist, config


def _run(dist: Path, config: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "node",
            str(CHECKER),
            "--dist",
            str(dist),
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_frontend_budget_gate_records_passing_metrics(tmp_path: Path) -> None:
    dist, config = _fixture(tmp_path)
    output = tmp_path / "result.json"

    result = _run(dist, config, output)

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["surfaces"]["main"]["metrics"]["entry_js"]["bytes"] == 10
    assert payload["failures"] == []


def test_frontend_budget_gate_ignores_inline_probes_and_legacy_entries(
    tmp_path: Path,
) -> None:
    dist, config = _fixture(tmp_path)
    _write(
        dist / "index.html",
        """
        <script data-cfasync="false" type="module" src="/assets/app.js"></script>
        <script data-cfasync="false" type="module">window.modern = true</script>
        <script data-cfasync="false" nomodule src="/assets/polyfills-legacy-hash.js"></script>
        """,
    )
    _write(dist / "assets/polyfills-legacy-hash.js", b"x")
    output = tmp_path / "result.json"

    result = _run(dist, config, output)

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["surfaces"]["main"]["metrics"]["entry_js"]["path"] == "assets/app.js"
    assert payload["surfaces"]["main"]["metrics"]["asset_files"] == 2
    assert payload["surfaces"]["main"]["metrics"]["legacy_js"] == {
        "asset_files": 1,
        "total_bytes": 1,
    }


def test_frontend_budget_gate_fails_when_entry_exceeds_budget(tmp_path: Path) -> None:
    dist, config = _fixture(tmp_path, main_entry_bytes=21)
    output = tmp_path / "result.json"

    result = _run(dist, config, output)

    assert result.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert {item["metric"] for item in payload["failures"]} >= {
        "entry_js_bytes",
        "single_asset_bytes",
    }


def test_frontend_budget_gate_rejects_asset_symlinks(tmp_path: Path) -> None:
    dist, config = _fixture(tmp_path)
    (dist / "assets/link.js").symlink_to(dist / "assets/app.js")

    result = _run(dist, config, tmp_path / "result.json")

    assert result.returncode == 2
    assert "symlink" in result.stderr
