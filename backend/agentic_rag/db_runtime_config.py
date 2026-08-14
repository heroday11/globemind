"""Expose the repository database credential resolver to agentic_rag modules."""
from __future__ import annotations

from importlib import import_module

require_database_password = import_module(
    "config.db_runtime_config"
).require_database_password


__all__ = ["require_database_password"]
