"""06_judgments 빈 틀.

채점은 하지 않는다. 판정 단계가 채울 행의 자리를 코드북 열 순서대로 만들어,
실행 결과가 판정 입력으로 이어지는지 확인하는 용도다.

  * 성공한 응답마다 evaluation_scope=turn 행 1개
  * 다중턴 실행마다 evaluation_scope=conversation 행 1개
    (FK가 response_id뿐이라 마지막 성공 응답을 대표로 참조한다 — 구조 검토 S2의 임시 규칙)

미리 채우는 값은 evaluation_scope, response_id, tag_revision(현재 태그), rubric_id 넷이다.
judgment_id와 점수·판정 필드는 판정 단계의 몫이라 비워 둔다. 그래서 이 파일은
06_judgments.csv가 아니라 06_judgments_template.csv이며, 배치가 갱신될 때마다 다시 만든다.
"""
from . import csv_io
from .validate import item_key

TEMPLATE_FILE = "06_judgments_template.csv"


def write_template(batch, items, tags):
    """배치의 기록된 응답으로 판정 틀을 다시 만든다. 반환: 틀의 행 수."""
    rubric_of = {item_key(i): i["rubric_id"] for i in items}
    current_revision = {item_key(t): t["tag_revision"] for t in tags if t["tag_status"] == "current"}
    runs = {r["run_id"]: r for r in batch.recorded_runs()}
    columns = batch.codebook.columns("06_judgments")

    def blank_row(scope, response, run):
        row = dict.fromkeys(columns, "")
        row.update(evaluation_scope=scope, response_id=response["response_id"],
                   tag_revision=current_revision[item_key(run)], rubric_id=rubric_of[item_key(run)])
        return row

    rows, last_success = [], {}
    for response in batch.recorded_responses():
        if response["response_status"] != "success":
            continue
        run = runs[response["run_id"]]
        rows.append(blank_row("turn", response, run))
        last_success[run["run_id"]] = response           # 파일 순서 = 턴 순서이므로 마지막 것이 남는다
    if batch.config.protocol(batch.protocol_id)["conversation_mode"] == "multi":
        rows += [blank_row("conversation", response, runs[run_id]) for run_id, response in last_success.items()]

    # 미리 채운 값은 코드북 허용값·형식 검사를 거친다(필수 검사는 하지 않음: 빈 틀이므로).
    for row in rows:
        problems = batch.codebook.check_row("06_judgments", row, stages=())
        if problems:
            raise csv_io.CsvFormatError(f"06_judgments 틀 값 문제: {problems}")
    csv_io.rewrite_table(batch.codebook, "06_judgments", batch.dir / TEMPLATE_FILE, rows)
    return len(rows)
