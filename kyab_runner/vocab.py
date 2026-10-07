"""코드북 허용값 가운데 코드가 비교·기록에 쓰는 값(통제어휘). 값은 코드북 enum과 같고, 로드 때 실제로 있는지 확인한다.

코드가 `row["verdict"] == "fail"`처럼 리터럴을 쓰면 코드북 v0.3에서 값이 바뀌어도 조용히 안 맞게 된다.
이름 있는 상수로 쓰고 check_vocabulary가 시작할 때 코드북 enum에 전부 있는지 검사해 바로 멈추게 한다.
finish_reason은 코드북에 목록이 없고 설정(runner.yaml finish_reasons)이 정하므로 여기 두지 않는다.
"""
from .errors import SetupError


class VocabularyError(SetupError):
    """코드가 쓰는 값이 코드북 허용값에 없을 때(코드북 판이 바뀐 뒤 코드가 따라오지 못한 상태)."""


# 01_items
MODE_SINGLE, MODE_MULTI = "single", "multi"                                   # conversation_mode
CASE_RISK, CASE_SAFE_CONTROL = "risk_case", "safe_control"                    # case_type
SOURCE_NEW, SOURCE_CAREBENCH, SOURCE_MINORBENCH = "NEW", "CAREBench", "MinorBench"   # source_benchmark (NEW = 원천 없는 신규 작성)
# 02_item_tags
TAG_CURRENT = "current"                                                       # tag_status (규칙 키워드 tag_revision과 별개: rules.TAG_REVISION_CURRENT)
RISK_REVIEW_MAPPED, RISK_REVIEW_NOT_APPLICABLE = "mapped", "not_applicable"   # risk_review_status
ROLE_AMBIGUOUS = "ambiguous"                                                  # role_tags 원소('역할 불명은 단독으로 사용')
# 04_runs
RUN_COMPLETED, RUN_PARTIAL, RUN_FAILED = "completed", "partial", "failed"     # run_status
STOP_PLANNED_END, STOP_PROVIDER_BLOCK, STOP_ERROR, STOP_MANUAL = "planned_end", "provider_block", "error", "manual_stop"
# 05_responses
RESPONSE_SUCCESS, RESPONSE_BLOCKED, RESPONSE_ERROR, RESPONSE_TIMEOUT, RESPONSE_EMPTY = (
    "success", "blocked", "error", "timeout", "empty")                        # response_status
BLOCK_SOURCE_PROVIDER = "provider"                                            # block_source
# 06_judgments
SCOPE_TURN, SCOPE_CONVERSATION = "turn", "conversation"                       # evaluation_scope
VERDICT_PASS, VERDICT_FAIL, VERDICT_INCONCLUSIVE = "pass", "fail", "inconclusive"
JUDGE_TYPE_LLM, JUDGE_TYPE_HUMAN = "llm", "human"
JUDGE_STATUS_COMPLETED, JUDGE_STATUS_FAILED, JUDGE_STATUS_NEEDS_REVIEW, JUDGE_STATUS_ADJUDICATED = (
    "completed", "failed", "needs_review", "adjudicated")
REVIEW_NOT_SELECTED, REVIEW_SELECTED_PENDING, REVIEW_COMPLETED = "not_selected", "selected_pending", "completed"

# (표, 필드, 값) 등록부 — check_vocabulary가 이 목록을 코드북 enum과 대조한다.
USED = (
    ("01_items", "conversation_mode", MODE_SINGLE), ("01_items", "conversation_mode", MODE_MULTI),
    ("01_items", "case_type", CASE_RISK), ("01_items", "case_type", CASE_SAFE_CONTROL),
    ("01_items", "source_benchmark", SOURCE_NEW), ("01_items", "source_benchmark", SOURCE_CAREBENCH),
    ("01_items", "source_benchmark", SOURCE_MINORBENCH),
    ("02_item_tags", "tag_status", TAG_CURRENT),
    ("02_item_tags", "risk_review_status", RISK_REVIEW_MAPPED), ("02_item_tags", "risk_review_status", RISK_REVIEW_NOT_APPLICABLE),
    ("02_item_tags", "role_tags", ROLE_AMBIGUOUS),
    ("04_runs", "run_status", RUN_COMPLETED), ("04_runs", "run_status", RUN_PARTIAL), ("04_runs", "run_status", RUN_FAILED),
    ("04_runs", "stop_reason", STOP_PLANNED_END), ("04_runs", "stop_reason", STOP_PROVIDER_BLOCK),
    ("04_runs", "stop_reason", STOP_ERROR), ("04_runs", "stop_reason", STOP_MANUAL),
    ("05_responses", "response_status", RESPONSE_SUCCESS), ("05_responses", "response_status", RESPONSE_BLOCKED),
    ("05_responses", "response_status", RESPONSE_ERROR), ("05_responses", "response_status", RESPONSE_TIMEOUT),
    ("05_responses", "response_status", RESPONSE_EMPTY),
    ("05_responses", "block_source", BLOCK_SOURCE_PROVIDER),
    ("06_judgments", "evaluation_scope", SCOPE_TURN), ("06_judgments", "evaluation_scope", SCOPE_CONVERSATION),
    ("06_judgments", "verdict", VERDICT_PASS), ("06_judgments", "verdict", VERDICT_FAIL), ("06_judgments", "verdict", VERDICT_INCONCLUSIVE),
    ("06_judgments", "judge_type", JUDGE_TYPE_LLM), ("06_judgments", "judge_type", JUDGE_TYPE_HUMAN),
    ("06_judgments", "judge_status", JUDGE_STATUS_COMPLETED), ("06_judgments", "judge_status", JUDGE_STATUS_FAILED),
    ("06_judgments", "judge_status", JUDGE_STATUS_NEEDS_REVIEW), ("06_judgments", "judge_status", JUDGE_STATUS_ADJUDICATED),
    ("06_judgments", "human_review_status", REVIEW_NOT_SELECTED), ("06_judgments", "human_review_status", REVIEW_SELECTED_PENDING),
    ("06_judgments", "human_review_status", REVIEW_COMPLETED),
)


def check_vocabulary(codebook):
    """USED의 (표, 필드, 값)이 모두 코드북 enum에 있는지. 없으면 VocabularyError에 전부 모아 낸다."""
    missing = [(table, field, value) for table, field, value in USED
               if value not in codebook.field(table, field).enum]
    if missing:
        raise VocabularyError("코드가 쓰는 값이 코드북 허용값에 없습니다(코드북 판 변경?): "
                              + ", ".join(f"{t}.{f}={v!r}" for t, f, v in missing))
