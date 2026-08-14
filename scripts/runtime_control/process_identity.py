"""Compatibility alias for :mod:`runtime_control.process_identity`."""

import sys
from importlib import import_module

_implementation = import_module("runtime_control.process_identity")
sys.modules[__name__] = _implementation
