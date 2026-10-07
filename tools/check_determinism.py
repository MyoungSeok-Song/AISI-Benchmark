"""반복 실행 사이에 응답이 같은지 확인한다 (temperature 0.0에서 결정적 생성 여부).

같은 (문항, 턴)을 rollout 1·2·3으로 돌린 05 response_text를 비교해 서로 다른 응답의 가짓수를 센다.
출력은 턴 순서이므로, 다중턴 문항에서는 처음 갈린 턴이 원인이고 그 뒤 턴은 입력(앞 턴 응답)이 이미
달라서 갈렸을 수 있다. 차단·오류·빈 응답(길이 0)도 본문으로 비교하므로 섞여 있으면 '다름'으로 센다.

  .venv/bin/python tools/check_determinism.py samples/output/RBATCH-20261005-001 [배치 폴더 ...]   (runner/ 폴더에서)

종료 코드: 0 모든 턴이 동일, 1 하나라도 다름(기록은 그대로 둔다), 2 배치를 읽을 수 없음(없는 폴더, 깨진 CSV, 짝 없는 응답 행).
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNNER_DIR))

from kyab_runner import csv_io                       # noqa: E402
from kyab_runner.codebook import load_codebook       # noqa: E402
from kyab_runner.context import SETUP_ERRORS, setup_error_message   # noqa: E402
from kyab_runner.layout import RESPONSES_FILE, RUNS_FILE            # noqa: E402
from kyab_runner.taxonomy import load_taxonomy       # noqa: E402

EXIT_SAME, EXIT_DIFFERENT, EXIT_INVALID = 0, 1, 2


class OrphanResponse(Exception):
    """04에 없는 run_id를 가리키는 05 행(중단된 실행이 남긴 것)."""


def compare(codebook, batch_dir):
    """배치 1개를 비교한다. 반환: (비교한 턴 수, 응답이 갈린 턴 목록)."""
    runs = {r["run_id"]: r for r in csv_io.read_table(codebook, "04_runs", batch_dir / RUNS_FILE)}
    texts = defaultdict(dict)                        # (item_id, turn_id) -> {rollout_no: 응답 본문}
    for row in csv_io.read_table(codebook, "05_responses", batch_dir / RESPONSES_FILE):
        run = runs.get(row["run_id"])
        if run is None:
            raise OrphanResponse(f"{row['response_id']}: 04_runs에 없는 실행 {row['run_id']} (중단된 실행의 짝 없는 응답 행 — "
                                 "먼저 --batch-id로 이어서 실행하면 정리된다)")
        texts[(run["item_id"], row["turn_id"])][run["rollout_no"]] = row["response_text"]

    differing = []
    for (item_id, turn_id), by_rollout in sorted(texts.items()):
        distinct = len(set(by_rollout.values()))
        print(f"  {item_id} {turn_id}  반복 {len(by_rollout)}회  서로 다른 응답 {distinct}종"
              f"  길이 {sorted(len(t) for t in by_rollout.values())}")
        if distinct > 1:
            differing.append((item_id, turn_id))
    return len(texts), differing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batches", nargs="+", type=Path, help="배치 폴더")
    args = parser.parse_args(argv)
    try:
        codebook = load_codebook(load_taxonomy())
    except SETUP_ERRORS as exc:
        print(setup_error_message(exc))
        return EXIT_INVALID
    invalid = differed = False
    for batch_dir in args.batches:
        print(batch_dir.name)
        try:
            total, differing = compare(codebook, batch_dir)
        except (csv_io.CsvFormatError, FileNotFoundError, OrphanResponse) as exc:
            print(f"{batch_dir}: 배치를 읽을 수 없습니다: {exc}")
            invalid = True
            continue
        print(f"  → {total}개 턴 중 {total - len(differing)}개 동일, {len(differing)}개 다름")
        differed = differed or bool(differing)
    return EXIT_INVALID if invalid else EXIT_DIFFERENT if differed else EXIT_SAME


if __name__ == "__main__":
    sys.exit(main())
