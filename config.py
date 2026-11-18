"""
config.py — settings shared by every module, read once from environment
variables (see .env.example) so the same code runs locally and on a server.

Recaply runs in one of two roles:
  * desktop (desktop.py): the Windows app. Recaps, clips, reels and the editor
    run on the user's own PC; accounts and plans come from the online service.
  * cloud (cloud.py): the online service: the website, accounts, plans,
    payments and the small API the Windows app logs in through.
"""

import os
import sys

FROZEN = getattr(sys, "frozen", False)            # running from the installed Recaply.exe
BASE_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def load_env_file(path=os.path.join(BASE_DIR, ".env")):
    """Read KEY=value lines from .env (when running without Docker). Variables
    already set in the environment win."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


load_env_file()
if FROZEN:
    load_env_file(os.path.join(BASE_DIR, "app.env"))     # written by build/build.ps1 (the online service's address)


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def env_flag(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


DESKTOP = env_flag("RECAPLY_DESKTOP", FROZEN)
# The installed app keeps everything in the user's profile; running from source uses ./clips.
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Recaply")
CLIPS_DIR = os.path.abspath(os.environ.get("RECAPLY_DATA_DIR") or (
    os.path.join(APP_DIR, "Library") if FROZEN else os.path.join(BASE_DIR, "clips")))
os.makedirs(CLIPS_DIR, exist_ok=True)

# The online service the Windows app logs in to (accounts, plans, payments).
CLOUD_URL = (os.environ.get("RECAPLY_CLOUD_URL") or "http://127.0.0.1:8000").rstrip("/")
VERSION = "2.1.0"
# The app also answers the web app (the website's /app) on the first free one of these
# ports on 127.0.0.1, so the website can find it. Only the website's own pages may use it.
HELPER_PORTS = (47613, 47614, 47615)

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = env_int("PORT", 5000)
# Only reachable from this computer: no payment provider is needed to try plans.
LOCAL_ONLY = HOST in ("127.0.0.1", "localhost", "::1")

# Hosts a VOD link may point at. "*" allows any site yt-dlp supports.
ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get(
    "RECAPLY_ALLOWED_HOSTS", "youtube.com,youtu.be,twitch.tv").split(",") if h.strip()]

# Public address of the site (for payment return links); guessed from the request when empty.
PUBLIC_URL = os.environ.get("RECAPLY_PUBLIC_URL", "").rstrip("/")
# Send the session cookie over HTTPS only (turn on behind TLS).
SECURE_COOKIES = env_flag("RECAPLY_SECURE_COOKIES", False)
