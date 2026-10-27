"""Shared paths, package loading and logging for the pilot pipeline."""
import glob
import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.pylib'))
WORKSPACE = ROOT.parent
PKG = Path(glob.glob(str(WORKSPACE / 'LLM*_20261023' / '*' / '05_*' / 'reference.py'))[0])
_spec = importlib.util.spec_from_file_location('reference', PKG)
reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reference)

VERSIONS = {'package_version': 'llm-pilot-0.1.1', 'rubric_version': 'expert-v0.6-20261019',
            'metric_version': 'review-v0.6-20261019'}
AGE_BANDS = ['10-12', '13-15', '16-18']
GENDERS = ['M', 'F']
CONDITIONS = [(a, g) for a in AGE_BANDS for g in GENDERS]


def frozen_cards(version=None):
    """Latest frozen condition table, or the one with this card_version (results must use the version they were run with)."""
    files = sorted(p for p in (ROOT / 'conditions/frozen').glob('조건표_pilot50_*.json') if not p.name.endswith('.sha256.json'))
    if version:
        files = [p for p in files if p.name == f'조건표_pilot50_{version}.json']
        if not files:
            raise SystemExit(f'frozen condition table {version} not found')
    body = json.loads(files[-1].read_text(encoding='utf-8'))
    return files[-1].name, {c['item_id']: c for c in body['cards']}


def items():
    return {r['item_id']: r for r in json.loads((ROOT / 'conditions/pilot50_items.json').read_text(encoding='utf-8'))}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def slug(text):
    return ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in text)


_LOG_LOCK = threading.Lock()


def append_jsonl(path, record):
    line = json.dumps(record, ensure_ascii=False) + '\n'
    with _LOG_LOCK:  # calls run in worker threads; one record per line, never interleaved
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(line)


def write_json(path, value, overwrite=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f'{path} exists; raw records are never overwritten')
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    for key in ['run_tag', 'protocol_version', 'targets', 'judges', 'persona', 'retries']:
        if key not in cfg:
            raise SystemExit(f'config missing {key}')
    return cfg


class Timer:
    def __enter__(self):
        self.t = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.seconds = time.monotonic() - self.t
