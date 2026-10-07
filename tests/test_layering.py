"""패키지 계층 검사(R15): kyab_runner/__init__.py가 선언한 '위층만 아래층을 import한다'를 AST로 확인한다.

같은 층 안의 순서는 보지 않는다(층 사이만). 허용 예외는 EXCEPTIONS에 이유와 함께 적는다.
사설 이름(_로 시작)을 모듈 밖에서 가져다 쓰는 것도 막는다 — 다른 모듈이 쓰는 순간 공개 API다.
"""
import ast
import re
import unittest

from support import RUNNER_DIR

PACKAGE = RUNNER_DIR / "kyab_runner"
# __init__.py 설명의 층 순서. 모듈은 자기 층이나 아래층만 import한다.
LAYERS = (
    ("공용", {"paths", "layout", "clock", "fileio", "exitcodes", "errors", "issues", "provenance", "vocab", "spec"}),
    ("명세·설정·입력", {"codebook", "taxonomy", "config", "rules", "csv_io", "validate", "ids", "records", "context"}),
    ("실행", {"messages", "adapters", "session", "cli", "run_single", "run_multiturn"}),
    ("판정", {"judge_io", "judges", "run_judge"}),
    ("집계", {"metrics", "run_aggregate"}),
    ("납품", {"export", "sources", "apply_judgments"}),
)
# (모듈, 가져오는 모듈): 이유. __init__.py 설명에도 적혀 있어야 한다.
EXCEPTIONS = {("cli", "judge_io"): "실행 끝에 06 판정 틀(write_template)을 쓴다 — 틀은 판정 단계의 입력 파일이라 judge_io가 소유"}


def layer_of(module):
    for level, (_, members) in enumerate(LAYERS):
        if module in members:
            return level
    raise AssertionError(f"__init__ 층 지도에 없는 모듈: {module} (LAYERS에 넣을 것)")


def package_imports(path):
    """모듈 파일 1개가 패키지 안에서 가져오는 (최상위 모듈 이름, 가져온 이름들) 목록.

    하위 패키지(adapters/·judges/) 안의 `from .base import …`는 그 하위 패키지 자신을 가리키므로 하위 패키지 이름으로 센다.
    `from ..vocab import …`처럼 한 단계 올라가면 최상위 모듈이다.
    """
    depth = len(path.relative_to(PACKAGE).parts) - 1   # 0 = 최상위 모듈, 1 = 하위 패키지 안
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.level):
            continue
        names = [a.name for a in node.names]
        if node.level <= depth:                       # 하위 패키지 안쪽 참조
            found.append((path.relative_to(PACKAGE).parts[0], names))
        elif node.module:
            found.append((node.module.split(".")[0], names))
        else:                                         # from . import a, b
            found.extend((a.name, []) for a in node.names)
    return found


class LayeringTest(unittest.TestCase):
    def modules(self):
        """최상위 모듈과 하위 패키지(adapters·judges·spec)를 한 이름으로 묶는다."""
        for path in sorted(PACKAGE.rglob("*.py")):
            relative = path.relative_to(PACKAGE)
            name = relative.parts[0].replace(".py", "")
            if name == "__init__":
                continue
            yield name, path

    def test_modules_import_only_same_or_lower_layers(self):
        violations = []
        for name, path in self.modules():
            for target, _ in package_imports(path):
                if target == "__version__" or target == name or (name, target) in EXCEPTIONS:
                    continue
                if layer_of(target) > layer_of(name):
                    violations.append(f"{path.relative_to(RUNNER_DIR)} → {target} ({LAYERS[layer_of(name)][0]} → {LAYERS[layer_of(target)][0]})")
        self.assertEqual(violations, [])

    def test_exceptions_are_documented_and_still_needed(self):
        doc = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        for (module, target), _ in EXCEPTIONS.items():
            self.assertIn(target, doc, f"예외 {module}→{target}가 __init__ 설명에 없음")
            uses = [t for t, _ in package_imports(PACKAGE / f"{module}.py") if t == target]
            self.assertTrue(uses, f"예외 {module}→{target}가 더는 쓰이지 않음 — EXCEPTIONS에서 지울 것")

    def test_no_private_names_across_modules(self):
        """다른 모듈의 _이름을 import하거나 모듈.이름으로 부르지 않는다."""
        offenders = []
        module_names = {name for name, _ in self.modules()}
        for name, path in self.modules():
            for target, names in package_imports(path):
                offenders.extend(f"{path.name}: from .{target} import {n}" for n in names if n.startswith("_"))
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in re.finditer(r"\b([a-z_]+)\._[a-z]\w*", line.split("#")[0]):
                    if match.group(1) in module_names and match.group(1) != name:
                        offenders.append(f"{path.name}:{line_no}: {match.group(0)}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
