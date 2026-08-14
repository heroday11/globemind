"""Expose the repository database credential resolver to agentic_rag modules."""
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

require_database_password = import_module(
    "config.db_runtime_config"
).require_database_password


__all__ = ["require_database_password"]
