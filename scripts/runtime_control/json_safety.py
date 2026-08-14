"""Compatibility alias for :mod:`runtime_control.json_safety`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.json_safety")
sys.modules[__name__] = _implementation
