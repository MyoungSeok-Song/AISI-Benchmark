"""납품 형식(JSONL) 내보내기 명령행 — kyab_runner.export의 얇은 껍데기.

  runner/.venv/bin/python tools/export_jsonl.py <배치 폴더> [...] --out <납품 폴더> [--input 입력 폴더]
                                                [--results RESULTS 폴더] [--allow-mock-judge]
구성과 규칙은 납품형식_JSONL스키마_v0.1.md와 README '납품 형식(JSONL)' 절. 종료 코드: 0 정상, 2 거부·검증 실패.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kyab_runner.export import main    # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
