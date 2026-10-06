"""러너 폴더 위치. 다른 모듈은 경로를 직접 조립하지 않고 여기서 가져온다."""
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent      # .../runner
SCHEMA_DIR = RUNNER_DIR / "schema"
CONFIG_DIR = RUNNER_DIR / "config"
SAMPLES_DIR = RUNNER_DIR / "samples"

CODEBOOK_JSON = SCHEMA_DIR / "codebook_v0.2.json"
OVERLAY_YAML = SCHEMA_DIR / "overlay_v0.3_confirmed.yaml"
TAXONOMY_JSON = SCHEMA_DIR / "taxonomy_A1-A10.json"
CROSSWALK_CSV = SCHEMA_DIR / "crosswalk_RM_to_A.csv"

AGGREGATION_RULES_YAML = CONFIG_DIR / "aggregation_rules.yaml"   # 판정·집계 가정 모음
JUDGES_YAML = CONFIG_DIR / "judges.yaml"                         # 판정기 등록부
SOURCES_YAML = CONFIG_DIR / "sources.yaml"                       # 원천 데이터셋 등록부(납품 sources.json 근거)
DEFAULT_DATA_DIR = RUNNER_DIR.parent / "data"                    # 원천 데이터셋 원본 CSV(프로젝트 폴더, 러너 밖)

DEFAULT_INPUT_DIR = SAMPLES_DIR / "input"
DEFAULT_OUTPUT_DIR = SAMPLES_DIR / "output"
