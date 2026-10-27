"""Bundle results for the research team and measure actual usage/cost from the raw logs.

Usage: python pipeline/package.py <config.json> [prices.json]
Writes runs/<run_tag>/report/: per-dialogue bundles, 결과요약.csv, 집계.json, 판단어려운사례.json/.md, 사용량_비용.md
"""
import csv
import json
import sys
from collections import defaultdict

import agreement
from common import ROOT, frozen_cards, load_config, reference, write_json

RATE_KEYS = ['FR', 'inconclusive_rate', 'CFR', 'ORR', 'TRR']


def load_results(out):
    rows = []
    for p in sorted((out / 'results').glob('*/*/*.json')):
        r = json.loads(p.read_text(encoding='utf-8'))
        r['_path'] = p.relative_to(out / 'results')
        rows.append(r)
    return rows


def aggregate(results):
    """Package summarize_runs per user condition (its unit key has no condition), then pooled totals."""
    per_cond, pooled = {}, defaultdict(lambda: defaultdict(lambda: [0, 0]))
    by = defaultdict(list)
    for r in results:
        c = r['_pipeline']['condition']
        by[(r['_pipeline']['judge'], r['_pipeline']['target'], c['age_band'], c['gender'])].append(r)
    for (j, t, a, g), xs in sorted(by.items()):
        clean = [{k: v for k, v in x.items() if not k.startswith('_')} for x in xs]
        groups = reference.summarize_runs(clean)
        per_cond[f'{j} | {t} | {a}/{g}'] = groups
        for grp in groups:
            for k in RATE_KEYS:
                pooled[(j, t)][k][0] += grp[k]['numerator']
                pooled[(j, t)][k][1] += grp[k]['denominator']
    pooled_out = {f'{j} | {t}': {k: {'numerator': n, 'denominator': d, 'value': n / d if d else None} for k, (n, d) in v.items()}
                  for (j, t), v in pooled.items()}
    return per_cond, pooled_out


def hard_cases(results, cards):
    cases = []
    verdicts = defaultdict(dict)
    for r in results:
        pl = r['_pipeline']
        key = (pl['target'], r['run']['item_id'], pl['condition']['age_band'], pl['condition']['gender'])
        verdicts[key][pl['judge']] = r['safety_verdict']
        why = []
        if r['safety_verdict'] == 'inconclusive':
            why.append('판정 미결(inconclusive)')
        if r['human_review']:
            why.append('인간검토 필요: ' + '; '.join(sorted({h.get('reason') or h.get('criterion_id', '?') for h in r['human_review']})))
        if r['missing_judgment_jobs']:
            why.append('채점 누락 job: ' + ', '.join(r['missing_judgment_jobs']))
        why += pl['judge_notes'] + pl['generation_flags']
        flags = [f for f in cards[(r['_card_version'], r['run']['item_id'])]['flags'] if f.startswith('[연구팀확인]')]
        if any(f.startswith('[연구팀확인] Q2') for f in flags):
            why.append('이용자 설정 충돌 문항(Q2)')
        if pl['self_judged']:
            why.append('자기 응답 채점(평가 대상 = 채점 모델)')
        if why:
            cases.append({'dialogue': str(r['_path']), 'judge': pl['judge'], 'target': pl['target'], 'item_id': r['run']['item_id'],
                          'condition': pl['condition'], 'verdict': r['safety_verdict'], 'reasons': why})
    for (t, iid, a, g), v in verdicts.items():
        if len(set(v.values())) > 1:
            cases.append({'dialogue': f'{t}/{iid}__{a}_{g}', 'judge': '채점 모델 간 비교', 'target': t, 'item_id': iid,
                          'condition': {'age_band': a, 'gender': g}, 'verdict': v, 'reasons': ['채점 모델 간 최종 판정 불일치']})
    return cases


def usage(out, prices):
    stats = defaultdict(lambda: defaultdict(float))
    for stage in ['generation', 'judging']:
        p = out / f'logs/{stage}.jsonl'
        if not p.exists():
            continue
        for line in p.open(encoding='utf-8'):
            x = json.loads(line)
            model = x.get('model')  # model_id as requested (config), not the dated snapshot the API echoes
            s = stats[(stage, model)]
            u = x.get('usage') or {}
            s['calls'] += 1
            s['failed_attempts'] += 0 if x.get('ok') else 1
            s['seconds'] += x.get('seconds') or 0
            s['input'] += u.get('prompt_tokens') or 0
            s['cached'] += (u.get('prompt_tokens_details') or {}).get('cached_tokens') or 0
            s['output'] += u.get('completion_tokens') or 0
            s['reasoning'] += (u.get('completion_tokens_details') or {}).get('reasoning_tokens') or 0
    lines = ['# 실측 사용량·비용', '', '| 단계 | 모델 | 호출(재시도 포함) | 실패 시도 | 입력 토큰 | 그중 캐시 | 출력 토큰 | 그중 추론 | 총 소요(초) | 비용(USD) |', '|---|---|---|---|---|---|---|---|---|---|']
    total = 0.0
    for (stage, model), s in sorted(stats.items()):
        p = next((v for k, v in prices.items() if k == model or (isinstance(v, dict) and v.get('model_id') == model)), None)
        cost = None
        if p:
            cost = ((s['input'] - s['cached']) * p['input'] + s['cached'] * p.get('cached_input', p['input']) + s['output'] * p['output']) / 1e6
            total += cost
        lines.append(f"| {stage} | {model} | {int(s['calls'])} | {int(s['failed_attempts'])} | {int(s['input']):,} | {int(s['cached']):,} | "
                     f"{int(s['output']):,} | {int(s['reasoning']):,} | {s['seconds']:.1f} | {'' if cost is None else f'${cost:,.4f}'} |")
    lines += ['', f'단가를 아는 모델 합계: ${total:,.4f}. 단가 없는 모델은 비어 있음(`tools/prices.json`에 추가).']
    return '\n'.join(lines)


def main(cfg_path, prices_path=None):
    cfg = load_config(cfg_path)
    prices = json.loads(open(prices_path, encoding='utf-8').read()) if prices_path else {}
    out = ROOT / 'runs' / cfg['run_tag']
    card_files, cards = set(), {}
    rep = out / 'report'
    results = load_results(out)
    bundles = []
    for r in results:
        rel = r['_path']
        d = json.loads((out / 'dialogues' / rel.relative_to(rel.parts[0])).read_text(encoding='utf-8'))
        r['_card_version'] = d['card_version']
        if (d['card_version'], r['run']['item_id']) not in cards:
            name, by_item = frozen_cards(d['card_version'])
            card_files.add(name)
            cards.update({(d['card_version'], k): v for k, v in by_item.items()})
        card = cards[(d['card_version'], r['run']['item_id'])]
        bundle = {'card_file': f"조건표_pilot50_{d['card_version']}.json", 'dialogue': d, 'conditions': card['item'], 'rationale': card['rationale'],
                  'card_flags': card['flags'], 'judgments': json.loads((out / 'judgments' / rel).read_text(encoding='utf-8')),
                  'result': {k: v for k, v in r.items() if k not in ('_path', '_card_version')}}
        write_json(rep / 'bundles' / rel, bundle, overwrite=True)
        bundles.append(bundle)
    with (rep / '결과요약.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['채점 모델', '평가 대상', '문항', '연령', '성별', '판정', 'CFC', '첫 실패 턴', 'MRS_app', 'MRS6', 'CRRI 상태', 'CRRI',
                    '과잉거절', '연결', '인간검토 수', '자기 채점', 'smoke'])
        for r in results:
            pl = r['_pipeline']
            w.writerow([pl['judge'], pl['target'], r['run']['item_id'], pl['condition']['age_band'], pl['condition']['gender'],
                        r['safety_verdict'], r['cfc_status'], r['first_failure_turn'], r['MRS_app'], r['MRS6'], r['CRRI']['state'],
                        r['CRRI']['value'], r['over_refusal'], r['referral'], len(r['human_review']), pl['self_judged'], pl['smoke']])
    per_cond, pooled = aggregate(results)
    write_json(rep / '집계.json', {'note': '패키지 summarize_runs를 이용자 조건별로 호출(평가 단위 키에 조건이 없어 한 번에 넣으면 중복으로 거부됨). pooled는 같은 공식으로 분자·분모를 합산.',
                                  'per_condition': per_cond, 'pooled': pooled}, overwrite=True)
    cases = hard_cases(results, cards)
    write_json(rep / '판단어려운사례.json', cases, overwrite=True)
    md = ['# 판단이 어려운 사례', '', f'총 {len(cases)}건 (채점 모델·대화 단위)', '', '| 대화 | 채점 모델 | 판정 | 사유 |', '|---|---|---|---|']
    md += [f"| {c['dialogue']} | {c['judge']} | {c['verdict']} | {'<br>'.join(c['reasons'])} |" for c in cases]
    (rep / '판단어려운사례.md').write_text('\n'.join(md), encoding='utf-8')
    (rep / '사용량_비용.md').write_text(usage(out, prices), encoding='utf-8')
    agree = agreement.analyse(bundles)
    write_json(rep / '채점일치도.json', agree, overwrite=True)
    (rep / '채점일치도.md').write_text(agreement.markdown(agree), encoding='utf-8')
    try:
        import report_xlsx
        report_xlsx.build(bundles, cases, pooled, rep / f"결과_{cfg['run_tag']}.xlsx",
                          {'run_tag': cfg['run_tag'], 'card_file': ', '.join(sorted(card_files)), 'smoke': bool(cfg.get('smoke'))})
    except ImportError as e:
        print('workbook skipped:', e)
    print(f'packaged {len(results)} results, {len(cases)} hard cases -> {rep}')


if __name__ == '__main__':
    main(*sys.argv[1:3])
