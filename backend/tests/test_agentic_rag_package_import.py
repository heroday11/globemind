from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes | None]]:
    snapshot: dict[str, tuple[str, int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_file():
            snapshot[relative] = ("file", mode, path.read_bytes())
        elif path.is_dir():
            snapshot[relative] = ("directory", mode, None)
    return snapshot


def test_package_import_does_not_write_to_immutable_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    package_dir = release / "backend" / "agentic_rag"
    package_dir.mkdir(parents=True)
    package_init = package_dir / "__init__.py"
    package_init.write_bytes(
        (PROJECT_ROOT / "backend" / "agentic_rag" / "__init__.py").read_bytes()
    )

    package_init.chmod(0o444)
    for directory in (package_dir, package_dir.parent, release):
        directory.chmod(0o555)
    before = _tree_snapshot(release)

    code = f"""
import pathlib
import sys
sys.dont_write_bytecode = True
sys.modules["config"] = None
sys.path.insert(0, {str(release / "backend")!r})
import agentic_rag
assert agentic_rag.DATA_DIR == pathlib.Path(agentic_rag.__file__).parent / "data"
assert agentic_rag.VAULT_DIR == pathlib.Path(agentic_rag.__file__).parent / "obsidian_vault"
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert _tree_snapshot(release) == before
    assert not (package_dir / "data").exists()
    assert not (package_dir / "obsidian_vault").exists()
