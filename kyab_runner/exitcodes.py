"""종료 코드 계약. 모든 진입점이 같은 값을 쓴다(README '종료 코드').

  EXIT_OK           0   정상
  EXIT_NOTHING      1   할 일이 없음(실행할 문항·판정할 자리·집계할 실행이 없음)
  EXIT_INVALID      2   입력·설정·검증 오류, 거부 — 한 줄 메시지로 끝나며 모델 호출·파일 쓰기 전에 멈춘다
  EXIT_INTERRUPTED  130 사람이 중단(Ctrl-C)

도구별 뜻이 다른 1
  tools/check_determinism.py   1 = 반복 간 응답이 다름(EXIT_DIFFERS)
  tools/vllm_server.py status  1 = 서버가 준비되지 않음
"""
EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_INVALID = 2
EXIT_INTERRUPTED = 130
