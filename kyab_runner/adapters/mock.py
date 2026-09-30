"""모의 어댑터. 외부로 아무것도 보내지 않고, 정해진 시나리오대로 결과를 만든다.

시나리오
  normal    정상 응답
  refusal   모델이 스스로 거절하는 문장. 차단이 아니므로 success
  length    출력 한도에서 잘린 응답. success + finish_reason=length
  blocked   공급자 안전필터가 응답을 막음. blocked + block_source=provider
  empty     본문 없이 정상 종료. empty
  error     되풀이해도 실패하는 오류. error (재시도 대상 아님)
  timeout   매번 시간 초과. timeout (재시도 대상)
  flaky     처음 두 번은 일시 오류, 세 번째에 정상 응답 (재시도 경로 확인용)

어느 호출에 어떤 시나리오를 쓸지는 plan으로 정한다.
  {"default": "normal",
   "rules": [{"item_id": "KYAB-900003", "scenario": "blocked"},
             {"item_id": "KYAB-900101", "turn_index": 2, "scenario": "error"}]}
rules는 위에서부터 처음 맞는 것을 쓴다. 조건 키: item_id, turn_index, rollout_no.
"""
from .base import Adapter, AdapterResult

SCENARIOS = ("normal", "refusal", "length", "blocked", "empty", "error", "timeout", "flaky")
_RULE_KEYS = ("item_id", "turn_index", "rollout_no")
_FLAKY_FAILURES = 2         # flaky 시나리오가 실패하는 횟수


class MockAdapter(Adapter):
    def __init__(self, model, plan):
        self._model = model
        self._default = plan.get("default", "normal")
        self._rules = plan.get("rules", [])
        unknown = {r["scenario"] for r in self._rules} | {self._default}
        unknown -= set(SCENARIOS)
        if unknown:
            raise ValueError(f"알 수 없는 모의 시나리오 {sorted(unknown)} (가능: {SCENARIOS})")

    def _scenario(self, call_info):
        for rule in self._rules:
            if all(rule[k] == getattr(call_info, k) for k in _RULE_KEYS if k in rule):
                return rule["scenario"]
        return self._default

    def complete(self, messages, params, call_info):
        scenario = self._scenario(call_info)
        if scenario == "flaky":
            scenario = "transient" if call_info.attempt <= _FLAKY_FAILURES else "normal"
        return getattr(self, f"_{scenario}")(messages, params, call_info)

    # ── 응답을 돌려주는 시나리오 ────────────────────────────────────────
    def _normal(self, messages, params, call_info):
        # 앞 턴 응답이 요청에 몇 개 들어왔는지 본문에 적어, 대화 이력 전달을 눈으로 확인할 수 있게 한다.
        prior = sum(1 for m in messages if m["role"] == "assistant")
        text = (f"[모의 응답] {call_info.item_id} 턴 {call_info.turn_index} · 반복 {call_info.rollout_no}. "
                f"앞선 응답 {prior}개를 이어받았습니다.")
        return self._completion(messages, text, "stop")

    def _refusal(self, messages, params, call_info):
        return self._completion(messages, "[모의 응답] 죄송하지만 그 요청은 도와드릴 수 없습니다.", "stop")

    def _length(self, messages, params, call_info):
        return self._completion(messages, "[모의 응답] 출력 한도에서 잘린 응답입니다. 이어지는 내용은", "length")

    def _empty(self, messages, params, call_info):
        return self._completion(messages, "", "stop", status="empty",
                                error_code="empty_response", error_message="응답 본문이 비어 있음")

    def _blocked(self, messages, params, call_info):
        return self._completion(messages, None, "content_filter", status="blocked", block_source="provider")

    # ── 응답이 오지 않는 시나리오 ───────────────────────────────────────
    def _error(self, messages, params, call_info):
        return AdapterResult("error", error_code="provider_error",
                             error_message="mock: 500 Internal Server Error")

    def _transient(self, messages, params, call_info):
        return AdapterResult("error", error_code="rate_limited",
                             error_message="mock: 429 Too Many Requests", retryable=True)

    def _timeout(self, messages, params, call_info):
        return AdapterResult("timeout", error_code="timeout",
                             error_message="mock: 응답 시간 초과", retryable=True)

    # ── 공급자 응답 객체 흉내 ───────────────────────────────────────────
    def _completion(self, messages, text, finish_reason, status="success", **extra):
        """chat completion 형태의 원본 응답을 만든다. usage가 있어 토큰 합산 경로를 시험할 수 있다."""
        prompt_tokens = sum(len(m["content"]) for m in messages)     # 글자 수로 대신한 모의 토큰 수
        completion_tokens = len(text or "")
        raw = {
            "object": "chat.completion",
            "model": self._model.model_version,
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        }
        return AdapterResult(status, response_text=text or "", raw_response=raw,
                             finish_reason=finish_reason, **extra)
