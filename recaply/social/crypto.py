"""
Encrypt sign-in tokens before they are written to disk.

On Windows (the desktop app) this uses DPAPI, so only the same Windows user
on the same PC can read them back, and there is no key file to steal. On
other systems it uses Fernet from the `cryptography` package with a key kept
in `<data dir>/.social_key` (private to the user). If neither is available
the token is stored base64-encoded and a warning is logged once.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys

from recaply.log import get_logger

log = get_logger("social.crypto")
_ENTROPY = b"clipforge-social-v1"
_warned = {"plain": False}


# ---------- Windows DPAPI ----------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    b = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    b._buf = buf                     # keep the buffer alive as long as the blob
    return b


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, ent, out = _blob(data), _blob(_ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))   # 0x1 = no UI
    if not ok:
        raise ValueError("DPAPI could not " + ("encrypt" if protect else "decrypt") + " the token")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ---------- Fernet (other systems) ----------

def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    from recaply.settings import settings
    path = os.path.join(settings.data_dir, ".social_key")
    try:
        with open(path, "rb") as f:
            key = f.read().strip()
    except OSError:
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    return Fernet(key)


def _scheme() -> str:
    if sys.platform == "win32":
        return "dpapi"
    return "fernet" if _fernet() is not None else "plain"


def encrypt(text: str) -> str:
    """'<scheme>:<base64>' so a token can always be read back with the scheme it was written with."""
    data = text.encode("utf-8")
    scheme = _scheme()
    if scheme == "dpapi":
        return "dpapi:" + base64.b64encode(_dpapi(data, True)).decode()
    if scheme == "fernet":
        return "fernet:" + _fernet().encrypt(data).decode()
    if not _warned["plain"]:
        _warned["plain"] = True
        log.warning("tokens are stored unencrypted: install the 'cryptography' package")
    return "plain:" + base64.b64encode(data).decode()


def decrypt(value: str) -> str:
    scheme, _, body = (value or "").partition(":")
    if scheme == "dpapi":
        return _dpapi(base64.b64decode(body), False).decode("utf-8")
    if scheme == "fernet":
        f = _fernet()
        if f is None:
            raise ValueError("this token needs the 'cryptography' package")
        return f.decrypt(body.encode()).decode("utf-8")
    if scheme == "plain":
        return base64.b64decode(body).decode("utf-8")
    raise ValueError("unknown token format")
