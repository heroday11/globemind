"""Compatibility alias for :mod:`runtime_control.catalog`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.catalog")
sys.modules[__name__] = _implementation
