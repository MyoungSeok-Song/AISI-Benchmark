"""입력 3종(01_items, 02_item_tags, 03_prompts) 검증.

실행 전에 한 번 돌린다. 오류가 하나라도 있으면 실행기는 모델을 호출하지 않는다.
검사는 네 묶음이다.
  1. 필드 단위   코드북 허용값·정규식·형식·필수 (codebook.check_row)
  2. 키·연결     PK 중복, FK, 턴 번호 연속
  3. 문항 규칙   대화 방식 ↔ 턴 수 ↔ 프로토콜, 조건부 필수, 등록 코드
  4. 태그 규칙   current 1개, 분류체계 판본별 코드 검사

조건부 필수 규칙(rule_*)은 overlay_v0.3_confirmed.yaml의 note가 가리키는 함수다.
"""
import json
from collections import Counter, defaultdict
from dataclasses import dataclass

# 분류체계 판본에 따라 허용값이 달라지는 02_item_tags 필드. 필드 단위 검사에서 빼고
# _check_risk_codes가 판본에 맞는 목록으로 검사한다.
TAXONOMY_DEPENDENT_FIELDS = ("primary_risk", "secondary_risks", "sub_risk_codes", "m_review_codes")


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


def item_key(row):
    """문항 판본 키 (item_id, item_version). 01·02·03이 이 키로 이어진다."""
    return (row["item_id"], row["item_version"])


def _label(key):
    return f"{key[0]}@{key[1]}"


def _json_list(value):
    """JSON 배열 셀을 리스트로. 빈 셀이나 잘못된 값은 빈 리스트(형식 오류는 필드 검사가 잡는다)."""
    try:
        parsed = json.loads(value) if value else []
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


class _Collector:
    """Issue를 모으는 작은 도우미."""

    def __init__(self):
        self.issues = []

    def error(self, table, key, field, message):
        self.issues.append(Issue("error", table, key, field, message))

    def warning(self, table, key, field, message):
        self.issues.append(Issue("warning", table, key, field, message))


# ── 1. 필드 단위 ────────────────────────────────────────────────────────
def _check_fields(out, codebook, table, rows, key_of, skip=()):
    for row in rows:
        for field, problem in codebook.check_row(table, row, skip=skip):
            out.error(table, key_of(row), field, problem)


# ── 2. 키·연결 ──────────────────────────────────────────────────────────
def _check_unique(out, table, rows, key_of, what):
    for key, count in Counter(key_of(r) for r in rows).items():
        if count > 1:
            out.error(table, str(key), what, f"중복 {count}행")


def _check_links(out, items, tags, prompts):
    item_keys = {item_key(r) for r in items}
    for row in prompts:
        if item_key(row) not in item_keys:
            out.error("03_prompts", row["turn_id"], "item_id", f"01_items에 없는 문항 판본 {_label(item_key(row))}")
    for row in tags:
        if item_key(row) not in item_keys:
            out.error("02_item_tags", _label(item_key(row)), "item_id", "01_items에 없는 문항 판본")


def _check_turns(out, items, prompts):
    """turn_index가 1부터 planned_round_count까지 빠짐없이 있는지."""
    turns = defaultdict(list)
    for row in prompts:
        if row["turn_index"].isdigit():
            turns[item_key(row)].append(int(row["turn_index"]))
    for item in items:
        if not item["planned_round_count"].isdigit():
            continue                                  # 형식 오류는 필드 검사가 이미 보고
        expected = list(range(1, int(item["planned_round_count"]) + 1))
        actual = sorted(turns.get(item_key(item), []))
        if actual != expected:
            out.error("03_prompts", _label(item_key(item)), "turn_index",
                      f"턴 번호 {actual} — planned_round_count 기준 {expected}이어야 함")


# ── 3. 문항 규칙 ────────────────────────────────────────────────────────
def _check_protocol(out, item, config):
    """conversation_mode ↔ planned_round_count ↔ protocol_id가 서로 맞는지."""
    protocol = config["protocols"].get(item["protocol_id"])
    if protocol is None:
        return                                        # 허용값 오류는 필드 검사가 보고
    actual = (item["conversation_mode"], item["planned_round_count"])
    expected = (protocol["conversation_mode"], str(protocol["planned_round_count"]))
    if actual != expected:
        out.error("01_items", _label(item_key(item)), "protocol_id",
                  f"{item['protocol_id']}은 (conversation_mode, planned_round_count)={expected}인데 문항은 {actual}")


def rule_original_text(out, item):
    """확정 4: original_text는 외부 원천을 번역·한국화한 문항만 필수."""
    from_source = item["source_benchmark"] != "NEW"
    key = _label(item_key(item))
    if from_source and not item["original_text"]:
        out.error("01_items", key, "original_text", "외부 원천 문항인데 원문이 없음")
    if from_source and not item["source_item_id"]:
        out.error("01_items", key, "source_item_id", "외부 원천 문항인데 원래 문항 ID가 없음")


def _check_registered(out, item, config):
    if item["rubric_id"] and item["rubric_id"] not in config["registered_rubric_ids"]:
        out.error("01_items", _label(item_key(item)), "rubric_id",
                  f"등록되지 않은 루브릭 {item['rubric_id']!r} (config/runner.yaml registered_rubric_ids)")


# ── 4. 태그 규칙 ────────────────────────────────────────────────────────
def _check_current_tag(out, items, tags):
    """문항 판본마다 tag_status=current인 행이 정확히 1개 (02 tag_status 형식 원문)."""
    current = Counter(item_key(r) for r in tags if r["tag_status"] == "current")
    for item in items:
        count = current.get(item_key(item), 0)
        if count != 1:
            out.error("02_item_tags", _label(item_key(item)), "tag_status", f"current 행이 {count}개 (1개여야 함)")


def _taxonomy_major(tag):
    """taxonomy_version의 MAJOR 숫자. 읽을 수 없으면 None."""
    head = tag["taxonomy_version"].split(".")[0]
    return int(head) if head.isdigit() else None


def _check_risk_codes(out, codebook, taxonomy, tag, key):
    """분류 코드 검사. 판본 MAJOR 0은 이전 체계(R/M), 1 이상은 A1~A10."""
    major = _taxonomy_major(tag)
    if major is None:
        return                                        # 형식 오류는 필드 검사가 보고
    if major == 0:
        _check_legacy_codes(out, taxonomy, tag, key)
        return
    # 새 체계: overlay가 넣어 둔 허용값(A 코드)으로 필드 검사
    for field in ("primary_risk", "secondary_risks", "sub_risk_codes"):
        problem = codebook.field("02_item_tags", field).check(tag[field])
        if problem:
            out.error("02_item_tags", key, field, problem)
    rule_sub_risk(out, taxonomy, tag, key)
    rule_m_review(out, tag, key, is_legacy=False)


def _check_legacy_codes(out, taxonomy, tag, key):
    """이전 체계 행(과거 이력). 코드북 v0.2 규칙대로 R1~R5 / M01~M05만 허용."""
    if tag["primary_risk"] and tag["primary_risk"] not in taxonomy.legacy_risk_codes:
        out.error("02_item_tags", key, "primary_risk", f"이전 체계 행인데 R 코드가 아님: {tag['primary_risk']!r}")
    for field, allowed in (("secondary_risks", taxonomy.legacy_risk_codes),
                           ("m_review_codes", taxonomy.legacy_m_codes)):
        bad = [c for c in _json_list(tag[field]) if c not in allowed]
        if bad:
            out.error("02_item_tags", key, field, f"이전 체계 행의 허용값 아님: {bad}")
    if _json_list(tag["sub_risk_codes"]):
        out.error("02_item_tags", key, "sub_risk_codes", "이전 체계에서는 세부 코드를 쓰지 않음(v0.2 '추후 확정')")
    rule_m_review(out, tag, key, is_legacy=True)


def rule_sub_risk(out, taxonomy, tag, key):
    """가정 P1: 주소분류는 주대분류의 자식이어야 하고, 주대분류 없이 단독으로 올 수 없다."""
    primary, subs = tag["primary_risk"], _json_list(tag["sub_risk_codes"])
    if subs and not primary:
        out.error("02_item_tags", key, "sub_risk_codes", "primary_risk가 비어 있는데 소분류가 있음")
    for sub in subs:
        if primary and sub in taxonomy.parent_of and not taxonomy.is_child(sub, primary):
            out.error("02_item_tags", key, "sub_risk_codes",
                      f"{sub}는 {primary}의 소분류가 아님 (소속: {taxonomy.parent_of[sub]})")
    if primary in _json_list(tag["secondary_risks"]):
        out.error("02_item_tags", key, "secondary_risks", f"주대분류 {primary}가 보조 위험에 다시 들어 있음")


def rule_primary_risk(out, tag, key):
    """확정 4: primary_risk는 검토 완료(mapped)된 문항만 필수. 공란이면 상태가 이유를 밝혀야 한다."""
    status, primary = tag["risk_review_status"], tag["primary_risk"]
    if status == "mapped" and not primary:
        out.error("02_item_tags", key, "primary_risk", "risk_review_status=mapped인데 비어 있음")
    if status == "mapped" and _taxonomy_major(tag) and not _json_list(tag["sub_risk_codes"]):
        out.error("02_item_tags", key, "sub_risk_codes", "risk_review_status=mapped인데 주소분류가 없음")
    if status == "not_applicable" and primary:
        out.warning("02_item_tags", key, "primary_risk", "risk_review_status=not_applicable인데 값이 있음")


def rule_m_review(out, tag, key, is_legacy):
    """가정 P2: 새 체계 행은 M 검토 칸을 비우고, 이전 체계 행은 v0.2대로 상태가 필수."""
    if is_legacy:
        if not tag["m_review_status"]:
            out.error("02_item_tags", key, "m_review_status", "이전 체계 행은 필수(v0.2)")
        return
    for field in ("m_review_codes", "m_review_status"):
        if tag[field] not in ("", "[]"):
            out.error("02_item_tags", key, field, "새 분류체계 행에서는 비워 둠 (M01~M05는 A6~A10으로 편입)")


def _check_roles(out, tag, key):
    """02 role_tags 설명: '역할 불명은 단독으로 사용'."""
    roles = _json_list(tag["role_tags"])
    if "ambiguous" in roles and len(roles) > 1:
        out.error("02_item_tags", key, "role_tags", f"ambiguous는 다른 역할과 함께 쓸 수 없음: {roles}")


# ── 진입점 ──────────────────────────────────────────────────────────────
def validate_inputs(codebook, taxonomy, config, items, tags, prompts):
    """입력 3종을 검사해 Issue 목록을 돌려준다(오류가 없으면 빈 목록 또는 경고만)."""
    out = _Collector()
    tag_key = lambda r: f"{_label(item_key(r))}#rev{r['tag_revision']}"     # noqa: E731

    _check_fields(out, codebook, "01_items", items, lambda r: _label(item_key(r)))
    _check_fields(out, codebook, "02_item_tags", tags, tag_key, skip=TAXONOMY_DEPENDENT_FIELDS)
    _check_fields(out, codebook, "03_prompts", prompts, lambda r: r["turn_id"])

    _check_unique(out, "01_items", items, item_key, "item_id+item_version")
    _check_unique(out, "02_item_tags", tags, lambda r: item_key(r) + (r["tag_revision"],),
                  "item_id+item_version+tag_revision")
    _check_unique(out, "03_prompts", prompts, lambda r: r["turn_id"], "turn_id")
    _check_unique(out, "03_prompts", prompts, lambda r: item_key(r) + (r["turn_index"],),
                  "item_id+item_version+turn_index")
    _check_links(out, items, tags, prompts)
    _check_turns(out, items, prompts)

    for item in items:
        _check_protocol(out, item, config)
        rule_original_text(out, item)
        _check_registered(out, item, config)

    _check_current_tag(out, items, tags)
    for tag in tags:
        key = tag_key(tag)
        _check_risk_codes(out, codebook, taxonomy, tag, key)
        rule_primary_risk(out, tag, key)
        _check_roles(out, tag, key)
    return out.issues


def errors_of(issues):
    return [i for i in issues if i.level == "error"]
