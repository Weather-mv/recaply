"""
storage.py — keeps recaps, clips, reels and editor projects in Cloudflare R2
(or any S3-compatible bucket) instead of only on this server's disk.

The data folder becomes a cache that mirrors the bucket: the file
<data dir>/<path> is stored as the object <path>. ffmpeg and the detectors
need real files, so work still happens on disk; finished files are uploaded
in the background and browsers fetch videos straight from the bucket with
short-lived signed links (the app checks who's asking first). Videos that
have been uploaded and not used for a while are removed from the disk and
fetched back when they're needed again. Small files (recap and project data,
pictures, posters, transcripts) stay mirrored both ways, so a new server
starts with everything. The accounts database is backed up to the bucket
every few minutes and restored on a server that doesn't have it.

Without bucket settings everything stays on disk, as before.
"""

import mimetypes
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from config import CLIPS_DIR, env_int

ACCOUNT_ID = os.environ.get("RECAPLY_R2_ACCOUNT_ID", "").strip()
ENDPOINT = (os.environ.get("RECAPLY_S3_ENDPOINT", "").strip().rstrip("/")
            or (f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com" if ACCOUNT_ID else ""))
BUCKET = os.environ.get("RECAPLY_S3_BUCKET", "").strip()
ACCESS_KEY = os.environ.get("RECAPLY_S3_ACCESS_KEY", "").strip()
SECRET_KEY = os.environ.get("RECAPLY_S3_SECRET_KEY", "").strip()
REGION = os.environ.get("RECAPLY_S3_REGION", "auto").strip() or "auto"
ENABLED = bool(ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY)

CACHE_GB = max(0, env_int("RECAPLY_CACHE_GB", 5))         # videos kept on disk at most
CACHE_HOURS = max(0, env_int("RECAPLY_CACHE_HOURS", 2))   # ...and only while used this recently
LINK_SEC = 6 * 3600                                       # signed links outlive a long editing session
BACKUP_EVERY = 300                                        # seconds between database backups (when changed)

# Only these leave the disk; everything else is small and stays mirrored.
CACHED = (".mp4",)
# Scratch files that are never uploaded.
SKIP_DIRS = ("_zips",)
SKIP_ENDS = (".part", ".part.mp4", ".tmp", ".orig", ".conv.mp4", ".ytdl", ".bin", ".raw", ".zip",
             ".db-wal", ".db-shm", ".db-journal")
SKIP_NAMES = ("recaply.db", ".secret_key")
SKIP_STARTS = ("import.", "live_", "audio_", "analysis_", "names_")
SYSTEM = "_system/"                                       # database backups and the session key

_client = None
_remote = {}                 # key -> size, for every object known to be in the bucket
_remote_lock = threading.Lock()
_pending = {}                # key -> bytes or a file path, waiting to upload (the latest wins)
_running = set()
_queue_lock = threading.Lock()
_fetch_locks = {}
_last_use = {}               # local path -> time it was last needed
_pool = ThreadPoolExecutor(max_workers=env_int("RECAPLY_UPLOAD_WORKERS", 4), thread_name_prefix="r2")


def client():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client(
            "s3", endpoint_url=ENDPOINT, region_name=REGION,
            aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 5, "mode": "standard"},
                          max_pool_connections=32,
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"))
    return _client


def _missing(e):
    """True when an S3 error just means the object isn't there."""
    code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


def origin():
    """The bucket's address, for the pages' Content-Security-Policy."""
    return ENDPOINT if ENABLED else ""


def key_for(path):
    rel = os.path.relpath(os.path.abspath(path), CLIPS_DIR)
    if rel.startswith("..") or os.path.isabs(rel):
        raise ValueError(f"{path} is outside the data folder")
    return rel.replace(os.sep, "/")


def path_for(key):
    return os.path.join(CLIPS_DIR, *key.split("/"))


def skipped(key):
    parts = key.split("/")
    name = parts[-1]
    return (key.startswith(SYSTEM) or parts[0] in SKIP_DIRS or name in SKIP_NAMES
            or name.endswith(SKIP_ENDS) or name.startswith(SKIP_STARTS))


def uploaded(path):
    """True when the bucket has this file as it is on disk (or the disk copy is gone)."""
    try:
        key = key_for(path)
    except ValueError:
        return False
    with _remote_lock:
        size = _remote.get(key)
    if size is None or key in _pending or key in _running:
        return False
    try:
        return os.path.getsize(path) == size
    except OSError:
        return True


# ---------- uploads (background, the latest version of a file wins) ----------

def upload(path):
    """Upload a finished file in the background."""
    if not ENABLED:
        return
    try:
        key = key_for(path)
    except ValueError:
        return
    if not skipped(key):
        _enqueue(key, path)


def put_bytes(path, data):
    """Upload small data that was just written to `path` (job and project files)."""
    if ENABLED:
        _enqueue(key_for(path), data.encode("utf-8") if isinstance(data, str) else data)


def _enqueue(key, src):
    with _queue_lock:
        _pending[key] = src
        if key in _running:
            return               # the running upload sends it again when it's done
        _running.add(key)
    _pool.submit(_upload_loop, key)


def _upload_loop(key):
    while True:
        with _queue_lock:
            src = _pending.pop(key, None)
            if src is None:
                _running.discard(key)
                return
        try:
            _upload_one(key, src)
        except Exception:  # noqa: BLE001 — retried by the next sweep
            print(f"[storage] upload failed for {key}:")
            traceback.print_exc()


def _upload_one(key, src):
    ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
    if isinstance(src, bytes):
        client().put_object(Bucket=BUCKET, Key=key, Body=src, ContentType=ctype)
        size = len(src)
    else:
        if not os.path.exists(src):
            return
        size = os.path.getsize(src)
        client().upload_file(src, BUCKET, key, ExtraArgs={"ContentType": ctype})
        if os.path.getsize(src) != size:       # changed while uploading: send it again
            _enqueue(key, src)
    with _remote_lock:
        _remote[key] = size


def flush(timeout=60):
    """Wait for queued uploads (used by tests and at shutdown)."""
    end = time.time() + timeout
    while time.time() < end:
        with _queue_lock:
            if not _running and not _pending:
                return True
        time.sleep(0.1)
    return False


# ---------- reading ----------

def fetch(path):
    """Make sure `path` is on disk, downloading it from the bucket if needed. False if it's nowhere."""
    _last_use[path] = time.time()
    if os.path.exists(path):
        return True
    if not ENABLED:
        return False
    try:
        key = key_for(path)
    except ValueError:
        return False
    lock = _fetch_locks.setdefault(key, threading.Lock())
    with lock:
        if os.path.exists(path):
            return True
        os.makedirs(os.path.dirname(path), exist_ok=True)
        part = f"{path}.{threading.get_ident()}.part"
        try:
            client().download_file(BUCKET, key, part)
            os.replace(part, path)
        except Exception as e:  # noqa: BLE001
            if os.path.exists(part):
                os.remove(part)
            if not _missing(e):
                print(f"[storage] fetching {key} failed: {e}")
            return False
    with _remote_lock:
        _remote[key] = os.path.getsize(path)
    return True


def link(path, download_name=None):
    """A short-lived signed link to the file in the bucket, or None to serve it from disk."""
    if not ENABLED or not uploaded(path):
        return None
    params = {"Bucket": BUCKET, "Key": key_for(path)}
    if download_name:
        safe = download_name.replace('"', "").replace("\\", "")
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
    _last_use.setdefault(path, time.time())
    return client().generate_presigned_url("get_object", Params=params, ExpiresIn=LINK_SEC)


# ---------- deleting ----------

def delete(path):
    """Delete a file from the disk and the bucket."""
    try:
        os.remove(path)
    except OSError:
        pass
    if ENABLED:
        key = key_for(path)
        with _remote_lock:
            _remote.pop(key, None)
        _pool.submit(_delete_keys, [key])


def delete_tree(folder):
    """Delete a folder (a recap or a project) from the disk and the bucket."""
    shutil.rmtree(folder, ignore_errors=True)
    if not ENABLED:
        return
    prefix = key_for(folder).rstrip("/") + "/"
    with _remote_lock:
        keys = [k for k in _remote if k.startswith(prefix)]
        for k in keys:
            _remote.pop(k, None)
    _pool.submit(_delete_prefix, prefix, keys)


def _delete_keys(keys):
    for i in range(0, len(keys), 1000):
        try:
            client().delete_objects(Bucket=BUCKET, Delete={
                "Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def _delete_prefix(prefix, known):
    keys = set(known)
    try:
        for page in client().get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
            keys.update(o["Key"] for o in page.get("Contents", []))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    _delete_keys(sorted(keys))


# ---------- start-up: restore, mirror, migrate ----------

def init(db_path):
    """Before the app opens its database: restore it (and the session key) if this
    server doesn't have them, download the small files, then upload anything
    only this disk has. Videos are left in the bucket until they're needed."""
    if not ENABLED:
        return
    t = time.time()
    os.makedirs(CLIPS_DIR, exist_ok=True)
    for name, dst in (("recaply.db", db_path), (".secret_key", os.path.join(CLIPS_DIR, ".secret_key"))):
        if not os.path.exists(dst):
            try:
                client().download_file(BUCKET, SYSTEM + name, dst + ".part")
                os.replace(dst + ".part", dst)
                print(f"[storage] restored {name} from the bucket")
            except Exception as e:  # noqa: BLE001 — a new install has none
                if os.path.exists(dst + ".part"):
                    os.remove(dst + ".part")
                if not _missing(e):
                    raise RuntimeError(f"Can't reach the storage bucket: {e}") from e
    listed = {}
    for page in client().get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for o in page.get("Contents", []):
            listed[o["Key"]] = o["Size"]
    with _remote_lock:
        _remote.update(listed)
    got = 0
    for key, size in listed.items():
        if key.startswith(SYSTEM) or key.lower().endswith(CACHED):
            continue
        dst = path_for(key)
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            client().download_file(BUCKET, key, dst + ".part")
            os.replace(dst + ".part", dst)
            got += 1
    print(f"[storage] bucket {BUCKET}: {len(listed)} files, {got} downloaded, in {time.time() - t:.1f}s")
    threading.Thread(target=_background, args=(db_path,), daemon=True, name="storage").start()


def sweep():
    """Upload whatever is on disk but not in the bucket (first run, or after a failed upload)."""
    n = 0
    for root, dirs, files in os.walk(CLIPS_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = os.path.join(root, name)
            key = key_for(path)
            if skipped(key):
                continue
            with _remote_lock:
                size = _remote.get(key)
            try:
                if size == os.path.getsize(path) or time.time() - os.path.getmtime(path) < 60:
                    continue        # already there, or still being written
            except OSError:
                continue
            upload(path)
            n += 1
    return n


def evict():
    """Remove uploaded videos that haven't been used lately, oldest first, while the cache is too big."""
    now = time.time()
    vids = []
    for root, dirs, files in os.walk(CLIPS_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.lower().endswith(CACHED):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if skipped(key_for(path)):
                continue
            vids.append((max(_last_use.get(path, 0), st.st_mtime), st.st_size, path))
    vids.sort()
    total = sum(v[1] for v in vids)
    freed = 0
    for used, size, path in vids:
        if not uploaded(path):
            continue
        if now - used < CACHE_HOURS * 3600 and total <= CACHE_GB * 1e9:
            continue
        try:
            os.remove(path)        # fails harmlessly on Windows while ffmpeg has it open
        except OSError:
            continue
        _last_use.pop(path, None)
        total -= size
        freed += size
    return freed


def backup_db(db_path, daily=False):
    """Copy the live database (safely, with SQLite's backup API) to the bucket."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        client().upload_file(tmp, BUCKET, SYSTEM + "recaply.db")
        if daily:
            client().upload_file(tmp, BUCKET, SYSTEM + f"backups/recaply-{time.strftime('%Y-%m-%d')}.db")
    finally:
        os.remove(tmp)
    key_path = os.path.join(CLIPS_DIR, ".secret_key")
    if os.path.exists(key_path) and not os.environ.get("RECAPLY_SECRET_KEY"):
        with open(key_path, "rb") as f:
            client().put_object(Bucket=BUCKET, Key=SYSTEM + ".secret_key", Body=f.read())


def _db_changed(db_path, last):
    stamp = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in (db_path, db_path + "-wal"))
    return stamp != last, stamp


def _background(db_path):
    last_stamp, last_day, last_sweep = None, None, 0
    while True:
        try:
            if time.time() - last_sweep > 600:
                last_sweep = time.time()
                n = sweep()
                if n:
                    print(f"[storage] uploading {n} files")
                freed = evict()
                if freed:
                    print(f"[storage] freed {freed / 1e9:.2f} GB of cached videos")
            changed, stamp = _db_changed(db_path, last_stamp)
            day = time.strftime("%Y-%m-%d")
            if os.path.exists(db_path) and (changed or day != last_day):
                backup_db(db_path, daily=day != last_day)
                last_stamp, last_day = stamp, day
        except Exception:  # noqa: BLE001 — try again next round
            traceback.print_exc()
        time.sleep(BACKUP_EVERY)
