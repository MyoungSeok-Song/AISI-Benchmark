"""추출기(extract_codebook · extract_taxonomy) 공용: 원본 xlsx 찾기, 해시, 생성 모듈 쓰기.

명세(코드북·분류체계)는 xlsx가 유일한 원천이고, 러너는 그것을 기계적으로 옮긴 파이썬 모듈
(kyab_runner/spec/*_data.py)을 읽는다(사용자 지시 2026-10-07). 생성 모듈은 손으로 고치지 않고,
원본이 바뀌면 추출기를 다시 돌린다. 모듈 머리에는 원본 파일 이름·sha256·생성 명령을 적어
어느 원본에서 나왔는지 되짚을 수 있게 한다. 시각은 적지 않는다(같은 원본이면 같은 바이트).
"""
import glob
import hashlib
import os
import pprint
import sys
import unicodedata
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent
SPEC_DIR = RUNNER_DIR / "kyab_runner" / "spec"
# 원본 xlsx의 기본 위치(프로젝트 폴더, 저장소 밖·공유 대상 아님). --xlsx로 다른 경로를 줄 수 있다.
DEFAULT_SRC_DIR = RUNNER_DIR.parent / "project proposal"


def nfc(text):
    """macOS에서 온 파일명은 자모가 분리(NFD)돼 있어 비교 전에 NFC로 맞춘다."""
    return unicodedata.normalize("NFC", text)


def locate_xlsx(src_dir, name_key):
    """src_dir에서 파일명(NFC)에 name_key가 든 xlsx 1개의 경로. 없으면 None."""
    for path in sorted(glob.glob(os.path.join(src_dir, "*.xlsx"))):
        if name_key in nfc(os.path.basename(path)):
            return path
    return None


def find_xlsx(src_dir, name_key):
    """locate_xlsx와 같되 없으면 중단(종료 코드 2)."""
    path = locate_xlsx(src_dir, name_key)
    if path is None:
        sys.exit(f"'{name_key}' xlsx를 {src_dir}에서 찾지 못했습니다 (--xlsx로 경로를 주세요)")
    return path


def resolve_xlsx(arg, name_key):
    """--xlsx 인자가 있으면 그 파일(없으면 중단), 없으면 기본 폴더에서 찾는다."""
    if arg is None:
        return find_xlsx(DEFAULT_SRC_DIR, name_key)
    if not os.path.isfile(arg):
        sys.exit(f"xlsx 파일이 없습니다: {arg}")
    return arg


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_module(docstring, source_path, command, assignments):
    """생성 모듈 본문. assignments는 [(이름, 값)] 순서대로 `이름 = <pformat>`로 적는다.

    pprint.pformat(sort_dicts=False)는 dict 키 순서를 지키므로 json 추출본과 같은 순서가 남는다.
    """
    header = [f'"""{docstring}', "",
              "자동 생성 — 손으로 고치지 말 것. 원본이 바뀌면 추출기를 다시 돌린다.",
              f"  원본 파일: {nfc(os.path.basename(source_path))}",
              f"  원본 sha256: {file_sha256(source_path)}",
              f"  생성 명령: {command}", '"""']
    body = []
    for name, value in assignments:
        body.append(f"{name} = {pprint.pformat(value, sort_dicts=False, width=120)}")
    return "\n".join(header) + "\n" + "\n\n".join(body) + "\n"


def write_module(path, text):
    """같은 내용이면 다시 쓰지 않는다(바이트 동일성을 눈으로 확인할 수 있게 출력)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"변경 없음: {path}")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return True
