"""Compatibility alias for the shared database runtime configuration."""
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Return the implementation module itself so legacy monkeypatches of constants,
# caches, or OS primitives still affect the single shared source of truth.
sys.modules[__name__] = import_module("config.db_runtime_config")
