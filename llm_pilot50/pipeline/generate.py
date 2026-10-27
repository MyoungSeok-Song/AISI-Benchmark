"""Generate target-model dialogues: items x 6 user conditions x targets, turn by turn.

Usage: python pipeline/generate.py <config.json>
Writes runs/<run_tag>/dialogues/<target>/<item>__<age>_<gender>.json and appends every API
call to runs/<run_tag>/logs/generation.jsonl. Existing dialogues are skipped (resume).
"""
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import adapters
from common import CONDITIONS, ROOT, append_jsonl, frozen_cards, items, load_config, now, slug, write_json


_VARIANTS = {}


def load_variants(cfg):
    """Variant file (method 1): {"variant_version", "rule_version", "items": [{item_id, age_band, gender, turns[{turn,text}], persona_record}]}"""
    path = (ROOT / cfg['persona']['file']).resolve()
    if path not in _VARIANTS:
        raw = path.read_bytes()
        body = json.loads(raw)
        table = {}
        for v in body['items']:
            key = (v['item_id'], v['age_band'], v['gender'])
            if key in table:
                raise SystemExit(f'duplicate variant {key}')
            table[key] = v
        _VARIANTS[path] = {'table': table, 'meta': {'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                                    'variant_version': body.get('variant_version'), 'rule_version': body.get('rule_version')}}
    return _VARIANTS[path]


def persona_plan(cfg, item, age, gender):
    """Return (user texts by turn, system text or None, record kept in the dialogue file but never sent).

    status: decided (template prefix/system), variants (method 1), context (method 2), variants+context, none_smoke.
    """
    p = cfg['persona']
    texts = {m['turn']: m['content'] for m in item['user_messages']}
    status = p['status']
    if status == 'none_smoke':
        if not cfg.get('smoke'):
            raise SystemExit('persona none_smoke is only allowed when "smoke": true')
        return texts, None, {'status': status}
    if status == 'pending' or status not in {'decided', 'variants', 'context', 'variants+context'}:
        raise SystemExit('persona injection is not decided (01_결정기록 3절). Generation is blocked; use a smoke config.')
    record, system = {'status': status}, None
    if status == 'decided':
        prefix = p['template'].format(age=p['age_text'][age], gender=p['gender_text'][gender])
        if p['position'] == 'system':
            system = prefix
        else:
            texts[1] = f'{prefix} {texts[1]}'
        record.update(position=p['position'], text=prefix)
    if status in {'variants', 'variants+context'}:
        vs = load_variants(cfg)
        v = vs['table'].get((item['item_id'], age, gender))
        if v is None:
            raise SystemExit(f"no variant for {item['item_id']} {age}/{gender} in {vs['meta']['file']}")
        turns = {t['turn']: t['text'] for t in v['turns']}
        if sorted(turns) != sorted(texts) or not all(x.strip() for x in turns.values()):
            raise SystemExit(f"variant turns for {item['item_id']} {age}/{gender} do not match planned turns {sorted(texts)}")
        texts = turns
        record.update(variant=vs['meta'], persona_record=v.get('persona_record', {}))
    if status in {'context', 'variants+context'}:
        c = p['context']
        system = c['template'].format(age=c['age_text'][age], gender=c['gender_text'][gender])
        record.update(context_text=system)
    return texts, system, record


def bad_response(r):
    """Technical failures are retried; a provider content filter with text is kept and flagged."""
    if not r['ok']:
        return r['error']
    if r.get('finish_reason') == 'length':
        return 'truncated (finish_reason=length)'
    if not r['text'].strip():
        return f"empty response (finish_reason={r.get('finish_reason')})"
    return None


def run_dialogue(cfg, target, adapter, item, card, age, gender, log):
    texts, system, persona = persona_plan(cfg, item, age, gender)
    run_id = f"{cfg['run_tag']}__{slug(target['name'])}__{slug(item['item_id'])}__{age}_{gender}"
    messages, sent = [], []
    if system:
        messages.append({'message_id': 's0', 'turn': 0, 'role': 'system', 'content': system, 'integrity': 'complete'})
        sent.append({'role': 'system', 'content': system})
    flags, status, completion = [], 'ok', 'complete'
    returned = set()
    for um in item['user_messages']:
        t = um['turn']
        text = texts[t]
        messages.append({'message_id': f'u{t}', 'turn': t, 'role': 'user', 'content': text, 'integrity': 'complete'})
        sent.append({'role': 'user', 'content': text})
        result = None
        for attempt in range(1, cfg['retries']['generation'] + 1):
            r = adapter.chat(sent)
            problem = bad_response(r)
            append_jsonl(log, {'ts': now(), 'run_id': run_id, 'turn': t, 'attempt': attempt, 'model': target['model_id'],
                               'request': r.get('request'), 'ok': r['ok'], 'problem': problem, 'finish_reason': r.get('finish_reason'),
                               'usage': r.get('usage'), 'seconds': r.get('seconds'), 'waits': r.get('waits'), 'raw': r.get('raw'), 'error': r.get('error')})
            if problem is None:
                result = r
                if attempt > 1:
                    flags.append(f'T{t}: 기술 재시도 {attempt - 1}회 후 성공')
                if r.get('finish_reason') == 'content_filter':
                    flags.append(f'T{t}: 공급사 필터(content_filter) 응답 — 그대로 채점, 판단이 어려운 사례')
                break
        if result is None:
            messages.append({'message_id': f'a{t}', 'turn': t, 'role': 'assistant', 'content': '', 'integrity': 'missing'})
            flags.append(f"T{t}: 기술 오류로 응답 없음({cfg['retries']['generation']}회 시도). 이후 턴 미진행")
            status, completion = 'error', 'error'
            break
        messages.append({'message_id': f'a{t}', 'turn': t, 'role': 'assistant', 'content': result['text'], 'integrity': 'complete'})
        returned.add(result.get('model_returned') or target['model_id'])
        sent.append({'role': 'assistant', 'content': result['text']})
    return {'run_id': run_id, 'run_tag': cfg['run_tag'], 'smoke': bool(cfg.get('smoke')), 'protocol_version': cfg['protocol_version'],
            'target': {k: target[k] for k in ['name', 'provider', 'model_id']} | {'settings': adapter.settings, 'model_returned': sorted(returned)},
            'item_id': item['item_id'], 'item_version': item['item_version'], 'card_version': card['card_version'],
            'condition': {'age_band': age, 'gender': gender},
            'persona': persona,
            'system_prompt': system or 'none',
            'execution_status': status, 'completion': completion, 'messages': messages, 'flags': flags, 'finished_at': now()}


def main(cfg_path):
    cfg = load_config(cfg_path)
    _, cards = frozen_cards(cfg.get('card_version'))  # pin with "card_version"; default = latest frozen
    src = items()
    chosen = cfg.get('items') or list(src)
    conds = [c for c in CONDITIONS if not cfg.get('conditions') or f'{c[0]}/{c[1]}' in cfg['conditions']]
    out = ROOT / 'runs' / cfg['run_tag']
    log = out / 'logs/generation.jsonl'
    tasks = []
    for target in cfg['targets']:
        adapter = adapters.make(target)
        for iid in chosen:
            for age, gender in conds:
                path = out / 'dialogues' / slug(target['name']) / f'{slug(iid)}__{age}_{gender}.json'
                if not path.exists():
                    tasks.append((target, adapter, iid, age, gender, path))
    for _, _, iid, age, gender, _ in tasks:  # validate every persona/variant before the first API call
        persona_plan(cfg, src[iid], age, gender)
    workers = cfg.get('concurrency', {}).get('generation', 1)

    def one(task):
        target, adapter, iid, age, gender, path = task
        d = run_dialogue(cfg, target, adapter, src[iid], cards[iid], age, gender, log)
        write_json(path, d)
        return d

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, t) for t in tasks]):
            d = fut.result()
            done += 1
            print(f"[gen {done}/{len(tasks)}] {d['run_id']} {d['completion']}", flush=True)
    print(f'generated {done} dialogues -> {out / "dialogues"}')


if __name__ == '__main__':
    main(sys.argv[1])
