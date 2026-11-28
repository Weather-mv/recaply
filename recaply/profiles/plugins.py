"""
Game-specific code is loaded only when a profile asks for it.

Valorant's round detector and kill-feed reader pull in OpenCV, ONNX and OCR
models; a podcast or GTA job must never pay for that. Anything outside the
profile packages reaches such a module through `lazy_module`, which imports
it on first use:

    round_detector = lazy_module("recaply.profiles.valorant.round_detector")
    round_detector.scan(...)          # imported here, not at start-up

`has_plugin(name)` says whether a detector / segmenter named in a profile
(`Profile.detectors`, `Profile.segmenter`) is implemented, so the UI can
hide profiles whose plugin isn't installed.
"""

from __future__ import annotations

import importlib
import importlib.util
import threading
from types import ModuleType

# plugin name (as written in a Profile) -> module that implements it
PLUGINS: dict[str, str] = {
    "valorant_rounds": "recaply.profiles.valorant.round_detector",
    "valorant_overlay": "recaply.profiles.valorant.round_detector",
    "valorant_killfeed": "recaply.profiles.valorant.killer_id",
}


class _Lazy(ModuleType):
    """Stands in for a module until an attribute is first read."""

    def __init__(self, target: str):
        super().__init__(target)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_real", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _load(self):
        real = object.__getattribute__(self, "_real")
        if real is None:
            with object.__getattribute__(self, "_lock"):
                real = object.__getattribute__(self, "_real")
                if real is None:
                    real = importlib.import_module(object.__getattribute__(self, "_target"))
                    object.__setattr__(self, "_real", real)
        return real

    def __getattr__(self, name: str):
        return getattr(self._load(), name)

    def __dir__(self):
        return dir(self._load())


def lazy_module(target: str) -> ModuleType:
    """A module object that imports `target` the first time something on it is used."""
    return _Lazy(target)


def has_plugin(name: str) -> bool:
    """True when the detector / segmenter `name` can be imported (without running it)."""
    target = PLUGINS.get(name)
    if target is None:
        return False
    try:
        return importlib.util.find_spec(target) is not None
    except (ImportError, ValueError):
        return False


def load_plugin(name: str) -> ModuleType:
    """Import the module that implements plugin `name` (KeyError if it's unknown)."""
    return importlib.import_module(PLUGINS[name])
