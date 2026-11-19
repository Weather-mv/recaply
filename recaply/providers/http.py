"""
Tiny HTTP helpers for the API providers (stdlib only, no new dependencies):
JSON and multipart POSTs with retries on network errors, 429 and 5xx
(honouring Retry-After). Error messages never include request headers, so
API keys can't leak into logs or job records.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid


class ProviderError(RuntimeError):
    """A provider call failed for good (after retries)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _send(req: urllib.request.Request, timeout: float, retries: int) -> bytes:
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:400].decode("utf-8", "replace")
            retryable = e.code == 429 or e.code >= 500
            if not retryable or attempt == retries:
                raise ProviderError(f"HTTP {e.code}: {body}", e.code, retryable) from None
            wait = e.headers.get("Retry-After")
            try:
                delay = min(60.0, max(delay, float(wait))) if wait else delay
            except ValueError:
                pass
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise ProviderError(f"network error: {getattr(e, 'reason', e)}", None, True) from None
        time.sleep(delay)
        delay = min(60.0, delay * 2)
    raise ProviderError("unreachable")


def post_json(url: str, body: dict, headers: dict | None = None, timeout: float = 120,
              retries: int = 3, parse: bool = True) -> dict:
    """POST JSON. With parse=False the reply is ignored (webhooks often answer 204 with no body)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    raw = _send(req, timeout, retries)
    if not parse:
        return {}
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ProviderError(f"bad JSON from provider: {raw[:200]!r}") from e


def post_multipart(url: str, fields: dict, files: dict[str, tuple[str, bytes, str]],
                   headers: dict | None = None, timeout: float = 300, retries: int = 3) -> dict:
    """`fields` values may be lists (repeated fields); `files` maps name -> (filename, bytes, mime)."""
    boundary = "----recaply" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{item}\r\n'.encode())
    for k, (fname, blob, mime) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                     f"Content-Type: {mime}\r\n\r\n".encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})})
    raw = _send(req, timeout, retries)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ProviderError(f"bad JSON from provider: {raw[:200]!r}") from e


def get_json(url: str, headers: dict | None = None, timeout: float = 30, retries: int = 2) -> dict | list:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json", **(headers or {})})
    raw = _send(req, timeout, retries)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ProviderError(f"bad JSON from {url.split('?')[0]}: {raw[:120]!r}") from e
