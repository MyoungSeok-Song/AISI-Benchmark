"""Offline token and cost estimate for the pilot (no API calls).

Judge inputs are counted exactly from the package's own request builder, using the frozen
condition cards and placeholder assistant responses of a chosen length. Everything that
depends on model behaviour (response length, reasoning tokens, judge output length) is a
scenario parameter, so the estimate brackets the real cost until a measured smoke test exists.

Usage: PYTHONPATH=.pylib python3 tools/estimate_cost.py [prices.json] > out.md
"""
import glob
import json
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_cards as vc  # noqa: E402

ref = vc.reference
ENC = tiktoken.get_encoding('o200k_base')
HERE = Path(__file__).resolve().parents[1]
FROZEN = sorted(p for p in (HERE / 'conditions/frozen').glob('조건표_pilot50_*.json') if not p.name.endswith('.sha256.json'))[-1]
CARDS = {c['item_id']: c for c in json.loads(FROZEN.read_text(encoding='utf-8'))['cards']}
PKG_EX = Path(glob.glob(str(HERE.parent / 'LLM*_20261023' / '*' / '04_*' / '빠른실행_judgments.json'))[0])

CONDITIONS = 6                      # 3 age bands x 2 genders (research team)
TARGETS = ['gpt6-luna', 'gpt5-mini', 'model-3', 'model-4', 'model-5']
JUDGES = ['gpt6-luna', 'gpt5-mini']  # plan A: both judge every dialogue
PERSONA_TOKENS = 12                 # injection method undecided; placeholder size only

SCENARIOS = {  # response tokens per target turn, reasoning tokens per call, judge visible output tokens
    '짧음': {'resp': 300, 'gen_reason': 0, 'judge_out': 1500, 'judge_reason': 0},
    '보통': {'resp': 700, 'gen_reason': 500, 'judge_out': 2500, 'judge_reason': 1500},
    '김': {'resp': 1500, 'gen_reason': 2000, 'judge_out': 4000, 'judge_reason': 4000},
}


def n(text):
    return len(ENC.encode(text))


def filler(tokens):
    unit = '이것은 토큰 수 추정을 위한 자리표시 응답 문장입니다. '
    s = unit * (tokens // max(1, n(unit)) + 1)
    ids = ENC.encode(s)[:tokens]
    return ENC.decode(ids)


def per_item(item_id, resp_tokens):
    card = CARDS[item_id]
    data = vc.synthetic_input(card)
    body = filler(resp_tokens)
    for m in data['messages']:
        if m['role'] == 'assistant':
            m['content'] = body
    users = [n(m['content']) for m in data['messages'] if m['role'] == 'user']
    T = len(users)
    gen_in = sum(sum(users[:t + 1]) + PERSONA_TOKENS + resp_tokens * t for t in range(T))
    jobs = ref.prepare_jobs(data)
    sys_tok, user_tok = 0, []
    for j in jobs:
        msgs = ref.request_messages(j)
        sys_tok = n(msgs[0]['content'])
        user_tok.append(n(msgs[1]['content']))
    return {'T': T, 'gen_calls': T, 'gen_in': gen_in, 'gen_out_visible': resp_tokens * T,
            'judge_calls': len(jobs), 'judge_sys': sys_tok, 'judge_user': sum(user_tok)}


def totals(sc, items):
    rows = [per_item(i, sc['resp']) for i in items]
    g = {k: sum(r[k] for r in rows) for k in ['gen_calls', 'gen_in', 'gen_out_visible', 'judge_calls', 'judge_user']}
    g['judge_sys'] = rows[0]['judge_sys']
    g['gen_out'] = g['gen_out_visible'] + sc['gen_reason'] * g['gen_calls']
    g['judge_in_uncached'] = g['judge_calls'] * g['judge_sys'] + g['judge_user']
    g['judge_out'] = (sc['judge_out'] + sc['judge_reason']) * g['judge_calls']
    return g


def money(tok, price):
    return None if price is None else tok / 1e6 * price


def run(scope_name, items, targets, judges, prices, out):
    out.append(f'\n### {scope_name}\n')
    out.append(f'문항 {len(items)}개 × 조건 {CONDITIONS} × 평가 대상 {len(targets)} / 채점 모델 {len(judges)}\n')
    out.append('| 시나리오 | 생성 호출 | 생성 입력 토큰 | 생성 출력 토큰 | 채점 호출 | 채점 입력 토큰(캐시 전) | 그중 캐시 가능 | 채점 출력 토큰 | 비용(USD, 단가 아는 모델만) |')
    out.append('|---|---|---|---|---|---|---|---|---|')
    for name, sc in SCENARIOS.items():
        g = totals(sc, items)
        k = CONDITIONS
        gen_calls = g['gen_calls'] * k * len(targets)
        gen_in = g['gen_in'] * k
        gen_out = g['gen_out'] * k
        jc = g['judge_calls'] * k * len(targets)
        j_in = g['judge_in_uncached'] * k * len(targets)
        j_cache = max(0, jc - 1) * g['judge_sys']
        j_out = g['judge_out'] * k * len(targets)
        cost, unknown = 0.0, []
        for m in targets:
            p = prices.get(m)
            if not p:
                unknown.append(m)
                continue
            cost += money(gen_in, p['input']) + money(gen_out, p['output'])
        for m in judges:
            p = prices.get(m)
            if not p:
                unknown.append(f'{m}(채점)')
                continue
            # Each judge scores every dialogue; best case the shared system prompt is cached after the first call.
            cost += money(j_in - j_cache, p['input']) + money(j_cache, p.get('cached_input', p['input'])) + money(j_out, p['output'])
        note = f'${cost:,.2f}' + (f' + 미정({", ".join(sorted(set(unknown)))})' if unknown else '')
        out.append(f'| {name} | {gen_calls:,} | {gen_in * len(targets):,} | {gen_out * len(targets):,} | {jc * len(judges):,} | '
                   f'{j_in * len(judges):,} | {j_cache * len(judges):,} | {j_out * len(judges):,} | {note} |')


def main():
    prices = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')) if len(sys.argv) > 1 else {}
    items = list(CARDS)
    smoke = ['NEW-A1.01-S1', 'NEW-A1.02-M2']
    g = per_item(items[0], 700)
    ex = json.loads(PKG_EX.read_text(encoding='utf-8'))
    ex_out = sum(n(json.dumps(x, ensure_ascii=False)) for x in ex) / len(ex)
    out = ['# 시범 50문항 비용 추정 (오프라인, API 호출 없음)', '',
           f'- 조건표: `{FROZEN.name}` · 토크나이저 o200k_base · 연령·성별 주입 문구는 미정이라 {PERSONA_TOKENS}토큰으로 가정',
           f'- 채점 요청 시스템 프롬프트(지시문+기준 원문+출력 스키마): **{g["judge_sys"]:,}토큰/호출**, 모든 채점 호출에 동일 → 프롬프트 캐시 대상',
           f'- 패키지 시연 원평정의 평균 크기: {ex_out:,.0f}토큰/호출(채점 출력 길이 시나리오의 참고값)',
           f'- 단가 입력: {json.dumps(prices, ensure_ascii=False) if prices else "없음"}',
           '- 시나리오: ' + ' / '.join(f'{k}=응답 {v["resp"]}·생성 추론 {v["gen_reason"]}·채점 출력 {v["judge_out"]}·채점 추론 {v["judge_reason"]}' for k, v in SCENARIOS.items())]
    run('A. 소량 실측 테스트 (단일턴 1 + 3턴 1, 6조건, 대상 2 · 채점 2)', smoke, JUDGES, JUDGES, prices, out)
    run('B. 시범 본실행 — 대상 2개(gpt6-luna, gpt5-mini)만', items, JUDGES, JUDGES, prices, out)
    run('C. 시범 본실행 — 대상 5개 전체(A안)', items, TARGETS, JUDGES, prices, out)
    print('\n'.join(out))


if __name__ == '__main__':
    main()
