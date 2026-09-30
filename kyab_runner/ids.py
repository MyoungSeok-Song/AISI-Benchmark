"""ID 발급: run_batch_id, run_id, response_id.

형식은 코드북 정규식을 따른다.
  RBATCH-YYYYMMDD-###     실행 명령 1회 = 배치 1개 (C10 기본값)
  RUN-YYYYMMDD-######     전역 고유
  RESP-########           전역 고유

'전역'의 범위는 출력 루트 폴더 하나다. 시작할 때 그 아래의 모든 배치 폴더를 훑어
가장 큰 번호를 찾고, 그 뒤부터 메모리에서 센다. 같은 출력 루트에 두 실행기를
동시에 돌리는 경우는 다루지 않는다(번호가 겹칠 수 있음).
"""
import re
from pathlib import Path

from . import csv_io

_RE_BATCH = re.compile(r"^RBATCH-([0-9]{8})-([0-9]{3})$")
_RE_RUN = re.compile(r"^RUN-([0-9]{8})-([0-9]{6})$")
_RE_RESP = re.compile(r"^RESP-([0-9]{8})$")


class IdAllocator:
    def __init__(self, codebook, out_root, today):
        """today: 'YYYYMMDD'. 배치·실행 ID의 날짜 부분."""
        self._today = today
        self._out_root = Path(out_root)
        self._run_seq = 0       # 오늘 날짜로 발급된 run_id의 최대 순번
        self._resp_seq = 0      # 전체 response_id의 최대 순번
        for batch_dir in self._batch_dirs():
            for row in csv_io.read_if_exists(codebook, "04_runs", batch_dir / "04_runs.csv"):
                m = _RE_RUN.match(row["run_id"])
                if m and m.group(1) == today:
                    self._run_seq = max(self._run_seq, int(m.group(2)))
            for row in csv_io.read_if_exists(codebook, "05_responses", batch_dir / "05_responses.csv"):
                m = _RE_RESP.match(row["response_id"])
                if m:
                    self._resp_seq = max(self._resp_seq, int(m.group(1)))

    def _batch_dirs(self):
        if not self._out_root.exists():
            return []
        return sorted(p for p in self._out_root.iterdir() if p.is_dir() and _RE_BATCH.match(p.name))

    def new_batch_id(self):
        """오늘 날짜의 다음 배치 번호. 폴더 이름이 곧 배치 ID다."""
        used = [int(_RE_BATCH.match(p.name).group(2)) for p in self._batch_dirs()
                if _RE_BATCH.match(p.name).group(1) == self._today]
        return f"RBATCH-{self._today}-{max(used, default=0) + 1:03d}"

    def new_run_id(self):
        self._run_seq += 1
        return f"RUN-{self._today}-{self._run_seq:06d}"

    def new_response_id(self):
        self._resp_seq += 1
        return f"RESP-{self._resp_seq:08d}"
