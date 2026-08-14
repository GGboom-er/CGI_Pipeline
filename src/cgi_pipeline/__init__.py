"""Unified, host-safe public namespace for CGI Pipeline."""

from __future__ import annotations

import importlib
from types import ModuleType


__all__ = ["api", "client", "tools"]


def __getattr__(name: str) -> ModuleType:
    if name not in __all__:
        raise AttributeError(name)
    module = importlib.import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module
