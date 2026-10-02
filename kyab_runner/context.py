"""판정·집계 단계 도구가 함께 쓰는 준비 절차.

run_judge, run_aggregate, tools/apply_judgments.py는 모두 같은 것을 읽고 시작한다:
명세(코드북·분류체계) → 설정 → 판정·집계 규칙 → 입력 3종 → 배치 폴더.
"""
from dataclasses import dataclass
from pathlib import Path

from .codebook import Codebook, load_codebook
from .config import RunnerConfig, load_config
from .records import BatchRecords, BatchView, InputIndex
from .rules import Rules, load_rules
from .taxonomy import load_taxonomy


class RecordsError(Exception):
    """배치 기록이 입력과 이어지지 않을 때."""


@dataclass(frozen=True)
class Environment:
    codebook: Codebook
    config: RunnerConfig
    rules: Rules


def load_environment():
    codebook = load_codebook(load_taxonomy())
    return Environment(codebook=codebook, config=load_config(), rules=load_rules(codebook))


def open_views(env, input_dir, batch_dirs):
    """입력 3종과 배치 폴더들을 연다. 반환: (InputIndex, [BatchView], [안내 문구]).

    배치 기록이 입력의 문항·턴·현재 태그와 이어지지 않으면 RecordsError.
    01·03 입력 파일이 실행 때와 달라졌으면 안내 문구로 알린다(02는 태그 판본 추가로 달라질 수 있다).
    """
    index = InputIndex.load(env.codebook, input_dir)
    views, notices = [], []
    for batch_dir in batch_dirs:
        batch = BatchRecords(env.codebook, env.config, Path(batch_dir))
        view = BatchView(batch, index)
        problems = view.dangling()
        if problems:
            raise RecordsError(f"{batch.run_batch_id}: 입력과 이어지지 않는 기록 {len(problems)}건 — " + "; ".join(problems[:3]))
        changed = [name for name in batch.changed_inputs(index) if not name.startswith("02_")]
        if changed:
            notices.append(f"{batch.run_batch_id}: 실행 때와 내용이 다른 입력 파일 {changed}")
        views.append(view)
    return index, views, notices
