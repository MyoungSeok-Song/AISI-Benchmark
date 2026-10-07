"""명세·설정 오류의 공통 기반.

러너의 도메인 예외(ConfigError, RulesError, OverlayError, SourcesRegistryError, TaxonomyError)는 SetupError를
상속한다. 진입점은 SETUP_ERRORS(context)를 잡아 한 줄 메시지와 종료 코드 2로 끝낸다(traceback 없이).
yaml 설정 파일은 load_yaml로 읽어 '파일 없음'과 'yaml 문법 오류'를 같은 도메인 예외로 바꾼다.
"""
from pathlib import Path

import yaml


class SetupError(Exception):
    """명세·설정 파일이 잘못됐을 때의 공통 기반."""


def load_yaml(path, error_cls=SetupError):
    """yaml 파일 -> 파싱 값. 파일이 없거나 문법이 틀리면 error_cls(파일 이름이 든 메시지)."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as exc:
        raise error_cls(f"{path.name}: 파일이 없습니다 ({path})") from exc
    except yaml.YAMLError as exc:
        raise error_cls(f"{path.name}: yaml 문법 오류 — {exc}") from exc
