"""테스트 공용 기반(tooling-09·10): sys.path 설정 1회, 명세·설정 로드, 실행·판정 도우미 클래스.

모든 테스트 모듈은 이 모듈을 거쳐 kyab_runner와 tools/를 import한다(모듈마다 sys.path를 따로 만지지 않는다).
"""
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent
for entry in (str(RUNNER_DIR), str(RUNNER_DIR / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from kyab_runner import cli, csv_io, ids, judge_io, paths, run_judge, run_multiturn, run_single, validate   # noqa: E402
from kyab_runner.codebook import load_codebook                           # noqa: E402
from kyab_runner.config import load_config                               # noqa: E402
from kyab_runner.context import load_environment, open_views             # noqa: E402
from kyab_runner.taxonomy import load_taxonomy                           # noqa: E402

TAXONOMY = load_taxonomy()
CODEBOOK = load_codebook(TAXONOMY)        # 실행기와 같은 방식(ENV.codebook과 같은 내용이지만 따로 로드해 둔다)
CONFIG = load_config()
FAILURE_PLAN = paths.SAMPLES_DIR / "mock_plan_failures.yaml"
ENV = load_environment()
RULES = ENV.rules
SCORE_FIELDS = RULES.score_fields
CRRI = RULES.crri_axes
NONE = RULES.none_token                   # 치명적 실패 없음의 기록값(회신 ③)


def bumped(version):
    """비교용 규칙 파일에 쓸 다음 PATCH 판본 (현재 판본 리터럴을 테스트에 적지 않기 위해)."""
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


class RunnerTestCase(unittest.TestCase):
    """임시 출력 폴더와 실행·조회 도우미."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kyab_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "out"

    def capture(self, main, argv):
        """진입점을 돌리고 (종료 코드, 화면 출력)을 돌려준다. sys.exit(메시지)로 끝나면 코드 99에 메시지를 출력에 덧붙인다."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                code = main(argv)
            except SystemExit as exc:
                code, _ = 99, buffer.write(str(exc.code))
        return code, buffer.getvalue()

    def run_cli(self, runner, *extra, input_dir=None):
        """실행기를 돌리고 (종료 코드, 화면 출력)을 돌려준다."""
        argv = ["--out", str(self.out), "--allow-unverified", *extra]
        if input_dir:
            argv += ["--input", str(input_dir)]
        return self.capture(runner.main, argv)

    def batch_dirs(self):
        return sorted(p for p in self.out.iterdir() if p.is_dir())

    def table(self, batch_dir, table, filename=None):
        return csv_io.read_table(CODEBOOK, table, batch_dir / (filename or f"{table}.csv"))

    def copy_inputs(self):
        """샘플 입력을 임시 폴더에 복사해 고쳐 쓸 수 있게 한다. 반환: (폴더, {표: 행 목록})."""
        target = self.tmp / "input"
        shutil.copytree(paths.DEFAULT_INPUT_DIR, target)
        tables = {t: csv_io.read_table(CODEBOOK, t, target / f) for t, f in cli.INPUT_FILES.items()}
        return target, tables

    def save(self, input_dir, table, rows):
        csv_io.rewrite_table(CODEBOOK, table, input_dir / cli.INPUT_FILES[table], rows)


class JudgedTestCase(RunnerTestCase):
    """정상 배치 2개(단일 9실행, 3턴 9실행)를 만들고 모의 판정기로 판정해 둔다."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_cli(run_single)[0], 0)
        self.assertEqual(self.run_cli(run_multiturn)[0], 0)
        self.single_dir, self.multi_dir = self.batch_dirs()
        self.assertEqual(self.judge()[0], 0)
        _, (self.single, self.multi), _ = open_views(ENV, paths.DEFAULT_INPUT_DIR, [self.single_dir, self.multi_dir])

    def batch_dirs(self):
        """배치 폴더만 (출력 루트에는 집계 결과 폴더 RESULTS-…도 생긴다)."""
        return ids.batch_dirs(self.out)

    def judge(self, *extra, batches=None):
        """run_judge를 돌리고 (종료 코드, 화면 출력)을 돌려준다."""
        return self.capture(run_judge.main, [*extra, *map(str, batches or self.batch_dirs())])

    def judgments(self, batch_dir):
        return judge_io.load_judgments(CODEBOOK, batch_dir)

    def errors(self, view, rows, **kwargs):
        """검증 오류를 (필드, 메시지) 목록으로."""
        issues = judge_io.validate_judgments(CODEBOOK, RULES, view, rows, **kwargs)
        return [(i.field, i.message) for i in validate.errors_of(issues)]

    def assert_error(self, view, rows, field, fragment=""):
        found = [(f, m) for f, m in self.errors(view, rows) if f == field and fragment in m]
        self.assertTrue(found, f"{field} 오류가 없음: {self.errors(view, rows)}")

    def pick(self, view, rows, scope="turn", item_id=None):
        """조건에 맞는 첫 판정 행의 (위치, 복사본)."""
        for position, row in enumerate(rows):
            run = view.run_of(view.responses[row["response_id"]])
            if row["evaluation_scope"] == scope and item_id in (None, run["item_id"]):
                return position, dict(row)
        raise AssertionError("조건에 맞는 판정 행이 없음")

    def mutated(self, view, rows, scope="turn", item_id=None, **changes):
        """한 행만 바꾼 판정 목록."""
        position, row = self.pick(view, rows, scope, item_id)
        row.update(changes)
        return rows[:position] + [row] + rows[position + 1:]
