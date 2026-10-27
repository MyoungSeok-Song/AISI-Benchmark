"""Tag every card flag with its triage category from conditions/flag_triage.json.

Idempotent: already tagged flags are re-derived from the untagged text. Run after any build_*.py.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
TRIAGE = json.loads((HERE / 'conditions/flag_triage.json').read_text(encoding='utf-8'))
TAGS = ('자체결정', '기록', '연구팀확인')
TAG = re.compile(r'^\[(%s)\] ' % '|'.join(TAGS))


def main():
    seen = set()
    for f in sorted((HERE / 'conditions/cards').glob('*.json')):
        cards = json.loads(f.read_text(encoding='utf-8'))
        for c in cards:
            raw = [TAG.sub('', x) for x in c['flags']]
            plan = TRIAGE.get(c['item_id'], [])
            if len(plan) != len(raw):
                raise SystemExit(f"{c['item_id']}: {len(raw)} flags but {len(plan)} triage entries")
            out = []
            for text, (tag, new) in zip(raw, plan):
                if tag not in TAGS:
                    raise SystemExit(f"{c['item_id']}: unknown tag {tag}")
                body = TAG.sub('', new) if new else text
                out.append(f'[{tag}] {body}')
            c['flags'] = out
            seen.add(c['item_id'])
        f.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding='utf-8')
    extra = set(TRIAGE) - {'_doc'} - seen
    if extra:
        raise SystemExit(f'triage for unknown items: {sorted(extra)}')
    print('tagged', len(seen), 'cards')


if __name__ == '__main__':
    main()
