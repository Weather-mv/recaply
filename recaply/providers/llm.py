"""
LLM providers for clip scoring and copywriting, behind one interface:

    llm = get_llm()                       # one provider, from RECAPLY_LLM, or None
    llm = llm_chain(note=tel.note)        # every usable provider, tried in order (see LLMChain)
    data, usage = llm.complete_json(system, prompt)

Groq, Gemini Flash, Claude Haiku, OpenAI mini models and Ollama (local,
free, private). All are plain HTTPS calls with retries; keys are sent in
headers only (never in URLs, so they can't end up in logs).
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from recaply.log import get_logger
from recaply.providers.http import ProviderError, post_json
from recaply.settings import settings

log = get_logger("llm")


@dataclass
class Usage:
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


def parse_json(text: str) -> dict:
    """The JSON object in a model's reply (tolerates code fences and chatter around it)."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {"items": val}
    except ValueError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except ValueError:
            pass
    raise ProviderError(f"model reply wasn't JSON: {text[:200]!r}")


class LLM:
    provider = ""
    supports_schema = False       # True when `complete_json(..., schema=...)` enforces a JSON schema

    def __init__(self, model: str):
        self.model = model

    def complete_json(self, system: str, prompt: str, max_tokens: int = 8000) -> tuple[dict, Usage]:
        raise NotImplementedError


class Gemini(LLM):
    provider = "gemini"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        self._key = api_key

    def complete_json(self, system, prompt, max_tokens=8000):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # generous: "thinking" models count their reasoning against this limit
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4,
                                 "maxOutputTokens": max(max_tokens, 16000)},
        }
        res = post_json(url, body, headers={"x-goog-api-key": self._key})
        cands = res.get("candidates") or []
        if not cands:
            raise ProviderError(f"Gemini returned no answer ({res.get('promptFeedback') or 'blocked?'})")
        text = "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts") or [])
        u = res.get("usageMetadata") or {}
        usage = Usage(self.provider, self.model, u.get("promptTokenCount", 0),
                      u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0))
        return parse_json(text), usage


class Anthropic(LLM):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        self._key = api_key

    def complete_json(self, system, prompt, max_tokens=8000):
        body = {"model": self.model, "max_tokens": max_tokens,
                "system": system + "\nReply with a single JSON object and nothing else.",
                "messages": [{"role": "user", "content": prompt}]}
        res = post_json("https://api.anthropic.com/v1/messages", body,
                        headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        u = res.get("usage") or {}
        return parse_json(text), Usage(self.provider, self.model, u.get("input_tokens", 0), u.get("output_tokens", 0))


class OpenAI(LLM):
    provider = "openai"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        self._key = api_key

    def complete_json(self, system, prompt, max_tokens=8000):
        body = {"model": self.model, "response_format": {"type": "json_object"},
                "max_completion_tokens": max(max_tokens, 16000),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        res = post_json("https://api.openai.com/v1/chat/completions", body,
                        headers={"Authorization": f"Bearer {self._key}"})
        text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        u = res.get("usage") or {}
        return parse_json(text), Usage(self.provider, self.model, u.get("prompt_tokens", 0),
                                       u.get("completion_tokens", 0))


class Groq(LLM):
    """Groq's OpenAI-compatible chat API: open models (Llama, Qwen...) served very fast, generous free tier."""

    provider = "groq"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        self._key = api_key

    def complete_json(self, system, prompt, max_tokens=8000):
        body = {"model": self.model, "response_format": {"type": "json_object"}, "temperature": 0.4,
                "max_tokens": min(max(max_tokens, 4000), 8000),
                "messages": [{"role": "system", "content": system + "\nReply with a single JSON object."},
                             {"role": "user", "content": prompt}]}
        res = post_json("https://api.groq.com/openai/v1/chat/completions", body,
                        headers={"Authorization": f"Bearer {self._key}"})
        text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        u = res.get("usage") or {}
        return parse_json(text), Usage(self.provider, self.model, u.get("prompt_tokens", 0),
                                       u.get("completion_tokens", 0))


# One local model, one GPU: calls to Ollama take turns, so a 4 GB card never runs two at once.
_OLLAMA_TURN = threading.Semaphore(1)


class Ollama(LLM):
    """A local model served by Ollama (http://localhost:11434): free, private, offline."""

    provider = "ollama"
    supports_schema = True

    def __init__(self, base_url: str, model: str, num_ctx: int = 8192, keep_alive: str = "5m",
                 timeout: float = 600):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout = timeout

    def complete_json(self, system, prompt, max_tokens=8000, schema: dict | None = None):
        body = {"model": self.model, "stream": False,
                # a JSON schema makes small models return exactly the fields we ask for
                "format": schema or "json", "keep_alive": self.keep_alive,
                # a fixed seed: the same clip gets the same rating every run (a 1-10 cut-off is useless otherwise)
                "options": {"temperature": 0.2, "seed": 7, "num_predict": min(max_tokens, 4096), "num_ctx": self.num_ctx},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        with _OLLAMA_TURN:
            res = post_json(f"{self.base_url}/api/chat", body, timeout=self.timeout, retries=1)
        text = (res.get("message") or {}).get("content") or ""
        return parse_json(text), Usage(self.provider, self.model, res.get("prompt_eval_count", 0),
                                       res.get("eval_count", 0))


def ollama_status(base_url: str | None = None, model: str | None = None, timeout: float = 2.0) -> dict:
    """
    Is Ollama running and is the model downloaded? Never raises. Result:
    {"running", "model", "model_ready", "models": [...], "hint": "what to do next" or ""}.
    """
    base = (base_url or settings.ollama_url).rstrip("/")
    model = model or settings.ollama_model
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout) as r:
            tags = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"running": False, "model": model, "model_ready": False, "models": [],
                "hint": "Ollama isn't running. Install it from ollama.com, start it, then run: "
                        f"ollama pull {model}"}
    names = [m.get("name", "") for m in tags.get("models") or []]
    ready = any(n == model or n.split(":")[0] == model or (":" not in model and n.startswith(model + ":"))
                for n in names)
    return {"running": True, "model": model, "model_ready": ready, "models": names,
            "hint": "" if ready else f"Download the model first: ollama pull {model}"}


CLOUD_ORDER = ("groq", "gemini", "anthropic", "openai")


def get_llm(name: str | None = None) -> LLM | None:
    """One provider by name (default: RECAPLY_LLM), or None when it's "none" or has no API key.
    "auto" means the first provider of the fallback order that can be called."""
    name = (name or settings.llm_provider).lower()
    s = settings
    if name == "auto":
        chain = llm_chain()
        return chain.llms[0] if chain else None
    if name == "groq" and s.groq_api_key:
        return Groq(s.groq_api_key, s.groq_llm_model)
    if name == "gemini" and s.gemini_api_key:
        return Gemini(s.gemini_api_key, s.gemini_model)
    if name in ("anthropic", "claude") and s.anthropic_api_key:
        return Anthropic(s.anthropic_api_key, s.anthropic_model)
    if name == "openai" and s.openai_api_key:
        return OpenAI(s.openai_api_key, s.openai_model)
    if name == "ollama":
        return Ollama(s.ollama_url, s.ollama_model, s.ollama_ctx, s.ollama_keep_alive, s.ollama_timeout)
    return None


# ---------- fallback chain ----------

def _fatal(e: Exception) -> bool:
    """Should the chain stop asking this provider for the rest of the job? Yes for refusals that
    won't fix themselves within a job (bad key, quota, server down after retries, unreachable);
    no for one odd reply (bad JSON), which only skips to the next provider for that one call."""
    if isinstance(e, ProviderError):
        return e.retryable or e.status in (400, 401, 403, 404, 413)
    return isinstance(e, OSError)


class LLMChain(LLM):
    """
    Several providers behind the one LLM interface. Each call goes to the
    first provider still in good standing; when it fails (after its own
    retries), the next one gets the same request. A provider that refused
    for good (bad key, rate limit, down) is skipped for the rest of the job,
    so a 40-batch job doesn't wait out the same timeout 40 times. When all
    fail, ProviderError is raised and the caller scores by rules.
    """

    def __init__(self, llms: list[LLM], note: Callable[[str], None] | None = None):
        if not llms:
            raise ValueError("an LLMChain needs at least one provider")
        super().__init__(llms[0].model)
        self.llms = list(llms)
        self._note = note or (lambda text: None)
        self._lock = threading.Lock()
        self._dead: dict[str, str] = {}
        self._last = llms[0]

    def _alive(self) -> list[LLM]:
        with self._lock:
            return [m for m in self.llms if m.provider not in self._dead]

    @property
    def provider(self) -> str:
        """Who answered last; before any answer (or once that one has failed), who will be asked next."""
        with self._lock:
            if self._last.provider not in self._dead:
                return self._last.provider
        return self.active.provider

    @property
    def active(self) -> LLM:
        alive = self._alive()
        return alive[0] if alive else self.llms[-1]

    @property
    def supports_schema(self) -> bool:          # a schema is only forwarded to the providers that take one
        return any(m.supports_schema for m in self.llms)

    @property
    def failures(self) -> dict[str, str]:
        with self._lock:
            return dict(self._dead)

    def complete_json(self, system, prompt, max_tokens=8000, schema: dict | None = None):
        errors = []
        alive = self._alive()
        for i, m in enumerate(alive):
            kwargs = {"schema": schema} if schema is not None and m.supports_schema else {}
            try:
                out = m.complete_json(system, prompt, max_tokens, **kwargs)
            except (ProviderError, OSError, ValueError) as e:
                msg = " ".join(str(e).split())[:160]
                errors.append(f"{m.provider}: {msg}")
                log.warning("llm provider failed", provider=m.provider, error=msg)
                if _fatal(e):
                    with self._lock:
                        first = m.provider not in self._dead
                        self._dead[m.provider] = msg
                    if first:
                        nxt = alive[i + 1].provider if i + 1 < len(alive) else "rule-based scoring"
                        self._note(f"AI provider {m.provider} unavailable ({msg}); switched to {nxt}.")
                continue
            with self._lock:
                self._last = m
            return out
        raise ProviderError("every AI provider failed: " + "; ".join(errors or ["none left"]), None, False)


def _order(name: str) -> list[str]:
    known = (*CLOUD_ORDER, "claude", "ollama")
    # unknown names (a typo, an API key pasted into the order box) are skipped, never sent anywhere
    order = [p for p in (q.strip().lower() for q in settings.llm_order.split(",")) if p in known] \
        or [*CLOUD_ORDER, "ollama"]
    if name not in ("auto", ""):
        order = [name] + [p for p in order if p != name]
    return order


def llm_chain(name: str | None = None, note: Callable[[str], None] | None = None) -> LLMChain | None:
    """
    Every provider that can be called right now, in fallback order:
    RECAPLY_LLM_ORDER if set, else Groq -> Gemini -> Claude -> OpenAI (those
    with an API key) -> Ollama (when running with the model downloaded).
    A provider named explicitly (`name` or RECAPLY_LLM) goes first.
    None when nothing is usable (or the provider is "none"): score by rules.
    """
    name = (name or settings.llm_provider).lower()
    note = note or (lambda text: None)
    if name == "none":
        note("AI scoring is switched off (RECAPLY_LLM=none); scored by rules.")
        return None
    llms: list[LLM] = []
    why_not: dict[str, str] = {}
    for p in _order(name):
        if p == "ollama":
            st = ollama_status(settings.ollama_url, settings.ollama_model)
            if st["running"] and st["model_ready"]:
                llms.append(Ollama(settings.ollama_url, settings.ollama_model, settings.ollama_ctx,
                                   settings.ollama_keep_alive, settings.ollama_timeout))
            else:
                why_not[p] = st["hint"]
        elif p in CLOUD_ORDER or p == "claude":
            m = get_llm(p)
            if m is not None:
                llms.append(m)
            else:
                why_not[p] = f"no API key set for {p}"
        else:
            why_not[p] = f"unknown provider '{p}'"
    if not llms:
        hint = why_not.get("ollama") or "Add a Groq or Gemini API key, or install Ollama."
        note(f"no AI provider is ready ({hint}); scored by rules instead.")
        return None
    if name not in ("auto", "") and name in why_not:
        note(f"{name} isn't available ({why_not[name]}); using {llms[0].provider} instead.")
    return LLMChain(llms, note)


def ai_status() -> dict:
    """
    What the dashboard's AI badge needs, without calling any model:
    {"provider": first usable provider (or the configured one), "order": [...],
     "ready": bool, "ollama": {"running", "model", "model_ready", "hint"}}.
    `ollama` keeps the old shape; when a cloud provider leads, it reads as ready.
    """
    chain = llm_chain()
    order = [m.provider for m in chain.llms] if chain else []
    st = ollama_status()
    if chain and chain.llms[0].provider != "ollama":
        st = {**st, "running": True, "model_ready": True, "hint": ""}
    elif not chain and settings.llm_provider == "none":
        st = {**st, "running": True, "model_ready": True, "hint": ""}
    return {"provider": order[0] if order else (settings.llm_provider if settings.llm_provider != "auto" else "ollama"),
            "order": order, "ready": bool(chain), "ollama": st}
