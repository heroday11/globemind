"""Unified FastAPI application package (Phase 2)."""

# Configuration must be loaded before importing db/auth modules because those
# modules derive immutable settings at import time.
from api.core.environment import discard_plaintext_database_environment, load_environment

load_environment()
discard_plaintext_database_environment()
