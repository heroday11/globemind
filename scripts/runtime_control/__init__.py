"""Compatibility facade for the canonical backend runtime-control package."""

from importlib import import_module

_implementation = import_module("runtime_control")
__all__ = _implementation.__all__


def __getattr__(name: str):
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
