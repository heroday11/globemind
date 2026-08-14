"""Compatibility alias for :mod:`runtime_control.dependency_probes`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.dependency_probes")
sys.modules[__name__] = _implementation
