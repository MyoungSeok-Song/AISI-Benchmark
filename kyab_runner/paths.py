"""러너 폴더 위치. 다른 모듈은 경로를 직접 조립하지 않고 여기서 가져온다."""
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent      # .../runner
SCHEMA_DIR = RUNNER_DIR / "schema"
CONFIG_DIR = RUNNER_DIR / "config"
SAMPLES_DIR = RUNNER_DIR / "samples"

# 코드북·분류체계 추출본은 파일이 아니라 생성 모듈(kyab_runner/spec/)이다. overlay만 yaml로 남는다.
OVERLAY_YAML = SCHEMA_DIR / "overlay_v0.3_confirmed.yaml"

RUNNER_YAML = CONFIG_DIR / "runner.yaml"                         # 코드북이 정하지 않은 규칙의 기본값
MODELS_YAML = CONFIG_DIR / "models.yaml"                         # 모델 등록부
AGGREGATION_RULES_YAML = CONFIG_DIR / "aggregation_rules.yaml"   # 판정·집계 가정 모음
JUDGES_YAML = CONFIG_DIR / "judges.yaml"                         # 판정기 등록부
SOURCES_YAML = CONFIG_DIR / "sources.yaml"                       # 원천 데이터셋 등록부(납품 sources.json 근거)
DEFAULT_DATA_DIR = RUNNER_DIR.parent / "data"                    # 원천 데이터셋 원본 CSV(프로젝트 폴더, 러너 밖)

DEFAULT_INPUT_DIR = SAMPLES_DIR / "input"
DEFAULT_OUTPUT_DIR = SAMPLES_DIR / "output"

VAR_DIR = RUNNER_DIR / "var"                                     # 실행 부산물(git 제외): 서버 기동 정보·로그, e2e 출력
VLLM_SERVER_INFO = VAR_DIR / "vllm_server.json"                  # tools/vllm_server.py가 쓰고 local_vllm 어댑터가 읽음
VLLM_SERVER_LOG = VAR_DIR / "vllm_server.log"
