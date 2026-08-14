"""Stable opinion scoring constants shared by transport and application layers."""

METHOD_VERSION = "china_stance_v6_title_context_actor_eval_20260629"

DECAY_TAU_BASE = 1.0
DECAY_TAU_SCALE = 4.0
DECAY_ALPHA = 1.5
DECAY_MAX_LAG = 45


__all__ = (
    "DECAY_ALPHA",
    "DECAY_MAX_LAG",
    "DECAY_TAU_BASE",
    "DECAY_TAU_SCALE",
    "METHOD_VERSION",
)
