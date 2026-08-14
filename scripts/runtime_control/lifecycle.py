"""Compatibility alias for :mod:`runtime_control.lifecycle`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.lifecycle")
sys.modules[__name__] = _implementation
