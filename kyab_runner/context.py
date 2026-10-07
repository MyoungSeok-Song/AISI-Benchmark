"""판정·집계·납품 단계 도구가 함께 쓰는 준비 절차.

run_judge, run_aggregate, apply_judgments, export, tools/e2e_check는 모두 같은 것을 읽고 시작한다(prepare):
명세(코드북·분류체계) → 설정 → 판정·집계 규칙 → 입력 3종 → 배치 폴더.
실패하면 한 줄 메시지를 출력하고 None을 돌려주며, 진입점은 종료 코드 2로 끝낸다.
준비 절차를 바꿀 때는 여기만 고친다 — 진입점이 이 순서를 따로 베껴 두면 변경이 거기까지 닿지 않는다.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import csv_io, paths, validate
from .codebook import Codebook, load_codebook
from .config import RunnerConfig, load_config
from .errors import SetupError
from .records import BatchRecords, BatchView, InputIndex, RecordsError      # noqa: F401 (RecordsError는 이 모듈 이름으로도 쓴다)
from .rules import Rules, load_rules
from .taxonomy import load_taxonomy
from .vocab import check_vocabulary


# 명세·설정 파일이 잘못됐을 때 나는 예외. 진입점은 이것을 잡아 한 줄 메시지와 종료 코드 2로 끝낸다(traceback 없이).
# 도메인 예외(ConfigError·RulesError·OverlayError…)는 SetupError를 상속한다. FileNotFoundError·KeyError·YAMLError는
# 로더가 아직 모두 감싸지 못한 경로를 위해 남겨 둔다.
SETUP_ERRORS = (SetupError, FileNotFoundError, KeyError, yaml.YAMLError)


def setup_error_message(exc):
    """SETUP_ERRORS를 잡은 진입점이 출력하는 한 줄."""
    return f"명세·설정을 읽을 수 없습니다: {type(exc).__name__}: {exc}"


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
    check_vocabulary(codebook)                       # 코드가 쓰는 통제어휘가 이 코드북에 있는지(vocab)
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
        raise RecordsError(f"입력 검증 오류 {len(errors)}건 — " + "; ".join(str(i) for i in errors[:3]), issues)
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


# 배치·입력을 읽다 나는 예외(설정 오류와 구분). 깨진 batch_manifest.json(JSON·필수 키)은 BatchRecords가 RecordsError로 바꾼다.
READ_ERRORS = (csv_io.CsvFormatError, FileNotFoundError, RecordsError)


def report_read_error(exc):
    """배치·입력 읽기 실패를 출력한다. 입력 검증 결과가 있으면(RecordsError.issues) 처음 3건이 아니라 전부 보인다."""
    issues = getattr(exc, "issues", [])
    if issues:
        validate.report_issues(issues)
    print(f"배치 또는 입력을 읽을 수 없습니다: {exc}")


@dataclass(frozen=True)
class Prepared:
    """prepare의 결과. 입력 색인(index)은 납품 내보내기처럼 문항 전체가 필요한 진입점이 쓴다."""
    env: Environment
    index: InputIndex
    views: list


def prepare(input_dir, batch_dirs, rules_yaml=paths.AGGREGATION_RULES_YAML):
    """진입점 공통 준비: 명세·설정·규칙 → 입력 3종·배치 폴더. 반환: Prepared(env, index, views) — 실패하면 출력 뒤 None.

    규칙 파일 로드 경고와 입력 안내 문구는 '주의:'로 출력한다.
    """
    try:
        env = load_environment(rules_yaml)
    except SETUP_ERRORS as exc:
        print(setup_error_message(exc))
        return None
    for warning in env.rules.load_warnings:
        print(f"주의: {warning}")
    try:
        index, views, notices = open_views(env, input_dir, batch_dirs)
    except READ_ERRORS as exc:
        report_read_error(exc)
        return None
    for notice in notices:
        print(f"주의: {notice}")
    return Prepared(env, index, views)
