"""Compatibility alias for :mod:`runtime_control.constants`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.constants")
sys.modules[__name__] = _implementation
