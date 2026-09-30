"""KYAB 러너: 코드북 7 CSV 형식으로 모델 실행 기록(04_runs, 05_responses)을 만든다.

모듈 구성
  paths       폴더 위치
  codebook    코드북 추출본 + 확정 변경(overlay) 로드, 값·행 검사
  taxonomy    A1~A10 분류체계와 이전 코드 대응표
  config      runner.yaml · models.yaml 로드
  csv_io      코드북 열 순서로 CSV 읽기·쓰기
  validate    입력 3종(01·02·03) 검증
  ids         RBATCH · RUN · RESP 발급
  messages    모델 요청 메시지 구성 (AI 전달 ○/△ 필드만)
  adapters    모델 호출부 (지금은 mock만)
  session     단일턴·3턴 실행기가 함께 쓰는 실행 1건의 기록 절차
  run_single / run_multiturn   실행기 (명령행 진입점)
  judge_io    06_judgments 빈 틀
"""
__version__ = "0.1.0"
