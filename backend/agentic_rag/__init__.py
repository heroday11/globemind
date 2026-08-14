"""Top-level paths for the Agentic RAG package.

Importing the package must remain read-only.  Code that writes data or vault
content is responsible for creating its destination at the operation boundary.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
DATA_DIR = ROOT / "data"

# Lazy import: config.settings may not exist in all environments (e.g. standalone scripts, tests)
try:
    from config.settings import obsidian_vault_path  # type: ignore[import-untyped]
except (ImportError, ModuleNotFoundError):
    def obsidian_vault_path() -> Path:
        return ROOT / "obsidian_vault"
# 与 config.yaml paths.obsidian_vault 一致：仓库根下 obsidian_vault/
VAULT_DIR = obsidian_vault_path()
