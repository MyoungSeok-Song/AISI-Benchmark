"""기록된 배치와 입력 3종을 읽는다. 판정·집계 단계가 함께 쓴다.

실행기(session.Batch)는 배치를 쓰는 쪽이고, 이 모듈은 끝난 배치를 읽는 쪽이다.

  load_inputs    입력 3종(01·02·03) 읽기 + 파일 해시
  InputIndex     입력 3종을 문항 판본 키·턴 ID로 찾는 색인
  BatchRecords   배치 폴더 1개 (manifest, 04_runs, 05_responses)
  BatchView      배치 기록과 입력을 이어 붙인 조회 (응답 → 실행 → 문항 → 현재 태그)

코드북의 연결 경로는 responses.run_id → runs.(item_id, item_version) → items / item_tags,
responses.turn_id → prompts 이다. 06_judgments는 response_id로만 이어지므로(구조 검토 S2)
판정·집계 코드는 BatchView를 거쳐 문항과 태그를 찾는다.
"""
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import csv_io
from .validate import item_key

INPUT_FILES = {"01_items": "01_items.csv", "02_item_tags": "02_item_tags.csv", "03_prompts": "03_prompts.csv"}
MANIFEST_FILE = "batch_manifest.json"
RUNS_FILE = "04_runs.csv"
RESPONSES_FILE = "05_responses.csv"
EVENTS_FILE = "runner_events.jsonl"


def load_inputs(codebook, input_dir):
    """입력 3종을 읽는다. 반환: ({표 이름: 행 목록}, {파일명: sha256})."""
    tables, digests = {}, {}
    for table, filename in INPUT_FILES.items():
        path = Path(input_dir) / filename
        tables[table] = csv_io.read_table(codebook, table, path)
        digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return tables, digests


class InputIndex:
    """입력 3종의 색인. 키는 문항 판본 (item_id, item_version)."""

    def __init__(self, items, tags, prompts, digests=None):
        self.digests = digests or {}
        self.items = {item_key(r): r for r in items}
        self.current_tag = {item_key(r): r for r in tags if r["tag_status"] == "current"}
        self.tag_revisions = defaultdict(dict)          # 문항 판본 -> {tag_revision: 태그 행}
        for row in tags:
            self.tag_revisions[item_key(row)][row["tag_revision"]] = row
        self.turn = {r["turn_id"]: r for r in prompts}
        self.turns = defaultdict(list)                  # 문항 판본 -> 턴 행 (turn_index 오름차순)
        for row in sorted(prompts, key=lambda r: int(r["turn_index"])):
            self.turns[item_key(row)].append(row)

    @classmethod
    def load(cls, codebook, input_dir):
        tables, digests = load_inputs(codebook, input_dir)
        index = cls(tables["01_items"], tables["02_item_tags"], tables["03_prompts"], digests)
        index.tables = tables                           # 검증기에 넘길 원본 행 목록
        return index


class BatchRecords:
    """끝난 배치 폴더 1개. session.Batch와 같은 조회 메서드를 갖는다(judge_io가 둘 다 받는다)."""

    def __init__(self, codebook, config, batch_dir):
        self.codebook = codebook
        self.config = config
        self.dir = Path(batch_dir)
        self.manifest = json.loads((self.dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        self.run_batch_id = self.manifest["run_batch_id"]
        self.protocol_id = self.manifest["protocol_id"]

    def recorded_runs(self):
        return csv_io.read_if_exists(self.codebook, "04_runs", self.dir / RUNS_FILE)

    def recorded_responses(self):
        return csv_io.read_if_exists(self.codebook, "05_responses", self.dir / RESPONSES_FILE)

    def now(self):
        """ISO 8601 타임스탬프 (시간대 포함, 밀리초). session.Batch.now와 같은 표기."""
        return datetime.now(ZoneInfo(self.config["timezone"])).isoformat(timespec="milliseconds")

    def log_event(self, event, **fields):
        """배치의 보조 로그(runner_events.jsonl)에 한 줄 추가한다."""
        with open(self.dir / EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": self.now(), "event": event, **fields}, ensure_ascii=False) + "\n")

    def changed_inputs(self, index):
        """실행 때와 내용이 달라진 입력 파일 이름 목록.

        02_item_tags는 실행 뒤에 태그 판본이 추가될 수 있어 달라져도 정상이다.
        01·03이 달라졌으면 호출자가 경고한다(같은 문항 판본의 내용이 바뀌었을 수 있다).
        """
        saved = self.manifest.get("input_sha256", {})
        return [name for name, digest in index.digests.items() if saved.get(name) not in (None, digest)]


class BatchView:
    """배치 기록 + 입력 색인. 응답에서 실행·문항·태그·턴 번호를 찾는다."""

    def __init__(self, batch, index):
        self.batch = batch
        self.index = index
        self.runs = {r["run_id"]: r for r in batch.recorded_runs()}
        self.responses = {r["response_id"]: r for r in batch.recorded_responses()}
        self.responses_by_run = defaultdict(list)       # run_id -> 응답 행 (턴 순서)
        for row in self.responses.values():
            self.responses_by_run[row["run_id"]].append(row)
        for rows in self.responses_by_run.values():
            # 입력에 없는 턴은 0으로 둔다. 그런 기록은 dangling()이 알려 주고 호출자가 멈춘다.
            rows.sort(key=lambda r: self.turn_index(r) if r["turn_id"] in index.turn else 0)

    def turn_index(self, response):
        return int(self.index.turn[response["turn_id"]]["turn_index"])

    def run_of(self, response):
        return self.runs[response["run_id"]]

    def item_of(self, run):
        return self.index.items[item_key(run)]

    def current_tag_of(self, run):
        return self.index.current_tag[item_key(run)]

    def successes(self, run_id):
        """그 실행의 성공 응답 (턴 순서)."""
        return [r for r in self.responses_by_run[run_id] if r["response_status"] == "success"]

    def dangling(self):
        """입력에서 찾을 수 없는 연결. 반환: 설명 문자열 목록(없으면 빈 목록)."""
        problems = []
        for run in self.runs.values():
            if item_key(run) not in self.index.items:
                problems.append(f"{run['run_id']}: 01_items에 없는 문항 판본 {item_key(run)}")
            elif item_key(run) not in self.index.current_tag:
                problems.append(f"{run['run_id']}: 02_item_tags에 current 태그가 없음 {item_key(run)}")
        for response in self.responses.values():
            if response["run_id"] not in self.runs:
                problems.append(f"{response['response_id']}: 04_runs에 없는 실행 {response['run_id']}")
            if response["turn_id"] not in self.index.turn:
                problems.append(f"{response['response_id']}: 03_prompts에 없는 턴 {response['turn_id']}")
        return problems
