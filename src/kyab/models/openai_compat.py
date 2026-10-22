"""OpenAI-compatible Chat Completions client (OpenAI API, vLLM, and other compatible servers).

The API key is read from an environment variable named in config/models.yaml and is only placed in the
Authorization header. It is registered with kyab.redact, and error text / raw payloads are redacted here;
the runner applies the same sanitizer again before writing.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

import httpx

from ..redact import redact_obj, redact_text, register_secret
from .base import (BLOCKED, EMPTY, ERROR, SUCCESS, TIMEOUT, GenerationConfig, GenerationResult, ModelInfo,
                   normalize_finish_reason)


class OpenAICompatibleClient:
    def __init__(self, info: ModelInfo, *, base_url: str, request_model: str, api_key_env: str | None = None,
                 timeout_s: float = 120.0, max_tokens_param: str = "max_tokens",
                 extra_body: dict | None = None, omit_params: list[str] | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.info = info
        self.base_url = base_url.rstrip("/")
        self.request_model = request_model
        self.max_tokens_param = max_tokens_param
        self.extra_body = extra_body or {}
        # Sampling parameters a model rejects (e.g. GPT-5 mini accepts only its default temperature 1 / top_p 1):
        # not sent, so the provider default applies. runs.csv still records the configured value; the batch
        # manifest records omit_params (models.yaml entry) so the two can be reconciled.
        self.omit_params = set(omit_params or ())
        unknown = self.omit_params - {"temperature", "top_p"}
        if unknown:
            raise ValueError(f"omit_params는 temperature, top_p만 가능: {sorted(unknown)}")
        self._secret = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not self._secret:
            raise RuntimeError(f"환경변수 {api_key_env}가 비어 있음 (.env 또는 export로 설정)")
        register_secret(self._secret)
        headers = {"Authorization": f"Bearer {self._secret}"} if self._secret else {}
        self._http = httpx.Client(timeout=timeout_s, headers=headers, transport=transport)

    @staticmethod
    def _scrub(text: str) -> str:
        return redact_text(text)

    @staticmethod
    def _scrub_obj(obj):
        """Providers sometimes echo the key back (e.g. in auth errors); strip it from stored raw JSON too."""
        return redact_obj(obj)

    def generate(self, messages: list[dict[str, Any]], config: GenerationConfig) -> GenerationResult:
        sent = copy.deepcopy(messages)
        body = {"model": self.request_model, "messages": sent, "temperature": config.temperature,
                "top_p": config.top_p, self.max_tokens_param: config.max_output_tokens, **self.extra_body}
        for k in self.omit_params:
            body.pop(k, None)
        try:
            resp = self._http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TimeoutException as e:
            return self._no_response(sent, TIMEOUT, "TIMEOUT", e)
        except httpx.TransportError as e:
            return self._no_response(sent, ERROR, "NETWORK_ERROR", e)

        try:
            payload = self._scrub_obj(resp.json())
        except ValueError:
            payload = None
        if resp.status_code != 200:
            return self._http_error(sent, resp, payload)
        if not isinstance(payload, dict):
            return GenerationResult(ERROR, None, {"http_status": 200, "body_text": self._scrub(resp.text[:4000])},
                                    sent, error_code="PARSER_ERROR", error_message="200 응답 본문이 JSON 객체가 아님")
        try:
            choice = payload["choices"][0]
            msg = choice.get("message") or {}
            text = msg.get("content")
            finish = normalize_finish_reason(choice.get("finish_reason"))
        except (KeyError, IndexError, TypeError) as e:
            return GenerationResult(ERROR, None, payload, sent, error_code="PARSER_ERROR",
                                    error_message=f"choices[0].message 파싱 실패: {e!r}")
        if not text and msg.get("refusal"):
            text = msg["refusal"]  # the model's own refusal is a model response, not a provider block
        if finish == "content_filter" and not text:
            return GenerationResult(BLOCKED, None, payload, sent, finish_reason=finish, block_source="provider",
                                    error_code="PROVIDER_BLOCK", error_message="finish_reason=content_filter")
        if not text or not str(text).strip():
            return GenerationResult(EMPTY, text or "", payload, sent, finish_reason=finish,
                                    error_code="EMPTY_RESPONSE", error_message="응답 본문이 비어 있음")
        return GenerationResult(SUCCESS, text, payload, sent, finish_reason=finish)

    def _no_response(self, sent, status, code, exc) -> GenerationResult:
        raw = {"pipeline_note": "no_provider_response", "exception_type": type(exc).__name__}
        return GenerationResult(status, None, raw, sent, error_code=code, error_message=self._scrub(str(exc)))

    def _http_error(self, sent, resp: httpx.Response, payload) -> GenerationResult:
        code_ = resp.status_code
        raw = {"http_status": code_, "body": payload if payload is not None else self._scrub(resp.text[:4000])}
        text = self._scrub(json.dumps(payload, ensure_ascii=False) if payload is not None else resp.text)[:2000]
        low = text.lower()
        if code_ == 400 and any(m in low for m in ("content_filter", "content management policy")):
            return GenerationResult(BLOCKED, None, raw, sent, finish_reason="content_filter", block_source="provider",
                                    error_code="PROVIDER_BLOCK", error_message=text)
        if code_ in (401, 403):
            ec = "AUTH_ERROR"
        elif code_ == 429:
            ec = "RATE_LIMIT"
        elif code_ == 408:
            return GenerationResult(TIMEOUT, None, raw, sent, error_code="TIMEOUT", error_message=text)
        elif code_ >= 500:
            ec = "SERVER_ERROR"
        elif "context length" in low or "maximum context" in low or "context_length" in low:
            ec = "CONTEXT_LENGTH"
        elif 400 <= code_ < 500:
            ec = "BAD_REQUEST"
        else:
            ec = "UNKNOWN_ERROR"
        return GenerationResult(ERROR, None, raw, sent, error_code=ec, error_message=f"HTTP {code_}: {text}")
