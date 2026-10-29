"""Minimal, optional LLM clients over urllib (no SDK dependency).

Providers: Anthropic Claude (ANTHROPIC_API_KEY) or Google Gemini
(GEMINI_API_KEY or GOOGLE_API_KEY). If both keys are set, Claude is used
unless GRANTAUDIT_PROVIDER=gemini. GRANTAUDIT_MODEL overrides the model.

The LLM never makes compliance decisions: it extracts award terms (with
quotes) and suggests categories. Tests never touch the network.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable, Optional

from .config import DEFAULT_GEMINI_MODEL, DEFAULT_MODEL

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

LLMCall = Callable[[str, str], str]  # (system, user) -> text

PROVIDER_LABELS = {"anthropic": "Claude (Anthropic)", "gemini": "Gemini (Google)"}


def load_dotenv(path=None) -> list[str]:
    """Load KEY=VALUE lines from a .env file into os.environ (stdlib only).

    Variables already set in the environment win. Returns the names loaded
    (never the values). Called by the app and CLI, not on import, so tests
    never read a real .env.
    """
    from pathlib import Path
    path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def _gemini_key() -> Optional[str]:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def provider() -> Optional[str]:
    """'anthropic', 'gemini' or None (offline)."""
    keys = {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")), "gemini": bool(_gemini_key())}
    wanted = os.environ.get("GRANTAUDIT_PROVIDER", "").strip().lower()
    if wanted in keys:
        return wanted if keys[wanted] else None
    return "anthropic" if keys["anthropic"] else ("gemini" if keys["gemini"] else None)


def available() -> bool:
    return provider() is not None


def provider_label() -> str:
    return PROVIDER_LABELS.get(provider() or "", "offline")


def model_name() -> str:
    default = DEFAULT_GEMINI_MODEL if provider() == "gemini" else DEFAULT_MODEL
    return os.environ.get("GRANTAUDIT_MODEL", default)


def call_claude(system: str, user: str, max_tokens: int = 16000, timeout: float = 120.0) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    body = {
        "model": model_name(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": API_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Claude API error {e.code}: {e.read()[:300]!r}") from e
    if payload.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined the request")
    if payload.get("stop_reason") == "max_tokens":
        raise RuntimeError("Claude response was cut off (max_tokens)")
    return "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")


def call_gemini(system: str, user: str, max_tokens: int = 16000, timeout: float = 120.0) -> str:
    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        GEMINI_URL.format(model=model_name()),
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-goog-api-key": key},  # header, so the key isn't in the URL
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini API error {e.code}: {e.read()[:300]!r}") from e
    block = (payload.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise RuntimeError(f"Gemini blocked the request ({block})")
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")
    finish = candidates[0].get("finishReason")
    if finish == "MAX_TOKENS":
        raise RuntimeError("Gemini response was cut off (MAX_TOKENS)")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text and finish not in (None, "STOP"):
        raise RuntimeError(f"Gemini stopped without output ({finish})")
    return text


def extract_json(text: str) -> Optional[object]:
    """Parse the first JSON object/array in a model response (tolerates ```json fences)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    # Start at the FIRST bracket of either kind, so a top-level list isn't mistaken for its first object.
    starts = sorted(i for i in (text.find("{"), text.find("[")) if i != -1)
    for i in starts:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[i:])
            return obj
        except json.JSONDecodeError:
            continue
    return None


def default_call() -> Optional[LLMCall]:
    p = provider()
    if p == "anthropic":
        return lambda system, user: call_claude(system, user)
    if p == "gemini":
        return lambda system, user: call_gemini(system, user)
    return None
