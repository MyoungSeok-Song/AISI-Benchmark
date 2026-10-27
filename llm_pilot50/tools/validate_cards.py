"""Validate pilot condition cards against item data, the authoring rules and the package's input checks.

Usage: python validate_cards.py <cards.json or directory> [...]
Each file holds a JSON array of cards. No model is called.
"""
import glob
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ITEMS = {r['item_id']: r for r in json.loads((HERE.parent / 'conditions/pilot50_items.json').read_text(encoding='utf-8'))}
PKG = Path(glob.glob(str(ROOT / 'LLM*_20261023' / '*' / '05_*' / 'reference.py'))[0])
spec = importlib.util.spec_from_file_location('reference', PKG)
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)

CARD_KEYS = {'item_id', 'item_version', 'card_version', 'item', 'rationale', 'age_notes', 'flags'}
TURN_ID = re.compile(r'^T(\d)-[EF]\d+$')
DLG_ID = re.compile(r'^DLG-[EF]\d+$')


def synthetic_input(card, age_band='13-15'):
    src = ITEMS[card['item_id']]
    messages = []
    for m in src['user_messages']:
        messages.append({'message_id': m['message_id'], 'turn': m['turn'], 'role': 'user',
                         'content': m['content'], 'integrity': 'complete'})
        messages.append({'message_id': f"a{m['turn']}", 'turn': m['turn'], 'role': 'assistant',
                         'content': '(검증용 자리표시 응답)', 'integrity': 'complete'})
    run = {'run_id': 'validate', 'item_id': card['item_id'], 'item_version': card['item_version'],
           'model_snapshot': 'none', 'protocol_version': 'validate', 'repeat_id': 1, 'attempt_id': 1,
           'judge_id': 'none', 'judge_model': 'none', 'judge_settings': {},
           'execution_status': 'ok', 'completion': 'complete'}
    item = dict(card['item'], age_band=age_band)
    return {'package_version': 'llm-pilot-0.1.1', 'rubric_version': 'expert-v0.6-20261019',
            'metric_version': 'review-v0.6-20261019', 'run': run, 'item': item, 'messages': messages}


def check(card):
    errs = []
    def need(ok, msg):
        if not ok:
            errs.append(msg)
    need(set(card) == CARD_KEYS, f'card keys {sorted(set(card) ^ CARD_KEYS)}')
    src = ITEMS.get(card.get('item_id'))
    if src is None:
        return ['unknown item_id']
    it = card['item']
    need('age_band' not in it, 'age_band must be added at run time')
    for k in ['risk_group', 'request_type', 'safe_control', 'planned_turn_count']:
        need(it.get(k) == src[k], f'{k} differs from item data')
    need(card['item_version'] == src['item_version'], 'item_version')
    need(it.get('crri_applicable') is True, 'crri_applicable must be true')
    multi = src['planned_turn_count'] > 1
    need(it.get('d6_applied') is multi and it.get('d6_required') is multi, 'd6 flags per research reply')
    need(it.get('verified_resource_facts') == [], 'resource facts are filled at run time')
    ids = []
    for p in it.get('turn_plan', []):
        need({'D1', 'D3'} <= set(p['required_dimensions']), f"T{p['turn']}: D1/D3 required")
        need(p['d1_d3_exception_reason'] == '', f"T{p['turn']}: no D1/D3 exception")
        need(len(p['expected_safe_requirements']) >= 1, f"T{p['turn']}: expected_safe_requirements")
        for x in p['essential_actions'] + p['failure_conditions']:
            mt = TURN_ID.match(x['id'])
            need(bool(mt) and int(mt.group(1)) == p['turn'], f"bad id {x['id']}")
            ids.append(x['id'])
        if 'D4' not in p['applied_dimensions']:
            need(not any(p['d4_components'].values()), f"T{p['turn']}: d4 components without D4")
    for x in it.get('dialogue_essential_actions', []) + it.get('dialogue_failure_conditions', []):
        need(bool(DLG_ID.match(x['id'])), f"bad id {x['id']}")
        ids.append(x['id'])
    need(len(ids) == len(set(ids)), 'duplicate condition ids')
    rt = card['rationale']
    need(isinstance(rt.get('request_type'), str) and rt['request_type'], 'rationale.request_type')
    need([t['turn'] for t in rt.get('turns', [])] == list(range(1, src['planned_turn_count'] + 1)), 'rationale.turns coverage')
    covered = {k for t in rt.get('turns', []) for k in t.get('conditions', {})}
    need(covered >= {i for i in ids if i.startswith('T')}, f'missing rationale for {sorted({i for i in ids if i.startswith("T")} - covered)}')
    dlg = [i for i in ids if i.startswith('DLG')]
    need(all(i in rt.get('dialogue', '') for i in dlg), 'dialogue rationale must name each DLG id')
    need(isinstance(card['age_notes'], str) and card['age_notes'], 'age_notes')
    need(isinstance(card['flags'], list), 'flags list')
    try:
        reference.prepare_jobs(synthetic_input(card))
    except reference.ValidationError as e:
        errs.append(f'package validation: {e}')
    return errs


def main(paths):
    files = []
    for p in paths:
        p = Path(p)
        files += sorted(p.glob('*.json')) if p.is_dir() else [p]
    seen, bad = {}, 0
    for f in files:
        for card in json.loads(f.read_text(encoding='utf-8')):
            iid = card.get('item_id')
            errs = check(card)
            if iid in seen:
                errs.append(f'duplicate card (also in {seen[iid]})')
            seen[iid] = f.name
            if errs:
                bad += 1
                print(f'FAIL {iid} ({f.name})')
                for e in errs:
                    print('   -', e)
    missing = sorted(set(ITEMS) - set(seen))
    print(f'cards={len(seen)} failed={bad} missing={len(missing)}')
    if missing:
        print('missing:', ', '.join(missing))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
