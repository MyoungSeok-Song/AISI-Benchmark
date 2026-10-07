"""파일 이름의 주인. 다른 모듈은 파일 이름을 직접 적지 않고 여기서 가져온다.

패키지 안의 어떤 모듈도 import하지 않는다(가장 아래 층).

  <입력 폴더>/            01_items.csv · 02_item_tags.csv · 03_prompts.csv         (INPUT_FILES)
  <출력 루트>/
    RBATCH-YYYYMMDD-###/  batch_manifest.json          배치의 고정 조건·입력 해시·어댑터 정보
                          04_runs.csv · 05_responses.csv                          실행 기록
                          runner_events.jsonl          코드북 밖 보조 로그(턴별 지연, 재시도, apply 백업 등)
                          06_judgments_template.csv    판정 단계가 채울 빈 틀
                          judge_inputs.jsonl           판정기가 볼 입력 묶음(내부 중간 산출물)
                          06_judgments.csv             판정 기록
                          judge_manifest.json          무엇으로 판정했는지
    RESULTS-YYYYMMDD-###/ 07_results.csv · results_denominators.csv · results_notes.json
  <납품 폴더>/            items.jsonl · responses/<model_id>.jsonl · judgments/<model_id>.jsonl · results/ · schema/
                          manifest.json · sources.json

CSV 파일 이름은 코드북 표 이름 + '.csv'다(코드북 7 CSV). 그 밖의 파일은 러너가 정한 보조 산출물이다.
"""
INPUT_FILES = {"01_items": "01_items.csv", "02_item_tags": "02_item_tags.csv", "03_prompts": "03_prompts.csv"}

# 배치 폴더
BATCH_MANIFEST_FILE = "batch_manifest.json"
RUNS_FILE = "04_runs.csv"
RESPONSES_FILE = "05_responses.csv"
EVENTS_FILE = "runner_events.jsonl"
JUDGMENTS_TEMPLATE_FILE = "06_judgments_template.csv"
JUDGE_INPUTS_FILE = "judge_inputs.jsonl"
JUDGMENTS_FILE = "06_judgments.csv"
JUDGE_MANIFEST_FILE = "judge_manifest.json"

# 집계 결과 폴더
RESULTS_FILE = "07_results.csv"
DENOMINATORS_FILE = "results_denominators.csv"
NOTES_FILE = "results_notes.json"
RESULTS_FOLDER_FILES = (RESULTS_FILE, DENOMINATORS_FILE, NOTES_FILE)

# 납품 폴더
DELIVERY_ITEMS_FILE = "items.jsonl"
DELIVERY_MANIFEST_FILE = "manifest.json"
DELIVERY_SOURCES_FILE = "sources.json"
DELIVERY_RESPONSES_DIR = "responses"
DELIVERY_JUDGMENTS_DIR = "judgments"
DELIVERY_RESULTS_DIR = "results"
DELIVERY_SCHEMA_DIR = "schema"
