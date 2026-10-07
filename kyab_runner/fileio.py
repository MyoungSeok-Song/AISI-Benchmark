"""JSON·JSONL 쓰기와 파일 해시의 주인. 바이트 규약을 한 곳에 둔다.

  write_json      json.dumps(ensure_ascii=False, indent=2), UTF-8, 끝에 줄바꿈 없음. 임시 파일에 쓴 뒤 바꿔치기(원자적)
  append_jsonl    한 줄 = json.dumps(ensure_ascii=False) + '\\n', UTF-8, 줄바꿈은 '\\n' 고정
  write_jsonl     전체를 새로 쓴다(atomic=True면 임시 파일 → 바꿔치기)
  sha256_file     파일 바이트의 sha256 (16진 소문자)
코드북 CSV는 csv_io가 맡는다.
"""
import hashlib
import json
from pathlib import Path


def dumps_pretty(data):
    """사람이 읽는 JSON 표기(manifest·notes). 한글 그대로, 들여쓰기 2."""
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path, data):
    """임시 파일에 쓴 뒤 바꿔치기한다(덮어쓰는 도중 끊겨도 반쪽 파일이 남지 않게)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps_pretty(data), encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _jsonl_line(record):
    return json.dumps(record, ensure_ascii=False) + "\n"


def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(_jsonl_line(record))


def write_jsonl(path, records, atomic=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_name(path.name + ".tmp") if atomic else path
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(_jsonl_line(record))
    if atomic:
        target.replace(path)


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
