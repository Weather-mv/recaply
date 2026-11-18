"""
LLM providers for clip scoring and copywriting, behind one interface:

    llm = get_llm()                       # from RECAPLY_LLM, or None
    data, usage = llm.complete_json(system, prompt)

Gemini Flash (default; free tier), Claude Haiku, OpenAI mini models and
Ollama (local, free). All are plain HTTPS calls with retries; keys are sent
in headers only (never in URLs, so they can't end up in logs).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from recaply.providers.http import ProviderError, post_json
from recaply.settings import settings


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


class Ollama(LLM):
    provider = "ollama"

    def __init__(self, base_url: str, model: str):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")

    def complete_json(self, system, prompt, max_tokens=8000):
        body = {"model": self.model, "stream": False, "format": "json",
                "options": {"temperature": 0.3, "num_predict": max_tokens, "num_ctx": 32768},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        res = post_json(f"{self.base_url}/api/chat", body, timeout=600, retries=1)
        text = (res.get("message") or {}).get("content") or ""
        return parse_json(text), Usage(self.provider, self.model, res.get("prompt_eval_count", 0),
                                       res.get("eval_count", 0))


def get_llm(name: str | None = None) -> LLM | None:
    """The configured LLM, or None when it's "none" or has no API key."""
    name = (name or settings.llm_provider).lower()
    s = settings
    if name == "gemini" and s.gemini_api_key:
        return Gemini(s.gemini_api_key, s.gemini_model)
    if name in ("anthropic", "claude") and s.anthropic_api_key:
        return Anthropic(s.anthropic_api_key, s.anthropic_model)
    if name == "openai" and s.openai_api_key:
        return OpenAI(s.openai_api_key, s.openai_model)
    if name == "ollama":
        return Ollama(s.ollama_url, s.ollama_model)
    return None
