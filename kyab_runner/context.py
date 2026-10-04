"""판정·집계 단계 도구가 함께 쓰는 준비 절차.

run_judge, run_aggregate, tools/apply_judgments.py는 모두 같은 것을 읽고 시작한다:
명세(코드북·분류체계) → 설정 → 판정·집계 규칙 → 입력 3종 → 배치 폴더.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import paths, validate
from .codebook import Codebook, OverlayError, load_codebook
from .config import ConfigError, RunnerConfig, load_config
from .records import BatchRecords, BatchView, InputIndex
from .rules import Rules, RulesError, load_rules
from .taxonomy import load_taxonomy


class RecordsError(Exception):
    """배치 기록이 입력과 이어지지 않을 때."""


# 명세·설정 파일이 잘못됐을 때 나는 예외. 진입점은 이것을 잡아 한 줄 메시지와 종료 코드 2로 끝낸다(traceback 없이).
SETUP_ERRORS = (OverlayError, RulesError, ConfigError, FileNotFoundError, KeyError, yaml.YAMLError)


@dataclass(frozen=True)
class Environment:
    codebook: Codebook
    config: RunnerConfig
    rules: Rules
    taxonomy: object = None


def load_environment(rules_yaml=paths.AGGREGATION_RULES_YAML):
    """명세·설정·규칙을 읽는다. rules_yaml로 다른 규칙 파일을 줄 수 있다(규칙을 바꿔 비교할 때)."""
    taxonomy = load_taxonomy()
    codebook = load_codebook(taxonomy)
    return Environment(codebook=codebook, config=load_config(), rules=load_rules(codebook, rules_yaml=Path(rules_yaml)),
                       taxonomy=taxonomy)


def open_views(env, input_dir, batch_dirs):
    """입력 3종과 배치 폴더들을 연다. 반환: (InputIndex, [BatchView], [안내 문구]).

    입력 3종은 실행기와 같은 검증(validate_inputs)을 거친다 — 실행 뒤 02를 고치는 경로(새 태그 판본)가 정상 절차이므로
    여기서도 오류가 있으면 RecordsError로 멈춘다. 경고는 안내 문구로 돌려준다.
    배치 기록이 입력의 문항·턴·현재 태그와 이어지지 않으면 RecordsError.
    01·03 입력 파일이 실행 때와 달라졌으면 안내 문구로 알린다(02는 태그 판본 추가로 달라질 수 있다).
    """
    index = InputIndex.load(env.codebook, input_dir)
    issues = validate.validate_inputs(env.codebook, env.taxonomy, env.config, *index.tables.values())
    errors = validate.errors_of(issues)
    if errors:
        raise RecordsError(f"입력 검증 오류 {len(errors)}건 — " + "; ".join(str(i) for i in errors[:3]))
    notices, views = [str(i) for i in issues], []        # 입력 검증 경고를 안내 문구로
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
