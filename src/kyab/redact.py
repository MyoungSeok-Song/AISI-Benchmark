"""Secret redaction applied before anything is written to runs/responses/batch manifests.

Two layers:
1. Exact values: every environment variable whose name looks like a credential (…API_KEY, …TOKEN,
   …SECRET, …PASSWORD) plus any secret a client registers is replaced wherever it appears, in every
   stored field.
2. Patterns: Authorization / Bearer headers, provider key shapes (OpenAI sk-, Anthropic sk-ant-,
   Google AIza, Hugging Face hf_) and "api_key": "…"-style pairs. Applied to technical logs only
   (raw_response_json, error_message) — never to response_text / request_messages_json, which are
   research data and must stay byte-exact (layer 1 still applies to them).
"""
from __future__ import annotations

import json
import os
import re

MASK = "***REDACTED***"
_SECRET_ENV = re.compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD)$", re.I)
_MIN_LEN = 8  # never mask short values like "1" or "true" that happen to sit in a *_TOKEN variable

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(\"?)(bearer\s+)?[^\s\"',}]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),
    # only credential-shaped names: plain words like "secret" are common in this benchmark's model outputs
    re.compile(r"(?i)([\"']?(?:api[_-]?key|x-api-key|access[_-]?token|client[_-]?secret)[\"']?\s*[:=]\s*[\"']?)"
               r"[^\s\"',}]{6,}"),
]
_SENSITIVE_KEY = re.compile(r"(?i)^(authorization|x-api-key|api[_-]?key|access[_-]?token|secret|password)$")

_extra: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= _MIN_LEN:
        _extra.add(value)


def known_secrets() -> list[str]:
    vals = {v for k, v in os.environ.items() if _SECRET_ENV.search(k) and v and len(v) >= _MIN_LEN}
    return sorted(vals | _extra, key=len, reverse=True)


def redact_exact(text: str) -> str:
    for s in known_secrets():
        text = text.replace(s, MASK)
    return text


def redact_text(text: str) -> str:
    """Exact values + credential patterns. For error messages and other technical text."""
    text = redact_exact(text)
    for p in _PATTERNS:
        if p.groups:
            text = p.sub(lambda m: (m.group(1) or "") + MASK, text)
        else:
            text = p.sub(MASK, text)
    return text


def redact_obj(obj):
    """Recursively redact a JSON-like object: sensitive keys are masked, all strings pattern-redacted."""
    if isinstance(obj, dict):
        return {k: (MASK if isinstance(k, str) and _SENSITIVE_KEY.match(k) else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def redact_json_text(s: str) -> str:
    """Redact a JSON string by value (keeps it valid JSON)."""
    try:
        return json.dumps(redact_obj(json.loads(s)), ensure_ascii=False, default=str)
    except (ValueError, TypeError):
        return redact_text(s)
