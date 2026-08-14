"""Compatibility alias for :mod:`runtime_control.cli`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.cli")
sys.modules[__name__] = _implementation
