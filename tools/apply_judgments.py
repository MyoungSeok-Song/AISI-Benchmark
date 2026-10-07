"""판정 결과로 04_runs의 first_fail_turn · first_cfc_turn 채우기 — kyab_runner.apply_judgments의 얇은 껍데기.

  .venv/bin/python tools/apply_judgments.py <배치 폴더> [배치 폴더 ...] [--input 입력 폴더] [--dry-run]   (runner/ 폴더에서)
규칙과 종료 코드는 kyab_runner/apply_judgments.py의 설명을 따른다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kyab_runner.apply_judgments import TARGET_FIELDS, main    # noqa: E402, F401 (테스트가 TARGET_FIELDS를 쓴다)

if __name__ == "__main__":
    sys.exit(main())
