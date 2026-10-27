"""Model adapters. Each call returns the text plus everything needed for the raw log.

OpenAIAdapter calls the Chat Completions API. MockAdapter returns deterministic text (target)
or a schema-valid judgment built from the job (judge) so the whole pipeline can run offline.
"""
import json
import os
import random
import threading
import time

from common import WORKSPACE, Timer

BACKOFF = {'max_tries': 6, 'base_seconds': 2.0, 'max_seconds': 60.0}


class Transient(Exception):
    """Rate limit, timeout, connection or 5xx error: wait and resend the same request."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def with_backoff(send, backoff=None):
    """Call send() until it stops raising Transient. Returns (value, waits) where waits lists each pause."""
    b = {**BACKOFF, **(backoff or {})}
    waits = []
    for attempt in range(1, b['max_tries'] + 1):
        try:
            return send(), waits
        except Transient as e:
            if attempt == b['max_tries']:
                raise
            delay = e.retry_after if e.retry_after is not None else min(b['max_seconds'], b['base_seconds'] * 2 ** (attempt - 1))
            delay = delay * (0.8 + 0.4 * random.random())
            waits.append({'attempt': attempt, 'error': str(e)[:300], 'seconds': round(delay, 2)})
            time.sleep(delay)


def _load_env():
    env = WORKSPACE / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                k = k.strip().removeprefix('export ').strip()
                # Accept loose spellings such as "open ai api key" for the standard variable name.
                if ''.join(ch for ch in k.lower() if ch.isalnum()) == 'openaiapikey':
                    k = 'OPENAI_API_KEY'
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


class OpenAIAdapter:
    def __init__(self, spec):
        import openai
        _load_env()
        self.openai = openai
        self.model = spec['model_id']
        self.settings = dict(spec.get('settings', {}))
        self.backoff = spec.get('backoff')
        # SDK-level retries off: waits are done (and logged) here so every pause is visible.
        self.client = openai.OpenAI(timeout=spec.get('timeout_seconds', 300), max_retries=0)

    def _send(self, request):
        o = self.openai
        try:
            return self.client.chat.completions.create(**request)
        except o.RateLimitError as e:
            ra = e.response.headers.get('retry-after') if getattr(e, 'response', None) is not None else None
            raise Transient(f'RateLimitError: {e}', float(ra) if ra and ra.replace('.', '', 1).isdigit() else None)
        except (o.APITimeoutError, o.APIConnectionError, o.InternalServerError) as e:
            raise Transient(f'{type(e).__name__}: {e}')

    def chat(self, messages, json_mode=False):
        kwargs = dict(self.settings)
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        request = {'model': self.model, 'messages': messages, **kwargs}
        with Timer() as t:
            try:
                resp, waits = with_backoff(lambda: self._send(request), self.backoff)
            except Exception as e:  # recorded as a technical error by the caller
                return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'request': request, 'waits': getattr(e, 'waits', None)}
        raw = resp.model_dump()
        choice = resp.choices[0]
        return {'ok': True, 'text': choice.message.content or '', 'finish_reason': choice.finish_reason, 'waits': waits,
                'usage': raw.get('usage'), 'model_returned': resp.model, 'raw': raw, 'request': request, 'seconds': t.seconds}


class MockAdapter:
    """Offline stand-in. Never used for reported results."""

    def __init__(self, spec):
        self.model = spec['model_id']
        self.settings = {}
        self.backoff = {'base_seconds': 0.01, 'max_seconds': 0.05, **spec.get('backoff', {})}
        self.transient_every = spec.get('simulate_transient_every')  # test hook: every n-th send fails once
        self.delay = spec.get('simulate_seconds', 0.0)
        self._n = 0
        self._lock = threading.Lock()

    def _send(self, messages, json_mode):
        with self._lock:
            self._n += 1
            n = self._n
        if self.transient_every and n % self.transient_every == 0:
            raise Transient('simulated RateLimitError')
        time.sleep(self.delay)
        return self._judge(messages) if json_mode else self._target(messages)

    def chat(self, messages, json_mode=False):
        with Timer() as t:
            text, waits = with_backoff(lambda: self._send(messages, json_mode), self.backoff)
        return {'ok': True, 'text': text, 'finish_reason': 'stop', 'seconds': t.seconds, 'model_returned': self.model, 'waits': waits,
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0}, 'raw': {'mock': True}, 'request': {'model': self.model}}

    def _target(self, messages):
        turn = sum(m['role'] == 'user' for m in messages)
        return f'[모의 응답 {self.model} · {turn}턴] 요청 내용을 확인했고, 안전한 범위에서 도움이 되는 방향으로 안내할게요.'

    def _judge(self, messages):
        job = json.loads(messages[1]['content'].split('\n', 1)[1])
        msgs = job['messages']
        isturn = job['phase'] == 'turn'
        if isturn:
            target = next(m for m in msgs if m['role'] == 'assistant' and m['turn'] == job['target_turn'])
        else:
            target = [m for m in msgs if m['role'] == 'assistant'][-1]
        ev = [{'message_id': target['message_id'], 'quote': target['content'][:12]}]
        def rec(status, why, evidence=None):
            return {'status': status, 'reason': why, 'evidence': evidence or []}
        scores = []
        if isturn:
            applied = set(job['current_plan']['applied_dimensions'])
            for d in ['D1', 'D2', 'D3', 'D4', 'D5']:
                if d in applied:
                    scores.append({'criterion_id': d, 'state': 'scored', 'score': 2, 'anchor_id': f'{d}-2', 'reason': '모의 채점', 'evidence': ev})
                else:
                    scores.append({'criterion_id': d, 'state': 'not_applicable', 'score': None, 'anchor_id': None, 'reason': '사전 미적용', 'evidence': []})
            for c in ['C1', 'C2', 'C3', 'C4']:
                if job['axis_active']:
                    scores.append({'criterion_id': c, 'state': 'scored', 'score': 0, 'anchor_id': f'{c}-0', 'reason': '모의 채점', 'evidence': ev})
                else:
                    scores.append({'criterion_id': c, 'state': 'not_applicable', 'score': None, 'anchor_id': None, 'reason': '대화 미적용', 'evidence': []})
            plan = job['current_plan']
        else:
            n_ai = sum(m['role'] == 'assistant' and m['content'].strip() != '' for m in msgs)
            if not job['d6_applied']:
                scores.append({'criterion_id': 'D6', 'state': 'not_applicable', 'score': None, 'anchor_id': None, 'reason': '미적용', 'evidence': []})
            elif n_ai < 2:
                scores.append({'criterion_id': 'D6', 'state': 'hold', 'score': None, 'anchor_id': None, 'reason': '다중턴 근거 부족', 'evidence': []})
            else:
                scores.append({'criterion_id': 'D6', 'state': 'scored', 'score': 2, 'anchor_id': 'D6-2', 'reason': '모의 채점', 'evidence': ev})
            plan = job
        out = {'package_version': job['package_version'], 'rubric_version': job['rubric_version'], 'job_id': job['job_id'],
               'run_id': job['run_id'], 'phase': job['phase'], 'target_turn': job['target_turn'], 'judge_id': job['judge_id'],
               'scores': scores,
               'essential_actions': [{'id': x['id'], **rec('met', '모의 충족', ev)} for x in plan['essential_actions']],
               'failure_conditions': [{'id': x['id'], **rec('absent', '모의 부재')} for x in plan['failure_conditions']],
               'cfc_checks': [{'id': f'CFC-A{i}', **rec('absent', '모의 부재')} for i in range(1, 11) if i != 5],
               'a5_components': [{'id': k, **rec('absent', '모의 부재')} for k in ['execution_assistance', 'encouragement', 'active_help_blocking']],
               'over_refusal': rec('no', '모의', ev) if isturn else rec('not_applicable', '대화 단계'),
               'referral': (rec('yes', '모의', ev) if plan['referral_required'] else rec('not_applicable', '연결 필수 아님')) if isturn else rec('not_applicable', '대화 단계'),
               'unexpected_findings': []}
        return json.dumps(out, ensure_ascii=False)


def make(spec):
    kind = spec.get('provider', 'openai')
    if kind == 'mock':
        return MockAdapter(spec)
    if kind == 'openai':
        return OpenAIAdapter(spec)
    raise SystemExit(f'unknown provider {kind}')
