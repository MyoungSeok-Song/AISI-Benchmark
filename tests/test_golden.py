"""출력 고정(골든) 검사: 리팩토링 전후로 출력이 바이트 단위로 같은지 본다 (과제 8, metrics-02 · cross-13).

두 종류가 있다.
  GoldenChainTest    모의 입력으로 전체 사슬(실행 ST1·MT3 + 실패 계획 → 판정 → apply → 집계 두 정책 → 납품 내보내기)을
                     임시 폴더에 돌리고, 실행 시각류 값을 정규화한 뒤 파일별 sha256을 tests/golden/chain_sha256.json과 대조한다.
                     실모델 응답은 넣지 않는다(모의 어댑터만).
  GoldenMetricsTest  손계산 가상 배치(test_metrics의 시나리오) × 규칙 변형마다 07·분모·notes 세 파일을
                     tests/golden/metrics/ 아래 파일과 바이트 단위로 대조한다. 다르면 처음 어긋난 줄의 unified diff를 보여 준다.

정규화(시각류 필드 — 작업 지침 0절): ISO 시각(+09:00, 정밀도는 토큰에 남김), RBATCH·RUN·RESULTS의 날짜 8자리, .bak-시각,
러너 코드 판본(runner-…+sha[.dirty]), 러너 절대 경로, 임시 폴더 경로, manifest의 runner_git·dirty 경고·파일 sha256(정규화한
내용으로 다시 계산). 원천 파일(data/)은 러너 밖이라 환경마다 다르므로 sources.json은 '원천 파일 없음' 상태로 고정한다.

골든 갱신:  KYAB_UPDATE_GOLDEN=1 .venv/bin/python -m unittest tests.test_golden   (runner/ 폴더에서; 의도한 출력 변경일 때만)
정규화 결과 보기:  KYAB_GOLDEN_DUMP=<폴더>  — 정규화한 전체 출력을 그 폴더에 써 두 판본을 diff 할 수 있다.
"""
import difflib
import hashlib
import itertools
import json
import os
import re
import shutil
import sys
from pathlib import Path
from unittest import mock

from test_judge_io import ENV, RULES, JudgedTestCase
from test_metrics import hand_computed_scenario, provider_block_scenario, rules_with
from test_runner import CODEBOOK, FAILURE_PLAN, RUNNER_DIR, RunnerTestCase

from kyab_runner import csv_io, export, ids, metrics, run_aggregate, run_judge, run_multiturn, run_single   # noqa: E402
from kyab_runner import paths                                                                              # noqa: E402

sys.path.insert(0, str(RUNNER_DIR / "tools"))
import apply_judgments                                                                                     # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CHAIN_MANIFEST = GOLDEN_DIR / "chain_sha256.json"
METRICS_DIR = GOLDEN_DIR / "metrics"
METRICS_MANIFEST = METRICS_DIR / "metrics_sha256.json"
# 파일 전체를 두는 조합(어긋나면 diff를 바로 볼 수 있다). 나머지 조합은 sha256만 둔다.
FULL_FILES = frozenset({"hand.default", "block.default", "block.exclude", "hand.inconclusive_on",
                        "hand.control_only", "hand.single_only"})
UPDATE = os.environ.get("KYAB_UPDATE_GOLDEN") == "1"
DUMP_DIR = os.environ.get("KYAB_GOLDEN_DUMP")
JSON_KW = dict(ensure_ascii=False, indent=2)


# ── 정규화 ──────────────────────────────────────────────────────────────
# (정규식, 치환) 순서대로 적용한다. 토큰에 정밀도·종류를 남겨 두어 형식이 바뀌면 골든이 어긋난다.
_RULES = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+09:00"), "<TS.ms>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00"), "<TS.s>"),
    (re.compile(r"\.bak-\d{8}T\d{6}"), ".bak-<STAMP>"),
    (re.compile(r"\b(RBATCH|RUN|RESULTS)-\d{8}-"), r"\1-<DATE>-"),
    (re.compile(r"runner-[0-9.]+\+(?:src)?[0-9a-f]{7}(?:\.dirty)?"), "<LIB>"),
)


def normalize_text(text, tmp):
    text = text.replace(str(tmp), "<TMP>").replace(str(paths.RUNNER_DIR), "<RUNNER>")
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def normalize_name(relative):
    """출력 파일의 상대 경로(폴더 이름의 날짜, .bak 시각)."""
    return normalize_text(relative, "\0")


def normalize_file(path, relative, tmp, delivery_files):
    """파일 1개의 정규화한 본문. 납품 manifest는 JSON으로 풀어 git 상태·dirty 경고를 지우고 파일 sha256을 다시 센다."""
    text = normalize_text(path.read_text(encoding="utf-8"), tmp)
    if relative.endswith("delivery/" + export.MANIFEST_FILE):
        manifest = json.loads(text)
        manifest["runner_git"] = "<GIT>"
        manifest["warnings"] = [w for w in manifest["warnings"] if "커밋되지 않은 수정" not in w]
        for name, info in manifest["files"].items():
            info["sha256"] = hashlib.sha256(delivery_files[name].encode("utf-8")).hexdigest()
        text = json.dumps(manifest, **JSON_KW)
    return text


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unified_diff(expected, actual, name):
    lines = difflib.unified_diff(expected.splitlines(), actual.splitlines(), f"golden/{name}", f"now/{name}", lineterm="", n=2)
    return "\n".join(itertools.islice(lines, 40))


# ── 전체 사슬 ────────────────────────────────────────────────────────────
class GoldenChainTest(JudgedTestCase):
    """실행 → 판정 → apply → 집계(두 정책) → 내보내기. 모든 종료 코드 0, 정규화한 파일 sha256이 골든과 같다."""

    def setUp(self):
        RunnerTestCase.setUp(self)                   # JudgedTestCase의 자동 배치 생성은 쓰지 않고 순서를 직접 정한다
        for runner in (run_single, run_multiturn):
            self.assertEqual(self.run_cli(runner)[0], 0)
            self.assertEqual(self.run_cli(runner, "--mock-plan", str(FAILURE_PLAN))[0], 0)
        batches = [str(p) for p in ids.batch_dirs(self.out)]
        self.assertEqual(len(batches), 4)
        code, output = self.capture(run_judge.main, batches)
        self.assertEqual(code, 0, output)
        code, output = self.capture(apply_judgments.main, batches)
        self.assertEqual(code, 0, output)
        code, output = self.capture(run_aggregate.main, ["--allow-mock-judge", *batches])
        self.assertEqual(code, 0, output)
        exclude_rules = self.tmp / "rules_exclude.yaml"
        exclude_rules.write_text(self._exclude_policy_rules(), encoding="utf-8")
        code, output = self.capture(run_aggregate.main, ["--allow-mock-judge", "--rules", str(exclude_rules), *batches])
        self.assertEqual(code, 0, output)
        results_dirs = ids.results_dirs(self.out)
        self.assertEqual(len(results_dirs), 2)
        no_data = self.tmp / "no_data"                # 원천 파일(data/)은 환경마다 있고 없어서 '없음'으로 고정
        no_data.mkdir()
        original = export.sources_skeleton
        with mock.patch.object(export, "sources_skeleton", lambda index: original(index, no_data)):
            code, output = self.capture(export.main, [*batches, "--out", str(self.out / "delivery"),
                                                      "--results", str(results_dirs[0]), "--allow-mock-judge"])
        self.assertEqual(code, 0, output)

    @staticmethod
    def _exclude_policy_rules():
        """e2e 스크립트의 6단계와 같은 비교용 규칙: provider_block_policy=exclude, 판본은 PATCH+1."""
        text = paths.AGGREGATION_RULES_YAML.read_text(encoding="utf-8")
        current = RULES.rule_version
        major, minor, patch = current.split(".")
        text = text.replace(f"aggregation_rule_version: {current}", f"aggregation_rule_version: {major}.{minor}.{int(patch) + 1}", 1)
        assert "  provider_block_policy: count_as_refusal" in text
        return text.replace("  provider_block_policy: count_as_refusal", "  provider_block_policy: exclude", 1)

    def normalized_outputs(self):
        """{정규화한 상대 경로: 정규화한 본문}. 출력 루트 아래 모든 파일."""
        files = {}
        delivery_files = {}
        paths_sorted = sorted(p for p in self.out.rglob("*") if p.is_file())
        delivery = self.out / "delivery"
        for path in paths_sorted:                    # 납품 파일을 먼저 정규화해 manifest의 sha256을 다시 셀 수 있게 한다
            if delivery in path.parents and path.name != export.MANIFEST_FILE:
                delivery_files[str(path.relative_to(delivery))] = normalize_text(path.read_text(encoding="utf-8"), self.tmp)
        for path in paths_sorted:
            relative = normalize_name(str(path.relative_to(self.out)))
            files[relative] = normalize_file(path, relative, self.tmp, delivery_files)
        return files

    def test_chain_outputs_match_golden(self):
        files = self.normalized_outputs()
        digests = {name: sha256_text(text) for name, text in files.items()}
        if DUMP_DIR:
            for name, text in files.items():
                target = Path(DUMP_DIR) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        if UPDATE:
            GOLDEN_DIR.mkdir(exist_ok=True)
            CHAIN_MANIFEST.write_text(json.dumps(digests, **JSON_KW) + "\n", encoding="utf-8")
            return
        expected = json.loads(CHAIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(digests), "출력 파일 목록이 골든과 다름")
        changed = [name for name in expected if expected[name] != digests[name]]
        self.assertEqual(changed, [], "정규화한 출력이 골든과 다름 (KYAB_GOLDEN_DUMP로 내용 확인, 의도한 변경이면 KYAB_UPDATE_GOLDEN=1)")

    def test_normalization_removes_volatile_values(self):
        """정규화 뒤에는 시각·날짜·코드 판본·임시 경로가 남지 않는다 — 골든이 실행 시점에 기대지 않음을 확인."""
        for name, text in self.normalized_outputs().items():
            self.assertNotRegex(name + "\n" + text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", name)
            self.assertNotIn(str(self.tmp), text, name)
            self.assertNotRegex(text, r"runner-0\.\d+\.\d+\+", name)


# ── 지표 골든 ────────────────────────────────────────────────────────────
SCENARIOS = {"hand": hand_computed_scenario, "block": provider_block_scenario}
VARIANTS = {
    "default": lambda: RULES,
    "inconclusive_on": lambda: rules_with(inconclusive_report={"warn_rate": None, "report_failure_rate_if_inconclusive_failed": True}),
    "exclude": lambda: rules_with(provider_block_policy="exclude"),
    "no_substitute": lambda: rules_with(substitute_control_target_risk=False),
    "conversation_only": lambda: rules_with(multi_turn_dimension_source="conversation_only"),
}
CALCULATED_AT = "2026-10-02T12:30:00.000+09:00"


def two_model_scenario():
    """손계산 배치에 모델 m2의 실행 1건을 더한다(test_models_are_aggregated_separately와 같음)."""
    import copy

    from test_metrics import J
    s = copy.deepcopy(hand_computed_scenario())
    s.run("S1", 1, [J("fail", (0, 0, 0, 0, 0))], model="m2")
    return s


class GoldenMetricsTest(RunnerTestCase):
    """시나리오 × 규칙 변형의 07·분모·notes를 tests/golden/metrics/<시나리오>.<변형>.* 와 바이트 단위로 대조한다."""

    def aggregate_files(self, scenario, rules):
        counter = itertools.count(1)
        rows, notes = metrics.aggregate(CODEBOOK, rules, scenario.cases(rules), lambda: f"RESULT-{next(counter):08d}", CALCULATED_AT)
        results = self.tmp / "07.csv"
        csv_io.append_rows(CODEBOOK, metrics.TABLE, results, rows)
        denominators = self.tmp / "den.csv"
        run_aggregate.write_denominators(denominators, metrics.denominator_rows(notes, rules["aggregation"]["decimal_places"]))
        return {"07.csv": results.read_bytes(), "den.csv": denominators.read_bytes(),
                "notes.json": json.dumps(notes, **JSON_KW).encode("utf-8")}

    def slice_files(self, scenario, where):
        """slice_metrics 직접 호출(값·보조 기록)을 JSON으로 — 대조 문항만·단일 문항만 슬라이스의 처리 방식을 고정."""
        values, notes = metrics.slice_metrics(RULES, "overall", [c for c in scenario.cases() if where(c)])
        return {"slice.json": json.dumps({"values": values, "notes": notes}, **JSON_KW, default=str).encode("utf-8")}

    def check(self, name, files):
        """FULL_FILES에 든 이름은 파일 전체를, 나머지는 sha256만(metrics_sha256.json) 대조한다 — 저장소 크기 때문."""
        failures = []
        digests = json.loads(METRICS_MANIFEST.read_text(encoding="utf-8")) if METRICS_MANIFEST.exists() else {}
        for suffix, actual in files.items():
            golden = METRICS_DIR / f"{name}.{suffix}"
            if DUMP_DIR:
                target = Path(DUMP_DIR) / "metrics" / golden.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(actual)
            if UPDATE:
                golden.parent.mkdir(parents=True, exist_ok=True)
                if name in FULL_FILES:
                    golden.write_bytes(actual)
                else:
                    digests[golden.name] = hashlib.sha256(actual).hexdigest()
                    METRICS_MANIFEST.write_text(json.dumps(dict(sorted(digests.items())), **JSON_KW) + "\n", encoding="utf-8")
                continue
            if name in FULL_FILES:
                self.assertTrue(golden.exists(), f"골든 파일 없음: {golden}")
                expected = golden.read_bytes()
                if expected != actual:
                    failures.append(unified_diff(expected.decode("utf-8-sig"), actual.decode("utf-8-sig"), golden.name))
            elif digests.get(golden.name) != hashlib.sha256(actual).hexdigest():
                failures.append(f"{golden.name}: sha256이 골든과 다름 (KYAB_GOLDEN_DUMP로 내용 확인)")
        self.assertEqual(failures, [], "\n\n".join(failures))

    def test_scenarios_and_variants(self):
        for scenario_name, build in SCENARIOS.items():
            for variant_name, rules_of in VARIANTS.items():
                with self.subTest(scenario=scenario_name, variant=variant_name):
                    self.check(f"{scenario_name}.{variant_name}", self.aggregate_files(build(), rules_of()))

    def test_two_models(self):
        self.check("two_models.default", self.aggregate_files(two_model_scenario(), RULES))

    def test_direct_slices(self):
        """대조 문항만 있는 슬라이스(FR 계열 빈값, MRS 분자 0 — metrics-03 전 상태)와 단일 문항만의 슬라이스."""
        scenario = hand_computed_scenario()
        self.check("hand.control_only", self.slice_files(scenario, lambda c: c.item["case_type"] == "safe_control"))
        self.check("hand.single_only", self.slice_files(scenario, lambda c: c.item["conversation_mode"] == "single"))
