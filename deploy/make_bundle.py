"""
Pack the online service (cloud.py) into dist/recaply-cloud.zip for uploading
to a host such as PythonAnywhere.

    python deploy/make_bundle.py

SECURITY: files are added from an allowlist (the same set as the Dockerfile),
so .env, cookies.txt, the local database and clips/ can never end up in it.
"""

import os
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FILES = ["accounts.py", "auth.py", "cloud.py", "cloud_api.py", "config.py", "webutil.py", "requirements-cloud.txt"]
FOLDERS = ["templates", "static"]
NEVER = ("cookies", ".env", ".db", ".secret_key", "session.bin")
OUT = os.path.join(ROOT, "dist", "recaply-cloud.zip")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    paths = list(FILES)
    for folder in FOLDERS:
        for base, dirs, names in os.walk(os.path.join(ROOT, folder)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            paths += [os.path.relpath(os.path.join(base, n), ROOT) for n in names]
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(paths):
            if any(x in os.path.basename(rel).lower() for x in NEVER):
                raise SystemExit(f"refusing to pack {rel}")
            z.write(os.path.join(ROOT, rel), rel.replace(os.sep, "/"))
    print(f"{OUT}: {len(paths)} files, {os.path.getsize(OUT) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
