"""반복 실행 사이에 응답이 같은지 확인한다 (temperature 0.0에서 결정적 생성 여부).

같은 문항·같은 턴을 rollout 1·2·3으로 돌린 응답 본문을 비교한다.
다중턴은 앞 턴 응답이 다르면 뒤 턴 입력도 달라지므로, 1턴부터 차례로 본다.

  runner/.venv/bin/python tools/check_determinism.py samples/output/RBATCH-20260930-005 [배치 폴더 ...]

종료 코드: 모든 턴이 동일하면 0, 하나라도 다르면 1. 다르더라도 기록은 그대로 둔다.
"""
import sys
from collections import defaultdict
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNNER_DIR))

from kyab_runner import csv_io                       # noqa: E402
from kyab_runner.codebook import load_codebook       # noqa: E402
from kyab_runner.context import SETUP_ERRORS         # noqa: E402
from kyab_runner.taxonomy import load_taxonomy       # noqa: E402


def compare(codebook, batch_dir):
    """배치 1개를 비교한다. 반환: (비교한 턴 수, 응답이 갈린 턴 목록)."""
    runs = {r["run_id"]: r for r in csv_io.read_table(codebook, "04_runs", batch_dir / "04_runs.csv")}
    texts = defaultdict(dict)                        # (item_id, turn_id) -> {rollout_no: 응답 본문}
    for row in csv_io.read_table(codebook, "05_responses", batch_dir / "05_responses.csv"):
        run = runs[row["run_id"]]
        texts[(run["item_id"], row["turn_id"])][run["rollout_no"]] = row["response_text"]

    differing = []
    for (item_id, turn_id), by_rollout in sorted(texts.items()):
        distinct = len(set(by_rollout.values()))
        print(f"  {item_id} {turn_id}  반복 {len(by_rollout)}회  서로 다른 응답 {distinct}종"
              f"  길이 {sorted(len(t) for t in by_rollout.values())}")
        if distinct > 1:
            differing.append((item_id, turn_id))
    return len(texts), differing


def main():
    try:
        codebook = load_codebook(load_taxonomy())
    except SETUP_ERRORS as exc:
        print(f"명세·설정을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
        return 2
    failed = False
    for arg in sys.argv[1:]:
        batch_dir = Path(arg)
        print(batch_dir.name)
        total, differing = compare(codebook, batch_dir)
        print(f"  → {total}개 턴 중 {total - len(differing)}개 동일, {len(differing)}개 다름")
        failed = failed or bool(differing)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
