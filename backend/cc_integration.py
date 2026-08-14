"""Composition boundary for mounting the optional CC capability.

The API imports this narrow adapter instead of reaching into ``cppt``.  The
adapter is the only module that knows where the CC implementation lives; the
implementation itself remains independently hostable.
"""

from __future__ import annotations

from cppt.cc_bridge import cc_router, configure_cc_auth

__all__ = ["cc_router", "configure_cc_auth"]
