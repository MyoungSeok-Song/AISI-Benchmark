"""실행 기록 절차. 단일턴·3턴 실행기가 함께 쓴다.

  Batch       실행 명령 1회의 공통 값과 출력 파일 (04_runs.csv, 05_responses.csv, 보조 로그)
  RunSession  실행 1건(문항 × 모델 × 반복 번호). 턴 호출·재시도·응답 행 작성·마감

대화를 어떻게 진행할지(한 번 묻고 끝낼지, 이력을 쌓아 가며 세 번 물을지)는 여기에 없다.
그 부분은 run_single.py와 run_multiturn.py가 각자 가진다.

기록 원칙
  * 이미 쓴 행은 고치지 않는다(append-only). 실행 행은 실행이 끝났을 때 한 번만 쓴다.
    그래서 04_runs.csv에는 queued·running 상태의 행이 남지 않는다.
  * 실행 1건의 응답 행들은 모아 두었다가 실행 행과 함께 쓴다(응답 → 실행 순서).
    도중에 프로세스가 죽으면 그 실행은 기록에 없으므로 재시작 때 처음부터 다시 한다.
  * 코드북에 칸이 없는 값(턴별 지연, 재시도 내역)은 runner_events.jsonl에만 남긴다.
"""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import csv_io
from .adapters.base import CallInfo

# 러너가 채우는 필드의 '생성 단계'. 이 단계의 필수 필드가 비면 기록을 거부한다.
RUNNER_STAGES = ("실행 자동기록 필수",)

RUNS_FILE = "04_runs.csv"
RESPONSES_FILE = "05_responses.csv"
EVENTS_FILE = "runner_events.jsonl"

# 응답 상태 -> 실행 중단 사유 (04 stop_reason). 차단만 provider_block, 나머지 실패는 error.
_STOP_REASON = {"blocked": "provider_block", "error": "error", "timeout": "error", "empty": "error"}


class Batch:
    """실행 명령 1회(= run_batch_id 1개)의 공통 값과 출력 위치."""

    def __init__(self, *, codebook, config, model, adapter, ids, batch_dir, run_batch_id,
                 dataset_version, protocol_id, library_version):
        self.codebook = codebook
        self.config = config
        self.model = model                  # config.ModelEntry
        self.adapter = adapter
        self.ids = ids
        self.dir = batch_dir
        self.run_batch_id = run_batch_id
        self.dataset_version = dataset_version
        self.protocol_id = protocol_id
        self.library_version = library_version
        self._tz = ZoneInfo(config["timezone"])

    # ── 시각·보조 로그 ──────────────────────────────────────────────────
    def now(self):
        """ISO 8601 타임스탬프 (시간대 포함, 밀리초)."""
        return datetime.now(self._tz).isoformat(timespec="milliseconds")

    def log_event(self, event, **fields):
        """7 CSV 밖의 보조 로그에 한 줄 추가한다."""
        with open(self.dir / EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": self.now(), "event": event, **fields}, ensure_ascii=False) + "\n")

    # ── 기록된 실행 조회·정리 (재시작용) ────────────────────────────────
    def recorded_runs(self):
        return csv_io.read_if_exists(self.codebook, "04_runs", self.dir / RUNS_FILE)

    def recorded_responses(self):
        return csv_io.read_if_exists(self.codebook, "05_responses", self.dir / RESPONSES_FILE)

    def discard_orphan_responses(self):
        """실행 행 없이 남은 응답 행을 걷어낸다.

        응답 행을 쓴 직후 실행 행을 쓰기 전에 프로세스가 죽은 경우에만 생긴다.
        그 실행은 마감되지 않았으므로 기록으로 치지 않고 다시 실행한다.
        반환: 걷어낸 행 수.
        """
        run_ids = {r["run_id"] for r in self.recorded_runs()}
        responses = self.recorded_responses()
        kept = [r for r in responses if r["run_id"] in run_ids]
        if len(kept) != len(responses):
            csv_io.rewrite_table(self.codebook, "05_responses", self.dir / RESPONSES_FILE, kept)
            self.log_event("orphan_responses_discarded", count=len(responses) - len(kept))
        return len(responses) - len(kept)

    def call_params(self):
        return dict(self.config["run_params"])


class RunSession:
    """실행 1건. 실행기가 turn()을 부르고 마지막에 finish()를 부른다."""

    def __init__(self, batch, item, rollout_no):
        self.batch = batch
        self.item = item
        self.rollout_no = rollout_no
        self.run_id = batch.ids.new_run_id()
        self.started_at = batch.now()
        self._responses = []                # 이 실행의 응답 행 (마감 때 한꺼번에 기록)
        self._last_status = None            # 마지막 턴의 response_status
        batch.log_event("run_started", run_id=self.run_id, item_id=item["item_id"], rollout_no=rollout_no)

    def turn(self, turn, messages):
        """한 턴을 호출하고 응답 행을 만든다. 반환: AdapterResult (마지막 시도의 결과).

        일시 오류는 설정한 횟수까지 같은 요청으로 다시 시도한다. 응답 행은 턴당 1개이며
        마지막 시도의 결과를 담는다. 앞선 실패 시도는 보조 로그에만 남는다.
        """
        batch = self.batch
        max_attempts = batch.config["max_attempts_per_turn"]
        for attempt in range(1, max_attempts + 1):
            info = CallInfo(self.item["item_id"], int(turn["turn_index"]), self.rollout_no, attempt)
            began = time.monotonic()
            result = batch.adapter.complete(messages, batch.call_params(), info)
            batch.log_event("turn_attempt", run_id=self.run_id, turn_id=turn["turn_id"],
                            turn_index=info.turn_index, attempt=attempt,
                            response_status=result.response_status, error_code=result.error_code,
                            latency_ms=int((time.monotonic() - began) * 1000))
            if result.response_status == "success" or not result.retryable:
                break

        self._responses.append({
            "response_id": batch.ids.new_response_id(),
            "run_id": self.run_id,
            "turn_id": turn["turn_id"],
            "request_messages_json": messages,
            "response_text": result.response_text,
            "raw_response_json": result.raw_response,
            "response_status": result.response_status,
            "block_source": result.block_source,
            "finish_reason": result.finish_reason,
            "error_code": result.error_code,
            "error_message": result.error_message,
        })
        self._last_status = result.response_status
        return result

    def finish(self, planned_turns, interrupted=False):
        """실행을 마감하고 응답 행과 실행 행을 기록한다. 반환: 실행 행 dict.

        상태 규칙 (C4 기본값)
          계획한 턴을 모두 성공        completed / planned_end
          사람이 중단(Ctrl-C)          partial 또는 failed / manual_stop
          턴이 차단됨                  partial 또는 failed / provider_block
          턴이 오류·시간초과·빈 응답   partial 또는 failed / error
        성공한 턴이 하나도 없으면 failed, 하나라도 있으면 partial이다.
        """
        batch = self.batch
        done = sum(1 for r in self._responses if r["response_status"] == "success")
        if done == planned_turns:
            run_status, stop_reason = "completed", "planned_end"
        else:
            run_status = "partial" if done else "failed"
            stop_reason = "manual_stop" if interrupted else _STOP_REASON.get(self._last_status, "error")

        run_row = {
            "run_id": self.run_id,
            "run_batch_id": batch.run_batch_id,
            "dataset_version": batch.dataset_version,
            "item_id": self.item["item_id"],
            "item_version": self.item["item_version"],
            "rollout_no": self.rollout_no,
            "protocol_id": batch.protocol_id,
            "model_id": batch.model.model_id,
            "provider": batch.model.provider,
            "model_version": batch.model.model_version,
            "model_snapshot_date": batch.model.model_snapshot_date,
            "system_prompt_text": batch.config.system_prompt_text,
            "system_prompt_hash": batch.config.system_prompt_hash,
            # 고정 파라미터는 설정값을 그대로 적는다(0.0, 1.0, 1024).
            **batch.call_params(),
            "safety_profile": batch.config["safety_profile"],
            "tool_profile": batch.config["tool_profile"],
            "api_version": batch.model.api_version,
            "execution_library_version": batch.library_version,
            "started_at": self.started_at,
            "completed_at": batch.now(),
            "actual_turn_count": done,
            # first_fail_turn·first_cfc_turn은 판정에서 나오는 값이라 실행 시에는 비워 둔다(C3).
            "first_fail_turn": "",
            "first_cfc_turn": "",
            "run_status": run_status,
            "stop_reason": stop_reason,
        }
        self._check_open_vocabularies(run_row)
        # 응답을 먼저, 실행 행을 나중에 쓴다. 실행 행이 있으면 그 실행의 응답은 모두 있다.
        if self._responses:
            csv_io.append_rows(batch.codebook, "05_responses", batch.dir / RESPONSES_FILE,
                               self._responses, stages=RUNNER_STAGES)
        csv_io.append_rows(batch.codebook, "04_runs", batch.dir / RUNS_FILE, [run_row], stages=RUNNER_STAGES)
        batch.log_event("run_finished", run_id=self.run_id, run_status=run_status,
                        stop_reason=stop_reason, actual_turn_count=done)
        return run_row

    def _check_open_vocabularies(self, run_row):
        """코드북이 목록을 열어 둔 필드를 설정 파일의 등록 목록으로 검사한다.

        코드북 원문이 "등록 설정명", "공급자 값을 정규화한 통제어휘"처럼 목록을 적지 않은
        필드라 codebook.check_row로는 걸러지지 않는다. 기록 직전에 여기서 막는다.
        """
        config = self.batch.config
        checks = [("safety_profile", run_row["safety_profile"], config["registered_safety_profiles"]),
                  ("tool_profile", run_row["tool_profile"], config["registered_tool_profiles"])]
        for row in self._responses:
            if row["finish_reason"]:
                checks.append(("finish_reason", row["finish_reason"], config["finish_reasons"]))
            # 성공 응답에는 본문이 있어야 한다 (overlay OV-P3: 실패 응답만 본문 공란 허용)
            if row["response_status"] == "success" and not row["response_text"]:
                raise csv_io.CsvFormatError(f"{row['response_id']}: success인데 response_text가 비어 있음")
        for field, value, allowed in checks:
            if value not in allowed:
                raise csv_io.CsvFormatError(f"{field}={value!r}는 등록된 값이 아님 (config/runner.yaml: {allowed})")
