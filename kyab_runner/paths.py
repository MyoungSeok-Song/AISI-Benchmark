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

DEFAULT_INPUT_DIR = SAMPLES_DIR / "input"
DEFAULT_OUTPUT_DIR = SAMPLES_DIR / "output"
