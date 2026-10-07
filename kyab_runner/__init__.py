"""KYAB 러너: 코드북 7 CSV 형식으로 실행(04·05) → 판정(06) → 집계(07) 기록을 만든다.

모듈 구성 (아래층 → 위층. 위층만 아래층을 import한다)
  공용  paths       폴더 위치             layout     파일 이름           clock     시각 표기
        fileio      JSON·JSONL 쓰기·해시  exitcodes  종료 코드 계약      errors    설정 오류 기반(SetupError)
        issues      검증 결과(Issue) 기반  provenance 실행 코드 출처(git·판본)
  명세  spec        자동 생성 모듈(코드북·분류체계 추출본)
  codebook    코드북 추출본 + 확정 변경(overlay) 로드, 값·행 검사
  taxonomy    A1~A10 분류체계와 이전 코드 대응표
  config      runner.yaml · models.yaml 로드
  rules       판정·집계 규칙(aggregation_rules.yaml)과 판정기 등록부(judges.yaml) 로드
  csv_io      코드북 열 순서로 CSV 읽기·쓰기
  validate    입력 3종(01·02·03) 검증
  ids         RBATCH · RUN · RESP · JDG · RESULT 발급
  records     끝난 배치와 입력 3종 읽기 (판정·집계 공용)
  context     판정·집계 도구의 공통 준비 절차

  실행  messages    모델 요청 메시지 구성 (AI 전달 ○/△ 필드만)
        adapters    모델 호출부 (모의, 로컬 vLLM, 상용 3종)
        session     단일턴·3턴 실행기가 함께 쓰는 실행 1건의 기록 절차
        cli         두 실행기의 명령행 처리와 배치 진행
        run_single / run_multiturn   실행기 (명령행 진입점)
  판정  judge_io    판정 틀, 판정 입력, 06 검증, 주 판정 집합, first_fail/cfc_turn 산출
        judges      판정기 (지금은 모의 판정기만)
        run_judge   판정 실행기 (명령행 진입점)
  집계  metrics     지표 산식, 실행 단위 정리, 슬라이스 집계, 07 검증
        run_aggregate   집계 실행기 (명령행 진입점)
  납품  export      7 CSV → JSONL 납품 형식 내보내기, 스키마 생성, 왕복 검증 (명령행: tools/export_jsonl.py)
        sources     원천 데이터셋 등록부(config/sources.yaml)와 sources.json
        apply_judgments  판정 결과로 04 first_fail_turn·first_cfc_turn 채우기 (명령행: tools/apply_judgments.py)
"""
__version__ = "0.1.0"
