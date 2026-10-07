"""판정 입출력: 06_judgments의 틀·판정 입력·검증·주 판정 집합·사후 산출.

실행(04·05)과 판정(06)은 분리돼 있다. 이 모듈은 그 사이를 잇는다.

  1. 판정 틀        template_rows / write_template    성공 응답마다 turn 행, 다중턴 실행마다 conversation 행
  2. 판정 입력      build_judge_inputs / write_judge_inputs   틀의 행마다 판정기가 볼 묶음 (judge_inputs.jsonl)
                    self_identifying_responses        응답이 스스로 모델명을 말한 경우 찾기(눈가림 점검)
  3. 사람 표본      select_human_sample               자동 판정의 20%를 시드 고정으로 선정
  4. 06 검증        validate_judgments                코드북 행 검사 + 교차 규칙(rule_*)
  5. 주 판정 집합   select_primary                    집계와 사후 산출이 기준으로 삼는 판정 행
  6. 사후 산출      first_turns                       04_runs의 first_fail_turn · first_cfc_turn

판정기를 부르는 일은 여기에 없다(run_judge.py와 judges/). 코드북에 없는 결정은 모두
config/aggregation_rules.yaml에서 읽는다(rules.py).

대화 범위(conversation) 판정 행은 FK가 response_id뿐이라(구조 검토 S2) 그 실행의 마지막 성공
응답을 대표로 참조한다. 그래서 판정 행의 키는 (evaluation_scope, response_id)다.

judge_inputs.jsonl은 코드북 7 CSV에 속하지 않는 내부 중간 산출물이다. 한 줄이 판정 틀의
한 행과 1:1로 대응하며 키는 evaluation_scope + response_id다.
"""
import hashlib
import json
import math
from datetime import datetime
from fractions import Fraction

from . import csv_io, fileio
from .ids import judgment_ids_by_batch
from .issues import IssueCollector, check_fields, check_unique
from .layout import JUDGE_INPUTS_FILE, JUDGMENTS_FILE, JUDGMENTS_TEMPLATE_FILE   # noqa: F401 (재수출)
from .records import BatchView
from .vocab import (JUDGE_STATUS_ADJUDICATED, JUDGE_STATUS_COMPLETED, JUDGE_STATUS_FAILED, JUDGE_TYPE_HUMAN, MODE_MULTI,
                    RESPONSE_SUCCESS, REVIEW_COMPLETED, SCOPE_CONVERSATION, SCOPE_TURN, TAG_CURRENT, VERDICT_FAIL,
                    VERDICT_INCONCLUSIVE)

TABLE = "06_judgments"
TEMPLATE_FILE = JUDGMENTS_TEMPLATE_FILE             # 이 모듈의 옛 이름(테스트가 쓴다)
MOCK_WARNING = "모의 판정기 결과입니다. 실제 채점이 아니므로 본평가·보고에 쓸 수 없습니다."


def judgment_key(row):
    """판정 행이 가리키는 자리: (evaluation_scope, response_id)."""
    return (row["evaluation_scope"], row["response_id"])


# ── 1. 판정 틀 ──────────────────────────────────────────────────────────
def template_rows(view):
    """배치의 기록된 응답으로 판정 틀 행을 만든다.

    미리 채우는 값은 evaluation_scope, response_id, tag_revision(현재 태그), rubric_id 넷이다.
    judgment_id와 점수·판정 필드는 판정 단계의 몫이라 비워 둔다.
    """
    columns = view.batch.codebook.columns(TABLE)

    def blank_row(scope, response):
        run = view.run_of(response)
        row = dict.fromkeys(columns, "")
        row.update(evaluation_scope=scope, response_id=response["response_id"],
                   tag_revision=view.current_tag_of(run)["tag_revision"], rubric_id=view.item_of(run)["rubric_id"])
        return row

    rows, last_success = [], {}
    for response in view.responses.values():            # 파일 순서 = 기록 순서
        if response["response_status"] != RESPONSE_SUCCESS:
            continue
        rows.append(blank_row(SCOPE_TURN, response))
    for run_id in view.runs:
        successes = view.successes(run_id)
        if successes and view.item_of(view.runs[run_id])["conversation_mode"] == MODE_MULTI:
            last_success[run_id] = successes[-1]         # 대화 범위 행의 대표 응답
    rows += [blank_row(SCOPE_CONVERSATION, response) for response in last_success.values()]
    return rows


def write_template(batch, index):
    """판정 틀(06_judgments_template.csv)을 다시 만든다. 반환: 틀의 행 수.

    batch는 실행 중인 session.Batch나 끝난 배치의 records.BatchRecords, index는 입력 색인이다.
    06_judgments.csv가 아니라 _template인 까닭: 채점하지 않은 빈 틀이기 때문이다.
    배치가 갱신될 때마다(재시작 포함) 실행기가 다시 만든다.
    """
    rows = template_rows(BatchView(batch, index))
    # 미리 채운 값은 코드북 허용값·형식 검사를 거친다(필수 검사는 하지 않음: 빈 틀이므로).
    for row in rows:
        problems = batch.codebook.check_row(TABLE, row, stages=())
        if problems:
            raise csv_io.CsvFormatError(f"06_judgments 틀 값 문제: {problems}")
    csv_io.rewrite_table(batch.codebook, TABLE, batch.dir / TEMPLATE_FILE, rows)
    return len(rows)


# ── 2. 판정 입력 ────────────────────────────────────────────────────────
def _parsed(codebook, table, row, fields):
    """행에서 필드를 뽑는다. JSON 필드는 문자열이 아니라 값으로 풀어 넣는다(빈 셀은 None)."""
    out = {}
    for name in fields:
        value = row[name]
        if codebook.field(table, name).is_json:
            value = json.loads(value) if value else None
        out[name] = value
    return out


def build_judge_inputs(view, rules, rows):
    """판정 틀의 행마다 판정기가 볼 입력 묶음을 만든다. 반환: dict 목록(틀과 같은 순서).

    묶음 구성
      evaluation_scope, response_id   06 행과 잇는 키
      tag_revision                    판정에 적용하는 태그 판본(현재 태그)
      target_turn_index               turn: 채점할 턴 / conversation: 대화의 마지막 턴
      item, tags                      문항 메타와 현재 태그 (규칙 파일 judge_input이 고른 필드)
      turns                           1턴부터 target_turn_index까지의 턴별 기대 안전응답
      messages                        그 응답까지의 대화. 05 request_messages_json에 그 응답을 덧붙인 것

    scope=turn은 해당 턴까지의 대화, scope=conversation은 전체 대화다. 3턴 실행은 매 턴 앞선
    대화를 다시 보내므로, 그 응답의 요청 메시지 + 응답 본문이 곧 '그 턴까지의 대화'다.

    눈가림: 모델을 알 수 있는 값(model_id, provider, model_version, run_id, run_batch_id)을 넣지 않는다.
    기대응답·금지응답·위험 단서는 평가 대상 모델에는 보내지 않지만 판정기에는 보낸다.
    """
    codebook, spec = view.batch.codebook, rules["judge_input"]
    inputs = []
    for row in rows:
        response = view.responses[row["response_id"]]
        run = view.run_of(response)
        target = view.turn_index(response)
        turns = [{"turn_index": int(t["turn_index"]), **_parsed(codebook, "03_prompts", t, spec["turn_fields"])}
                 for t in view.index.turns[(run["item_id"], run["item_version"])] if int(t["turn_index"]) <= target]
        messages = json.loads(response["request_messages_json"])
        messages.append({"role": "assistant", "content": response["response_text"]})
        inputs.append({
            "evaluation_scope": row["evaluation_scope"],
            "response_id": row["response_id"],
            "tag_revision": row["tag_revision"],
            "target_turn_index": target,
            "item": _parsed(codebook, "01_items", view.item_of(run), spec["item_fields"]),
            "tags": _parsed(codebook, "02_item_tags", view.current_tag_of(run), spec["tag_fields"]),
            "turns": turns,
            "messages": messages,
        })
    return inputs


def write_judge_inputs(view, rules, rows):
    """판정 입력을 배치 폴더의 judge_inputs.jsonl에 쓴다(매번 전체를 다시 쓴다). 반환: 입력 목록."""
    inputs = build_judge_inputs(view, rules, rows)
    fileio.write_jsonl(view.batch.dir / JUDGE_INPUTS_FILE, inputs, atomic=True)
    return inputs


def self_identifying_responses(view, rules):
    """응답 본문에 모델 자기 지칭 패턴이 있는 성공 응답. 반환: {response_id: [걸린 패턴, ...]}.

    판정 입력에서 모델 식별 필드를 빼도, 응답이 스스로 정체를 밝히면 판정자가 모델을 알 수 있다.
    여기서는 찾아서 알리기만 한다. 응답 원문은 고치지 않는다.
    """
    patterns = rules.self_identification_patterns
    found = {}
    for response in view.responses.values():
        if response["response_status"] != RESPONSE_SUCCESS:
            continue
        text = response["response_text"].casefold()
        hits = [p for p in patterns if p.casefold() in text]
        if hits:
            found[response["response_id"]] = hits
    return found


# ── 3. 사람 재채점 표본 ─────────────────────────────────────────────────
def select_human_sample(keys, rate, seed):
    """판정 행 키 중 사람이 재채점할 표본을 고른다. 반환: 키 집합.

    ceil(행 수 × rate)개를 고른다. 무작위는 시드와 키의 해시 순위로 만든다. 입력 순서나
    실행 시점과 무관하게 같은 (시드, 키 집합)이면 같은 표본이 나온다(재시작해도 같다).
    """
    count = math.ceil(Fraction(str(rate)) * len(keys))      # 부동소수 오차 없이 올림
    ranked = sorted(keys, key=lambda k: hashlib.sha256(f"{seed}|{k[0]}|{k[1]}".encode()).hexdigest())
    return set(ranked[:count])


# ── 4. 06 검증 ──────────────────────────────────────────────────────────
def load_judgments(codebook, batch_dir):
    """배치의 06_judgments.csv를 읽는다. 없으면 빈 목록."""
    return csv_io.read_if_exists(codebook, TABLE, batch_dir / JUDGMENTS_FILE)


def _row_label(row):
    return row["judgment_id"] or f"{row['evaluation_scope']}:{row['response_id']}"


def rule_tag_revision(out, view, row, run):
    """tag_revision은 그 문항의 태그 판본이어야 한다. 현재 판본이 아니면 경고(집계에서 빠진다).

    코드북 memo: 태그가 바뀌어도 기존 판정 행은 고치지 않는다. 그래서 옛 판본으로 한 판정이
    남아 있는 것은 정상이고, 02에 없는 판본을 가리킬 때만 오류다.
    """
    revisions = view.index.tag_revisions[(run["item_id"], run["item_version"])]
    current = view.current_tag_of(run)["tag_revision"]
    if row["tag_revision"] not in revisions:
        out.error(TABLE, _row_label(row), "tag_revision",
                  f"02_item_tags에 없는 태그 판본 {row['tag_revision']!r} ({run['item_id']}@{run['item_version']})")
    elif row["tag_revision"] != current:
        out.warning(TABLE, _row_label(row), "tag_revision",
                    f"현재 태그 판본({current})이 아님 — 주 판정 집합에서 제외된다")


def rule_rubric(out, rules, view, row, run):
    """rubric_id는 01_items의 값과 같고, rubric_version은 등록된 판본이어야 한다."""
    expected = view.item_of(run)["rubric_id"]
    if row["rubric_id"] != expected:
        out.error(TABLE, _row_label(row), "rubric_id", f"문항의 rubric_id는 {expected!r}인데 {row['rubric_id']!r}")
    registered = rules.rubric_version(row["rubric_id"])
    if registered is not None and row["rubric_version"] != registered:
        out.error(TABLE, _row_label(row), "rubric_version",
                  f"{row['rubric_id']}의 등록 판본은 {registered}인데 {row['rubric_version']!r}")


def rule_cfc(out, rules, row):
    """critical_failure_code: 치명적 실패가 없으면 NONE(회신 ③), 있으면 승인 코드.

    잠정 해석(overlay OV-J1b): NONE이 아닌 코드이면 verdict=fail. NONE은 어떤 verdict와도 함께 올 수 있다.
    판정이 끝나지 못한 행(가정 J4): failed는 빈칸만, needs_review는 빈칸·NONE·코드 모두 허용(verdict 요구 없음).
    빈칸이 허용되지 않는 행은 rule_outcome_required가 잡는다.
    """
    code, status = row["critical_failure_code"], row["judge_status"]
    if not code:
        return
    if status == JUDGE_STATUS_FAILED:
        out.error(TABLE, _row_label(row), "critical_failure_code",
                  f"judge_status=failed 행은 CFC를 비워 둬야 함 (현재 {code!r}) — 판정이 실패했으면 실패 없음도 주장할 수 없다")
        return
    if code == rules.none_token:
        return
    if code not in rules["judgment"]["registered_cfc_codes"]:
        out.error(TABLE, _row_label(row), "critical_failure_code",
                  f"등록되지 않은 CFC 코드 {code!r} (치명적 실패가 없으면 {rules.none_token})")
    if not rules.is_unfinished(row) and row["verdict"] != VERDICT_FAIL:
        out.error(TABLE, _row_label(row), "verdict",
                  f"NONE이 아닌 critical_failure_code가 있으면 fail이어야 함 (현재 {row['verdict']!r})")


def rule_verdict(out, rules, row):
    """가정 J2: inconclusive 허용 여부는 규칙 파일이 정한다."""
    if row["verdict"] == VERDICT_INCONCLUSIVE and not rules["judgment"]["allow_inconclusive"]:
        out.error(TABLE, _row_label(row), "verdict", "inconclusive는 허용되지 않음 (aggregation_rules.yaml)")


def rule_judge(out, rules, row):
    """사람 판정 행은 human_review_status=completed만 (코드북 형식). LLM 판정기는 등록된 것만."""
    if row["judge_type"] == JUDGE_TYPE_HUMAN:
        if row["human_review_status"] != REVIEW_COMPLETED:
            out.error(TABLE, _row_label(row), "human_review_status",
                      f"사람 판정 행은 completed만 허용 (현재 {row['human_review_status']!r})")
        return
    entry = rules.judges.get(row["judge_id"])
    if entry is None or entry.judge_type != row["judge_type"]:
        out.error(TABLE, _row_label(row), "judge_id", f"등록되지 않은 판정기 {row['judge_id']!r} (config/judges.yaml)")


def rule_scope(out, view, row, response, run):
    """conversation 범위는 다중턴 실행에만 있고, 그 실행의 마지막 성공 응답을 참조해야 한다(S2)."""
    if response["response_status"] != RESPONSE_SUCCESS:
        out.warning(TABLE, _row_label(row), "response_id",
                    f"성공하지 않은 응답({response['response_status']})에 대한 판정")
    if row["evaluation_scope"] != SCOPE_CONVERSATION:
        return
    if view.item_of(run)["conversation_mode"] != MODE_MULTI:
        out.error(TABLE, _row_label(row), "evaluation_scope", "conversation 범위는 다중턴 실행에만 쓸 수 있음")
        return
    successes = view.successes(run["run_id"])
    if not successes or successes[-1]["response_id"] != row["response_id"]:
        out.error(TABLE, _row_label(row), "response_id",
                  "conversation 범위 행은 그 실행의 마지막 성공 응답을 참조해야 함")


def blank_allowed_fields(rules, item, scope):
    """가정 J3: 이 행에서 비워도 되는 점수 필드 집합."""
    facts = {"conversation_mode": item["conversation_mode"], "case_type": item["case_type"],
             "evaluation_scope": scope}
    allowed = set()
    for entry in rules["judgment"]["blank_allowed"]:
        if all(facts[key] == value for key, value in entry["when"].items()):
            allowed.update(entry["fields"])
    return allowed


LEGACY_BLANK_CFC = "J1 옛 형식(빈 CFC)"      # 2026-10-02 가정 J1(미발생=빈값)로 기록된 옛 판정 행을 가리키는 표지


def rule_outcome_required(out, rules, row):
    """가정 J4: 판정이 끝난 행(completed·adjudicated)에는 verdict·over_refusal·referral_present·CFC가 필수.

    CFC는 치명적 실패가 없어도 NONE을 적어야 한다(회신 ③). 빈칸은 미채점·누락과 구분되지 않는다.
    """
    if rules.is_unfinished(row):
        return
    for field in rules.outcome_fields:
        if row[field] == "":
            hint = (f" — 치명적 실패가 없으면 {rules.none_token}. 빈칸이면 {LEGACY_BLANK_CFC}일 수 있음"
                    if field == "critical_failure_code" else "")
            out.error(TABLE, _row_label(row), field, f"필수인데 비어 있음 (judge_status={row['judge_status']}){hint}")


def legacy_hint(issues):
    """빈 CFC(옛 형식) 오류가 있으면 재생성 안내 한 줄, 없으면 빈 문자열."""
    count = sum(1 for i in issues if i.level == "error" and LEGACY_BLANK_CFC in i.message)
    if not count:
        return ""
    return (f"{LEGACY_BLANK_CFC} 행 {count}개: 2026-10-05 회신 ③ 전의 모의 판정일 수 있습니다(모의 판정만 있는 배치는 README "
            "'옛 판정 기록 다시 만들기' 절차 — 04 백업 복원 → 06·judge_manifest 정리 → 다시 판정). 실제 판정기의 행이면 "
            "판정기가 CFC를 빠뜨린 것이므로 판정기 쪽을 고쳐 다시 판정하세요.")


def rule_blank_allowed(out, rules, view, row, run):
    """가정 J3: 점수 필드는 '해당 없음' 조건에 걸리는 행에서만 비울 수 있다.

    판정이 끝나지 못한 행(가정 J4)은 점수가 없을 수 있으므로 검사하지 않는다.
    """
    if rules.is_unfinished(row):
        return
    allowed = blank_allowed_fields(rules, view.item_of(run), row["evaluation_scope"])
    for field in rules.score_fields:
        if row[field] == "" and field not in allowed:
            out.error(TABLE, _row_label(row), field, "필수인데 비어 있음 (빈값 허용 조건 J3에 해당하지 않는 행)")


def foreign_judgment_ids(codebook, view):
    """같은 출력 루트의 다른 배치에서 쓰인 judgment_id (전역 고유 검사용)."""
    by_batch = judgment_ids_by_batch(codebook, view.batch.dir.parent)
    return [i for name, used in by_batch.items() if name != view.batch.dir.name for i in used]


def validate_judgments(codebook, rules, view, judgments, foreign_ids=()):
    """판정 행을 검사해 Issue 목록을 돌려준다.

    foreign_ids  같은 출력 루트의 다른 배치에서 이미 쓰인 judgment_id (전역 고유 검사용).
    검사: 코드북 행 검사(허용값·정규식·형식·필수) → ID 고유 → 응답 연결 → 교차 규칙.
    """
    out = IssueCollector()
    check_fields(out, codebook, TABLE, judgments, _row_label)
    check_unique(out, TABLE, judgments, lambda r: r["judgment_id"], "judgment_id")

    foreign = set(foreign_ids)
    for row in judgments:
        if row["judgment_id"] in foreign:
            out.error(TABLE, _row_label(row), "judgment_id", "다른 배치에서 이미 쓰인 ID (전역 고유여야 함)")
        response = view.responses.get(row["response_id"])
        if response is None:
            out.error(TABLE, _row_label(row), "response_id", "이 배치의 05_responses에 없는 응답")
            continue
        run = view.run_of(response)
        rule_tag_revision(out, view, row, run)
        rule_rubric(out, rules, view, row, run)
        rule_cfc(out, rules, row)
        rule_verdict(out, rules, row)
        rule_judge(out, rules, row)
        rule_scope(out, view, row, response, run)
        rule_outcome_required(out, rules, row)
        rule_blank_allowed(out, rules, view, row, run)

    mock_rows = sum(1 for r in judgments if r["judge_id"] in rules.non_production_judges())
    if mock_rows:
        out.warning(TABLE, view.batch.run_batch_id, "judge_id",
                    f"본평가에 쓸 수 없는 판정기(모의)의 행 {mock_rows}개 — 집계는 --allow-mock-judge가 있어야 한다")
    return out.issues


# ── 5. 주 판정 집합 ─────────────────────────────────────────────────────
def latest_rows(view, judgments, accept, current_tag_only=True):
    """판정 자리 (evaluation_scope, response_id)마다 accept(row)가 참인 행 중 가장 늦은 행.

    '가장 늦은'은 evaluated_at 기준이고, 같으면 judgment_id가 큰 행이다.
    current_tag_only이면 현재 태그 판본으로 판정한 행만 후보다.
    반환: {(scope, response_id): 판정 행}
    """
    best = {}
    for row in judgments:
        response = view.responses.get(row["response_id"])
        if response is None or not accept(row):
            continue
        if current_tag_only and row["tag_revision"] != view.current_tag_of(view.run_of(response))["tag_revision"]:
            continue
        order = (datetime.fromisoformat(row["evaluated_at"]), row["judgment_id"])
        key = judgment_key(row)
        if key not in best or order > best[key][0]:
            best[key] = (order, row)
    return {key: row for key, (_, row) in best.items()}


def select_auto(rules, view, judgments):
    """자동 판정 행: 규칙 파일이 정한 판정자 종류·상태(llm + completed)인 행 중 자리마다 가장 늦은 것."""
    spec = rules["aggregation"]["primary_judgment_set"]
    current_only = spec["tag_revision"] == TAG_CURRENT
    return latest_rows(view, judgments, current_tag_only=current_only,
                       accept=lambda r: r["judge_type"] == spec["judge_type"] and r["judge_status"] == spec["judge_status"])


def select_primary(rules, view, judgments):
    """판정 자리마다 집계가 기준으로 삼을 행 1개를 고른다.

    규칙(aggregation_rules.yaml primary_judgment_set)
      * 후보는 현재 태그 판본으로 판정한 행뿐이다.
      * judge_status=adjudicated 행이 있으면 그 행이 우선한다(판정자 종류와 무관, 시각과 무관).
      * 없으면 자동 판정 행(select_auto).
      * 같은 순위가 여럿이면 evaluated_at이 가장 늦은 행.
    반환: {(scope, response_id): 판정 행}
    """
    spec = rules["aggregation"]["primary_judgment_set"]
    primary = select_auto(rules, view, judgments)
    if spec["adjudicated_first"]:
        primary.update(latest_rows(view, judgments, lambda r: r["judge_status"] == JUDGE_STATUS_ADJUDICATED,
                                   current_tag_only=spec["tag_revision"] == TAG_CURRENT))
    return primary


def select_human(rules, view, judgments, independent_only):
    """사람 판정 행. independent_only이면 독립 재채점(judge_status=completed)만, 아니면 조정(adjudicated) 행도 포함."""
    current_only = rules["aggregation"]["primary_judgment_set"]["tag_revision"] == TAG_CURRENT
    return latest_rows(view, judgments, current_tag_only=current_only,
                       accept=lambda r: r["judge_type"] == JUDGE_TYPE_HUMAN
                       and (r["judge_status"] == JUDGE_STATUS_COMPLETED or not independent_only))


# ── 6. 사후 산출 (구조 검토 S1) ─────────────────────────────────────────
def first_turns(rules, view, primary):
    """실행마다 first_fail_turn·first_cfc_turn을 주 판정 집합의 turn 행으로 계산한다.

      first_fail_turn  verdict=fail인 가장 이른 턴. 없으면 빈값
      first_cfc_turn   치명적 실패(NONE이 아닌 critical_failure_code)가 있는 가장 이른 턴. 없으면 빈값
    성공 응답이 없는 실행은 판정할 것이 없어 둘 다 빈값이다. 차단·오류로 끝난 턴은 판정 행이
    없으므로 실패로 세지 않는다.

    성공 응답 중 하나라도 주 판정이 없으면 '가장 이른 턴'을 확정할 수 없으므로 그 실행은
    계산하지 않고 incomplete에 넣는다.
    반환: ({run_id: (first_fail_turn, first_cfc_turn)}, [판정이 덜 된 run_id])
    """
    values, incomplete = {}, []
    for run_id in view.runs:
        judged = [(view.turn_index(r), primary.get((SCOPE_TURN, r["response_id"]))) for r in view.successes(run_id)]
        if any(row is None for _, row in judged):
            incomplete.append(run_id)
            continue
        fail = min((turn for turn, row in judged if row["verdict"] == VERDICT_FAIL), default="")
        cfc = min((turn for turn, row in judged if rules.has_critical_failure(row)), default="")
        values[run_id] = (str(fail), str(cfc))
    return values, incomplete
