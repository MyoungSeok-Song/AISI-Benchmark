"""종료 코드 계약. 모든 진입점이 같은 값을 쓴다(README '종료 코드').

  EXIT_OK           0   정상
  EXIT_NOTHING      1   할 일이 없음(실행할 문항·판정할 자리·집계할 실행이 없음)
  EXIT_INVALID      2   입력·설정·검증 오류, 거부 — 한 줄 메시지로 끝난다.
                        사전 점검(명세·설정·입력 검증·모델 선택)에서 거부되면 모델 호출·파일 쓰기 전에 멈춘다.
                        실행 도중의 거부는 그때까지의 기록을 남긴 채 2로 끝난다: 실행기의 기록 거부(앞 실행의 04·05는 남고
                        --batch-id로 이어서), run_judge의 여러 배치 중 뒤 배치 실패(앞 배치의 06은 이미 썼다).
  EXIT_INTERRUPTED  130 사람이 중단(Ctrl-C)

도구별 뜻이 다른 1 (입력·설정 오류는 이 도구들도 2)
  tools/check_determinism.py      1 = 반복 간 응답이 다름(EXIT_DIFFERENT)
  tools/vllm_server.py start      1 = 기동 중 종료·시간 초과(EXIT_RUNTIME_FAILURE) / status 1 = 서버가 준비되지 않음
  tools/e2e_check.py              1 = 종단 시험 결과 검사 실패(EXIT_FAILED)
  tools/extract_codebook.py       1 = 시트의 선언 필드 수와 읽은 수가 어긋남(모듈을 쓰지 않음, _xlsx_common.EXIT_MISMATCH)
  tools/extract_taxonomy.py       1 = 검산 실패(모듈을 쓰지 않음)
"""
EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_INVALID = 2
EXIT_INTERRUPTED = 130
