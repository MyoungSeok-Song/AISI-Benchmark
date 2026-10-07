"""검증 결과(Issue)를 모으는 공용 기반. 입력 3종(validate)·판정(judge_io)·집계 결과(metrics) 검증이 함께 쓴다.

표준 라이브러리만 쓰고 패키지 안의 다른 모듈을 import하지 않는다(순환 없음).
"""
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    """검증에서 찾은 문제 1건."""
    level: str      # 'error'(실행 불가) | 'warning'(실행은 가능, 확인 필요)
    table: str
    key: str        # 어느 행인지: 'KYAB-900001@1.0.0', 'TURN-00000001' 등
    field: str
    message: str

    def __str__(self):
        return f"[{self.level}] {self.table} {self.key} {self.field}: {self.message}"


class IssueCollector:
    """Issue를 모으는 작은 도우미."""

    def __init__(self):
        self.issues = []

    def error(self, table, key, field, message):
        self.issues.append(Issue("error", table, key, field, message))

    def warning(self, table, key, field, message):
        self.issues.append(Issue("warning", table, key, field, message))


def errors_of(issues):
    return [i for i in issues if i.level == "error"]


def check_fields(out, codebook, table, rows, key_of, skip=()):
    """행마다 코드북 필드 검사(허용값·정규식·형식·필수)를 돌려 오류로 모은다."""
    for row in rows:
        for field, problem in codebook.check_row(table, row, skip=skip):
            out.error(table, key_of(row), field, problem)


def check_unique(out, table, rows, key_of, what):
    for key, count in Counter(key_of(r) for r in rows).items():
        if count > 1:
            out.error(table, str(key), what, f"중복 {count}행")


def report_issues(issues, title="입력 검증"):
    """Issue를 모두 출력하고 오류·경고 건수를 적는다. 반환: 오류 목록."""
    for issue in issues:
        print(issue)
    errors = errors_of(issues)
    print(f"{title}: 오류 {len(errors)}건, 경고 {len(issues) - len(errors)}건")
    return errors
