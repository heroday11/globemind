"""Compatibility alias for :mod:`runtime_control.redaction`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.redaction")
sys.modules[__name__] = _implementation
