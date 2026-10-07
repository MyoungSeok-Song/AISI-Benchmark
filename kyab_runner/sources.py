"""원천 데이터셋 등록부(config/sources.yaml)와 납품 sources.json.

원본 파일은 프로젝트 폴더 data/(러너 밖)에 있고, 판본·위치의 근거는 등록부에 있다.
등록부의 sha256과 로컬 파일의 sha256이 같을 때만 version(HF 커밋)·location(저장소·커밋·파일 URL)을 채운다.
값은 코드에 박지 않고 등록부에서만 읽는다(tools/build_samples.py도 원본 파일 이름을 여기서 가져간다).
"""
import re
from pathlib import Path

from . import fileio, paths
from .errors import SetupError, load_yaml

DEFAULT_DATA_DIR = paths.DEFAULT_DATA_DIR
DATA_DISPLAY_PREFIX = f"{paths.DEFAULT_DATA_DIR.name}/"        # sources.json에 적는 원본 파일 표기('data/…')
SOURCE_REGISTRY_KEYS = ("local_file", "hf_repo", "hf_commit", "hf_file", "sha256", "basis")
SELF_AUTHORED_SOURCE = "NEW"                                   # 원천이 없는 신규 작성 문항의 source_benchmark 값


class SourcesRegistryError(SetupError):
    """config/sources.yaml 형식 오류."""


def load_sources_registry(path=paths.SOURCES_YAML):
    """원천 등록부 {원천 이름: {local_file, hf_repo, hf_commit, hf_file, sha256, basis}}. 파일이 없으면 빈 dict."""
    path = Path(path)
    if not path.exists():
        return {}
    raw = load_yaml(path, SourcesRegistryError) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("sources", {}), dict):
        raise SourcesRegistryError(f"{path.name}: 최상위와 sources는 매핑(키: 값)이어야 합니다")
    registry = raw.get("sources") or {}
    for name, entry in registry.items():
        if not isinstance(entry, dict):
            raise SourcesRegistryError(f"{path.name} {name}: 매핑(키: 값)이어야 합니다 (현재 {entry!r})")
        missing = [k for k in SOURCE_REGISTRY_KEYS if not entry.get(k)]
        if missing:
            raise SourcesRegistryError(f"{path.name} {name}: 빈 항목 {missing}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"])):
            raise SourcesRegistryError(f"{path.name} {name}: sha256 형식이 아님 {entry['sha256']!r}")
        if not re.fullmatch(r"[0-9a-f]{7,40}", str(entry["hf_commit"])):
            raise SourcesRegistryError(f"{path.name} {name}: hf_commit 형식이 아님 {entry['hf_commit']!r}")
    return registry


def hf_location(entry):
    """HF 저장소·커밋·파일이 모두 보이는 위치 표기(URL)."""
    return f"https://huggingface.co/datasets/{entry['hf_repo']}/blob/{entry['hf_commit']}/{entry['hf_file']}"


def sources_skeleton(index, data_dir=DEFAULT_DATA_DIR, registry=None):
    """원천 데이터셋 목록. 반환 dict의 "warnings"에 화면에 낼 경고를 모은다.

    원천별 채움 규칙:
      등록부에 있고 data_dir의 원본 sha256이 등록부와 같음 → version(hf_commit)·location(URL)·origin·basis를 채움
      sha256이 다름 → 빈칸 + TODO + 경고(등록부와 로컬 파일 중 어느 쪽이 맞는지 사람이 확인)
      원본 파일 없음 → 빈칸 + TODO + 경고
      등록부에 없음(NEW 등) → 빈칸 + TODO
    acquired_at(취득일)은 근거가 없어 항상 TODO.
    """
    registry = load_sources_registry() if registry is None else registry
    sources, warnings = {}, []
    for item in index.tables["01_items"]:
        name = item["source_benchmark"]
        entry = sources.setdefault(name, {"version": "", "location": "", "license": set(), "acquired_at": "",
                                          "local_file": "", "sha256": "", "item_count": 0, "TODO": []})
        entry["license"].add(item["source_license"])
        entry["item_count"] += 1
    for name, entry in sources.items():
        entry["license"] = sorted(entry["license"])
        known = registry.get(name)
        if known:
            local = Path(data_dir) / known["local_file"]
            entry["local_file"] = DATA_DISPLAY_PREFIX + known["local_file"]
            if local.exists():
                entry["sha256"] = fileio.sha256_file(local)
                if entry["sha256"] == known["sha256"]:
                    entry["version"] = known["hf_commit"]
                    entry["location"] = hf_location(known)
                    entry["origin"] = {k: known[k] for k in ("hf_repo", "hf_commit", "hf_file")}
                    entry["basis"] = known["basis"]
                else:
                    message = (f"{name}: 원본 파일 sha256이 등록부(config/sources.yaml)와 다름 — 로컬 {entry['sha256'][:12]}… "
                               f"등록 {known['sha256'][:12]}… (판본·위치 미기록)")
                    entry["TODO"].append(message)
                    warnings.append(message)
            else:
                message = f"{name}: 원본 파일 없음 {DATA_DISPLAY_PREFIX}{known['local_file']} (sha256·판본·위치 미기록)"
                entry["TODO"].append(message)
                warnings.append(message)
        entry["TODO"] = [k for k in ("version", "location", "acquired_at") if not entry[k]] + entry["TODO"]
        if name == SELF_AUTHORED_SOURCE:
            entry["TODO"].append("작성 근거 기록 방식(협의 후보 P3)")
    return {"note": "원천 데이터셋 목록(연구실 A 제안). version=HF 커밋, location=저장소·커밋·파일 URL은 등록부(config/sources.yaml)의 "
                    "sha256과 로컬 원본이 일치할 때만 채운다. acquired_at(취득일)은 근거가 없어 비워 둔다(협의 후보 P1).",
            "sources": sources, "warnings": warnings}
