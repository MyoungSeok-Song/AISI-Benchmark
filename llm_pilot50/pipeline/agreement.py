"""Agreement between judge models on the same dialogues (plan A: every judge scores every dialogue).

- Final verdict: confusion table, raw agreement, Cohen's kappa on pairs both judged pass/fail (M-11).
- Scores (D1-D6, C1-C4, 0-2): exact agreement and quadratic-weighted kappa on pairs both scored.
- CFC: agreement per code on pairs both confirmed/absent.
- Self-judging: each judge's fail rate on its own dialogues vs. on other targets, next to the other judge's.
"""
from collections import Counter, defaultdict
from itertools import combinations


def kappa(pairs, cats):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[c] / n * cb[c] / n for c in cats)
    return None if pe == 1 else (po - pe) / (1 - pe)


def weighted_kappa(pairs, k=3):
    n = len(pairs)
    if not n:
        return None
    w = lambda i, j: (i - j) ** 2 / (k - 1) ** 2
    obs = sum(w(a, b) for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    exp = sum(ca[i] / n * cb[j] / n * w(i, j) for i in range(k) for j in range(k))
    return None if exp == 0 else 1 - obs / exp


def analyse(bundles):
    by_dialogue = defaultdict(dict)
    for b in bundles:
        pl = b['result']['_pipeline']
        by_dialogue[pl['dialogue']][pl['judge']] = b
    judges = sorted({j for v in by_dialogue.values() for j in v})
    out = {'judges': judges, 'pairs': {}}
    for ja, jb in combinations(judges, 2):
        both = [(v[ja], v[jb]) for v in by_dialogue.values() if ja in v and jb in v]
        verdicts = [(a['result']['safety_verdict'], b['result']['safety_verdict']) for a, b in both]
        decided = [p for p in verdicts if set(p) <= {'pass', 'fail'}]
        conf = Counter(f'{a}→{b}' for a, b in verdicts)
        scores, cfc = defaultdict(list), defaultdict(list)
        for a, b in both:
            ia = {(j['phase'], j['target_turn']): j for j in a['judgments']}
            for j in b['judgments']:
                o = ia.get((j['phase'], j['target_turn']))
                if not o:
                    continue
                sa = {s['criterion_id']: s for s in o['scores']}
                for s in j['scores']:
                    t = sa.get(s['criterion_id'])
                    if t and t['state'] == s['state'] == 'scored':
                        scores[s['criterion_id']].append((t['score'], s['score']))
                ca = {x['id']: x['status'] for x in o['cfc_checks']}
                for x in j['cfc_checks']:
                    if {ca.get(x['id']), x['status']} <= {'confirmed', 'absent'}:
                        cfc[x['id']].append((ca[x['id']], x['status']))
        out['pairs'][f'{ja} vs {jb}'] = {
            'dialogues': len(both),
            'verdict_confusion': dict(conf),
            'verdict_agreement': sum(a == b for a, b in verdicts) / len(verdicts) if verdicts else None,
            'pass_fail_pairs': len(decided),
            'pass_fail_kappa': kappa(decided, ['pass', 'fail']),
            'excluded_inconclusive': len(verdicts) - len(decided),
            'scores': {c: {'pairs': len(p), 'exact': sum(a == b for a, b in p) / len(p), 'weighted_kappa': weighted_kappa(p)}
                       for c, p in sorted(scores.items())},
            'cfc': {c: {'pairs': len(p), 'agreement': sum(a == b for a, b in p) / len(p),
                        'confirmed_by': [sum(a == 'confirmed' for a, _ in p), sum(b == 'confirmed' for _, b in p)]}
                    for c, p in sorted(cfc.items())},
        }
    self_rows = []
    for j in judges:
        for scope in ['자기 응답', '다른 대상']:
            for k in judges:
                rs = [v[k]['result'] for v in by_dialogue.values() if k in v
                      and ((v[k]['result']['_pipeline']['target'] == j) == (scope == '자기 응답'))
                      and not v[k]['result']['safe_control']]
                dec = [r for r in rs if r['safety_verdict'] in ('pass', 'fail')]
                fails = sum(r['safety_verdict'] == 'fail' for r in dec)
                self_rows.append({'target_scope': f'{j}의 {scope}' if scope == '자기 응답' else f'{j} 외 대상', 'judge': k,
                                  'FR': fails / len(dec) if dec else None, 'fail': fails, 'decided': len(dec)})
    out['self_judging'] = self_rows
    return out


def markdown(a):
    lines = ['# 채점 모델 간 일치도', '', f"채점 모델: {', '.join(a['judges'])}", '']
    for name, p in a['pairs'].items():
        lines += [f'## {name}', '', f"- 대화 {p['dialogues']}개, 최종 판정 일치율 {_pct(p['verdict_agreement'])}",
                  f"- pass/fail 쌍 {p['pass_fail_pairs']}개의 Cohen κ = {_num(p['pass_fail_kappa'])} (미결 포함 쌍 {p['excluded_inconclusive']}개 제외)",
                  f"- 판정 조합: {', '.join(f'{k} {v}' for k, v in sorted(p['verdict_confusion'].items()))}", '',
                  '| 기준 | 쌍 | 정확 일치 | 가중 κ |', '|---|---|---|---|']
        lines += [f"| {c} | {s['pairs']} | {_pct(s['exact'])} | {_num(s['weighted_kappa'])} |" for c, s in p['scores'].items()]
        hits = {c: s for c, s in p['cfc'].items() if any(s['confirmed_by'])}
        lines += ['', 'CFC 확인 건수(채점 모델별): ' + (', '.join(f"{c} {s['confirmed_by']}" for c, s in hits.items()) or '양쪽 모두 0건'), '']
    lines += ['## 자기 채점 비교 (위험 문항 실패율)', '', '| 대상 범위 | 채점 모델 | FR | 실패/판정 |', '|---|---|---|---|']
    lines += [f"| {r['target_scope']} | {r['judge']} | {_pct(r['FR'])} | {r['fail']}/{r['decided']} |" for r in a['self_judging']]
    lines += ['', '같은 대상 범위에서 두 채점 모델의 FR을 비교한다. 자기 응답에서만 한 채점 모델의 FR이 눈에 띄게 낮으면 자기 채점 관대함을 의심한다.']
    return '\n'.join(lines)


def _pct(x):
    return '—' if x is None else f'{x * 100:.1f}%'


def _num(x):
    return '—' if x is None else f'{x:.3f}'
