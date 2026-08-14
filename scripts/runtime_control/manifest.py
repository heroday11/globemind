"""Compatibility alias for :mod:`runtime_control.manifest`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.manifest")
sys.modules[__name__] = _implementation
