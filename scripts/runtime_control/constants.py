"""Shared constants for the runtime control plane."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "ops" / "runtime" / "services.json"

SCHEMA_VERSION = 2
DESTRUCTIVE_COMMANDS = frozenset({"adopt", "kill", "restart", "start", "stop"})
SAFE_COMMANDS = frozenset({"catalog", "doctor", "list", "status"})
LIFECYCLE_COMMANDS = frozenset({"restart", "start", "status", "stop"})
REDACTED = "[REDACTED]"
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# Only explicit, successful terminal states can establish pipeline completion.
SAFE_COMPLETE_VALUES = frozenset({"complete", "completed", "succeeded", "success"})
