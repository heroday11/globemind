"""Compatibility alias for :mod:`runtime_control.inspection`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.inspection")
sys.modules[__name__] = _implementation
