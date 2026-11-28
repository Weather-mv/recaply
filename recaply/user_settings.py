"""
Settings the user changes from the studio's Settings page (AI keys and provider order),
kept in `<data dir>/_user_settings.json` next to the job database and applied on top of
the environment / `.env`, with no restart.

Only a fixed list of keys is accepted. Secrets are never sent back to the browser:
`status()` says whether each one is set (and where it came from), nothing more.
"""

from __future__ import annotations

import json
import os
import threading

# env name -> (Settings field, label, is a secret)
KEYS: dict[str, tuple[str, str, bool]] = {
    "GROQ_API_KEY": ("groq_api_key", "Groq API key", True),
    "GEMINI_API_KEY": ("gemini_api_key", "Gemini API key", True),
    "ANTHROPIC_API_KEY": ("anthropic_api_key", "Claude API key", True),
    "OPENAI_API_KEY": ("openai_api_key", "OpenAI API key", True),
    "RECAPLY_PEXELS_KEY": ("pexels_key", "Pexels API key (B-roll)", True),
    "RECAPLY_LLM_ORDER": ("llm_order", "AI provider order", False),
}
MAX_LEN = 300
PROVIDER_NAMES = ("groq", "gemini", "anthropic", "claude", "openai", "ollama")
_lock = threading.Lock()
_from_ui: set[str] = set()


def _path() -> str:
    from config import CLIPS_DIR
    return os.path.join(CLIPS_DIR, "_user_settings.json")


def _read() -> dict[str, str]:
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {k: str(v).strip() for k, v in raw.items() if k in KEYS and isinstance(v, str) and v.strip()}
    # a provider order saved before it was checked (e.g. an API key pasted into that box) is ignored
    if "RECAPLY_LLM_ORDER" in out and not valid_order(out["RECAPLY_LLM_ORDER"]):
        out.pop("RECAPLY_LLM_ORDER")
    return out


def valid_order(value: str) -> bool:
    names = [p.strip().lower() for p in value.split(",") if p.strip()]
    return bool(names) and all(p in PROVIDER_NAMES for p in names)


def load_into_environment() -> None:
    """Called once before Settings is built: what the user saved in the UI wins over `.env`."""
    for k, v in _read().items():
        os.environ[k] = v
        _from_ui.add(k)


def _apply(settings) -> None:
    """Make the running process use these values (Settings is frozen, so the fields are set directly)."""
    for env, (field, _label, _secret) in KEYS.items():
        object.__setattr__(settings, field, os.environ.get(env, "").strip())


def update(settings, changes: dict) -> dict[str, str]:
    """
    Save changes ({ENV_NAME: value}); an empty string removes that setting. Returns {name: problem}
    for rejected entries (the rest is saved). Unknown names are ignored.
    """
    problems: dict[str, str] = {}
    with _lock:
        saved = _read()
        for k, v in (changes or {}).items():
            if k not in KEYS:
                continue
            v = str(v or "").strip()
            if len(v) > MAX_LEN or any(c in v for c in "\r\n\t\0"):
                problems[k] = "That doesn't look like a valid value."
                continue
            if k == "RECAPLY_LLM_ORDER" and v:
                names = [p.strip().lower() for p in v.split(",") if p.strip()]
                bad = [p for p in names if p not in PROVIDER_NAMES]
                if bad:
                    # an API key pasted into the wrong box ends up here: never save (or echo) it
                    problems[k] = ("Only provider names go here, separated by commas: "
                                   + ", ".join(PROVIDER_NAMES) + ". Paste API keys into their own boxes above.")
                    continue
                v = ",".join(dict.fromkeys(names))
            if v:
                saved[k] = v
                os.environ[k] = v
                _from_ui.add(k)
            else:
                saved.pop(k, None)
                if k in _from_ui:                      # back to whatever the environment / .env had
                    os.environ.pop(k, None)
                    _from_ui.discard(k)
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        os.replace(tmp, _path())
        try:
            os.chmod(_path(), 0o600)
        except OSError:
            pass
        _apply(settings)
    from recaply.log import get_logger          # here, not at import: recaply.log needs the finished settings
    get_logger("user_settings").info("settings saved", keys=sorted(changes or {}))
    return problems


def status() -> list[dict]:
    """Every setting the page shows: {name, label, secret, set, source: ui|env|none, value (non-secrets only)}."""
    out = []
    for env, (_field, label, secret) in KEYS.items():
        val = os.environ.get(env, "").strip()
        out.append({"name": env, "label": label, "secret": secret, "set": bool(val),
                    "source": "none" if not val else ("ui" if env in _from_ui else "env"),
                    "value": "" if secret else val})
    return out
