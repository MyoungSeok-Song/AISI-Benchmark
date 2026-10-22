import json

import pytest
from conftest import mock_info

from kyab.models.base import ERROR, GenerationResult
from kyab.redact import MASK, redact_exact, redact_json_text, redact_obj, redact_text
from kyab.runner.conversation import run_item
from kyab.schemas import RESPONSES
from kyab.storage import read_table


@pytest.mark.parametrize("text", [
    "Authorization: Bearer abcdefghijklmnop",
    '{"Authorization": "Bearer abcdefghijklmnop"}',
    "bearer abcdefghijklmnop123",
    "Incorrect API key provided: sk-proj-ABCDEFGH12345678",
    "key sk-ant-api03-ABCDEFGH12345678 rejected",
    "google key AIzaSyA1234567890abcdefghijklmnop",
    "token hf_ABCDEFGHIJKLMNOPQRSTUVWX",
    "api_key=abcdef123456",
    'x-api-key: "abcdef123456"',
])
def test_credential_patterns_are_masked(text):
    out = redact_text(text)
    assert MASK in out
    for secret in ("abcdefghijklmnop", "ABCDEFGH12345678", "AIzaSyA", "hf_ABCD", "abcdef123456"):
        assert secret not in out


def test_ordinary_safety_text_is_not_masked():
    for t in ("Keep this secret: nobody should know.", "The password is not something I can share.",
              "Please talk to a trusted adult about the token of friendship."):
        assert redact_text(t) == t


def test_env_secret_exact_value_is_masked_everywhere(monkeypatch):
    monkeypatch.setenv("SOMEPROVIDER_API_KEY", "custom-format-key-42")
    assert redact_exact("echo custom-format-key-42 back") == f"echo {MASK} back"
    assert redact_obj({"a": ["x custom-format-key-42"]}) == {"a": [f"x {MASK}"]}


def test_sensitive_json_keys_are_masked():
    assert redact_obj({"headers": {"Authorization": "Bearer x", "x-api-key": "y"}, "ok": 1}) == \
        {"headers": {"Authorization": MASK, "x-api-key": MASK}, "ok": 1}
    assert json.loads(redact_json_text('{"api_key": "zzzzzzzz"}')) == {"api_key": MASK}


def test_runner_redacts_error_message_and_raw_response(small_dataset, out, ctx, monkeypatch):
    monkeypatch.setenv("LEAKY_API_KEY", "leaky-secret-value-9")

    class Leaky:
        info = mock_info("leaky")

        def generate(self, messages, config):
            return GenerationResult(ERROR, None, {"echo": "leaky-secret-value-9",
                                                  "headers": {"Authorization": "Bearer sk-abcdefgh12345678"}},
                                    messages, error_code="AUTH_ERROR",
                                    error_message="HTTP 401 key=leaky-secret-value-9 Bearer sk-abcdefgh12345678")

    run_item(small_dataset.executable_items()[0], 1, Leaky(), ctx, out["ids"], out["runs"], out["responses"])
    text = (out["dir"] / "responses.csv").read_text(encoding="utf-8")
    assert "leaky-secret-value-9" not in text and "sk-abcdefgh12345678" not in text
    r = read_table(out["dir"] / "responses.csv", RESPONSES)[0]
    assert json.loads(r["raw_response_json"])["headers"]["Authorization"] == MASK
