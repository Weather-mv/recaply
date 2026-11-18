"""
Per-stage disk cache keyed by content.

Every stage's output is stored under
`<cache_dir>/<source key>/<stage>-<params hash>.<ext>`, so re-running a job
with different settings (another clip length, style or aspect ratio) reuses
the download, the signals and the transcripts instead of repeating them.

The source key comes from `recaply.ingest.sources` (the platform's video id,
or a hash of a local file's size and sampled bytes).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from typing import Any, Callable

import numpy as np

from recaply.settings import settings

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def params_hash(params: Any) -> str:
    """Stable short hash of JSON-serialisable parameters."""
    blob = json.dumps(params, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def safe_name(text: str, limit: int = 80) -> str:
    """A filesystem-safe version of `text` (no separators, no traversal)."""
    s = _SAFE.sub("_", text).strip("._") or "x"
    return s[:limit]


class StageCache:
    """Cache folder for one source. Writes are atomic (temp file + rename)."""

    def __init__(self, source_key: str, root: str | None = None):
        self.dir = os.path.join(root or settings.cache_dir, safe_name(source_key))
        os.makedirs(self.dir, exist_ok=True)

    def path(self, stage: str, params: Any = None, ext: str = "json") -> str:
        """Where `stage` with `params` is (or would be) stored."""
        suffix = f"-{params_hash(params)}" if params is not None else ""
        return os.path.join(self.dir, f"{safe_name(stage)}{suffix}.{ext}")

    # ----- JSON -----
    def get_json(self, stage: str, params: Any = None):
        p = self.path(stage, params, "json")
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def put_json(self, stage: str, value: Any, params: Any = None) -> str:
        p = self.path(stage, params, "json")
        _atomic_write(p, json.dumps(value, default=str).encode("utf-8"))
        return p

    # ----- numpy arrays -----
    def get_array(self, stage: str, params: Any = None):
        p = self.path(stage, params, "npy")
        if not os.path.exists(p):
            return None
        try:
            return np.load(p, allow_pickle=False)
        except (OSError, ValueError):
            return None

    def put_array(self, stage: str, value: np.ndarray, params: Any = None) -> str:
        p = self.path(stage, params, "npy")
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".npy.tmp")
        with os.fdopen(fd, "wb") as f:
            np.save(f, np.asarray(value), allow_pickle=False)
        os.replace(tmp, p)
        return p

    # ----- memoize helpers -----
    def json(self, stage: str, params: Any, compute: Callable[[], Any]):
        """Return the cached JSON value, computing and storing it on a miss."""
        hit = self.get_json(stage, params)
        if hit is not None:
            return hit
        value = compute()
        self.put_json(stage, value, params)
        return value

    def array(self, stage: str, params: Any, compute: Callable[[], np.ndarray]) -> np.ndarray:
        hit = self.get_array(stage, params)
        if hit is not None:
            return hit
        value = compute()
        self.put_array(stage, value, params)
        return value


def _atomic_write(path: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
