"""실행 코드 출처: 러너 git 상태와 execution_library_version.

04_runs.execution_library_version, batch_manifest.json, 납품 manifest의 generated_by·runner_git이 여기서 나온다.
본평가는 .dirty가 붙지 않은 상태에서 돌려야 실행 코드를 커밋으로 되짚을 수 있다.
"""
import hashlib
import subprocess
from pathlib import Path

from . import __version__, paths


def _git(*args):
    """러너 폴더에서 git 명령을 실행해 출력을 돌려준다. git이 없거나 실패하면 None."""
    try:
        return subprocess.run(["git", "-C", str(paths.RUNNER_DIR), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_state():
    """러너 저장소의 커밋과 수정 상태. 납품 manifest가 실행 코드를 되짚을 수 있게 남긴다.

    반환: {"commit": 전체 SHA, "dirty": 커밋 안 된 수정 유무, "uncommitted": 그 파일 수}.
    runner/ 가 git 저장소가 아니면 None. 상위 폴더의 다른 저장소를 잡지 않도록 최상위가 runner/ 자신인지 확인한다.
    """
    top = _git("rev-parse", "--show-toplevel")
    sha = _git("rev-parse", "HEAD")
    if not (top and sha and Path(top).resolve() == paths.RUNNER_DIR):
        return None
    status = _git("status", "--porcelain") or ""
    changed = [line for line in status.splitlines() if line.strip()]
    return {"commit": sha, "dirty": bool(changed), "uncommitted": len(changed)}


def library_version():
    """execution_library_version. 예: runner-0.1.0+abc1234

    runner/ 가 git 저장소면   runner-<버전>+<커밋 SHA 7자리>
    커밋 안 된 변경이 있으면   runner-<버전>+<SHA>.dirty  (기록은 하되 표시를 남긴다)
    git 저장소가 아니면        runner-<버전>+src<소스 해시 7자리>  (대체 수단)
    """
    state = git_state()
    if state:
        sha = _git("rev-parse", "--short=7", "HEAD")
        if sha:
            return f"runner-{__version__}+{sha}{'.dirty' if state['dirty'] else ''}"
    digest = hashlib.sha256()
    for source in sorted((paths.RUNNER_DIR / "kyab_runner").rglob("*.py")):
        digest.update(source.read_bytes())
    return f"runner-{__version__}+src{digest.hexdigest()[:7]}"
