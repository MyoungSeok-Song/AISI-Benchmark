"""로컬 vLLM 서버 기동·종료·상태 확인.

  runner/.venv/bin/python tools/vllm_server.py start  --model qwen3-8b-local [--gpu 0]
  runner/.venv/bin/python tools/vllm_server.py status
  runner/.venv/bin/python tools/vllm_server.py stop

서버는 전용 venv(config/runner.yaml vllm_venv)의 vLLM으로 띄운다. 러너 venv와 분리해 러너 쪽을
가볍게 유지한다. 모델은 HF 캐시에 이미 있는 스냅샷 폴더를 경로로 직접 지정하며,
models.yaml의 model_version(revision)과 같은 스냅샷이어야 한다. 없으면 받지 않고 멈춘다.

외부 전송 차단
  HF_HUB_OFFLINE=1         HF Hub에 접속하지 않는다(다운로드·조회 없음)
  VLLM_NO_USAGE_STATS=1    vLLM 사용 통계 전송 끔
  DO_NOT_TRACK=1           위와 같은 목적의 공통 스위치
  --host 127.0.0.1         이 서버 안에서만 접속 가능

기동 정보(명령, dtype, GPU, vLLM 버전, pid)는 runner/var/vllm_server.json에 남고,
실행기가 배치의 batch_manifest.json·runner_events.jsonl에 옮겨 적는다.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RUNNER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNNER_DIR))

from kyab_runner.config import load_config          # noqa: E402

# vLLM 전용 venv는 러너 폴더 밖, 공백 없는 경로에 둔다. vLLM이 쓰는 FlashInfer가 첫 실행 때
# CUDA 커널을 ninja로 빌드하는데, 설치 경로에 공백이 있으면 경로를 끊어 읽어 실패한다
# (프로젝트 폴더 이름 "ETRI_AI BENCHMARK"에 공백이 있음. 심볼릭 링크로는 해결되지 않는다).
# 위치는 config/runner.yaml의 vllm_venv.
VAR_DIR = RUNNER_DIR / "var"
INFO_FILE = VAR_DIR / "vllm_server.json"
LOG_FILE = VAR_DIR / "vllm_server.log"
HF_HUB_DIR = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
STARTUP_TIMEOUT_S = 900
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"}


def snapshot_dir(hf_repo, revision):
    """HF 캐시의 스냅샷 폴더. 없으면 중단한다(자동 다운로드 금지)."""
    path = HF_HUB_DIR / f"models--{hf_repo.replace('/', '--')}" / "snapshots" / revision
    if not (path / "config.json").exists():
        sys.exit(f"HF 캐시에 스냅샷이 없습니다: {path}\n모델 다운로드는 사용자 승인 후 별도로 진행합니다.")
    return path


def vllm_python(config):
    """서버를 띄울 파이썬 경로 (config/runner.yaml vllm_venv)."""
    python = Path(config["vllm_venv"]) / "bin" / "python"
    if " " in str(python) or not python.exists():
        sys.exit(f"vLLM venv가 없거나 경로에 공백이 있습니다: {python}\nREADME의 '로컬 모델' 설치 절을 참고하세요.")
    return python


def is_ready(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(args):
    config = load_config()
    model = config.models.get(args.model)
    if model is None or model.adapter != "local_vllm":
        sys.exit(f"'{args.model}'은 local_vllm 모델이 아닙니다 (config/models.yaml)")
    if INFO_FILE.exists() and process_alive(json.loads(INFO_FILE.read_text())["pid"]):
        sys.exit(f"이미 서버가 떠 있습니다: {INFO_FILE}. 먼저 stop 하세요.")

    server = model.options["server"]
    model_path = snapshot_dir(server["hf_repo"], model.model_version)
    python = str(vllm_python(config))
    command = [python, "-m", "vllm.entrypoints.openai.api_server",
               "--model", str(model_path),
               "--served-model-name", model.options["served_model_name"],
               "--host", "127.0.0.1", "--port", str(server["port"]),
               "--dtype", server["dtype"],
               "--max-model-len", str(server["max_model_len"]),
               "--gpu-memory-utilization", str(server["gpu_memory_utilization"]),
               # 모델 폴더의 generation_config.json(top_k 등 기본 샘플링 값)을 쓰지 않고
               # 요청에 적은 값만 적용한다. 러너가 보낸 파라미터가 곧 실제 파라미터가 되게 한다.
               "--generation-config", "vllm",
               "--seed", "0"]
    env = {**os.environ, **OFFLINE_ENV, "CUDA_VISIBLE_DEVICES": str(args.gpu)}

    VAR_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, "w") as log:
        # 새 세션으로 띄워 이 스크립트가 끝나도 서버가 남고, stop 때 묶음째 종료할 수 있게 한다.
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    # vLLM·torch 버전과 CUDA 빌드. 같은 모델이라도 이 조합이 다르면 출력이 달라질 수 있어 기록한다.
    probe = "import vllm, torch; print(vllm.__version__); print(torch.__version__); print(torch.version.cuda)"
    version, torch_version, cuda_build = subprocess.run(
        [python, "-c", probe], capture_output=True, text=True, env=env).stdout.split()[-3:]
    info = {"pid": process.pid, "model_id": args.model, "hf_repo": server["hf_repo"],
            "revision": model.model_version, "model_path": str(model_path),
            "dtype": server["dtype"], "gpu": args.gpu, "port": server["port"],
            "vllm_version": version, "torch_version": torch_version, "cuda_build": cuda_build,
            "command": command, "env": {**OFFLINE_ENV, "CUDA_VISIBLE_DEVICES": str(args.gpu)},
            "started_at": datetime.now(ZoneInfo(config["timezone"])).isoformat(timespec="seconds")}
    INFO_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"서버 기동 중 (pid {process.pid}, GPU {args.gpu}). 로그: {LOG_FILE}")
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if is_ready(server["port"]):
            print(f"준비 완료: http://127.0.0.1:{server['port']}/v1 (vLLM {version})")
            return 0
        if process.poll() is not None:
            INFO_FILE.unlink()
            sys.exit(f"서버가 시작 중 종료됐습니다 (코드 {process.returncode}). 로그를 확인하세요: {LOG_FILE}")
        time.sleep(3)
    sys.exit(f"{STARTUP_TIMEOUT_S}초 안에 준비되지 않았습니다. stop 후 로그를 확인하세요.")


def stop(_args):
    if not INFO_FILE.exists():
        print("기록된 서버가 없습니다.")
        return 0
    pid = json.loads(INFO_FILE.read_text())["pid"]
    if process_alive(pid):
        os.killpg(pid, signal.SIGTERM)                # start_new_session으로 띄웠으므로 pid = 프로세스 그룹
        for _ in range(60):
            if not process_alive(pid):
                break
            time.sleep(1)
        else:
            os.killpg(pid, signal.SIGKILL)
    INFO_FILE.unlink()
    print(f"서버 종료 (pid {pid})")
    return 0


def status(_args):
    if not INFO_FILE.exists():
        print("기록된 서버가 없습니다.")
        return 1
    info = json.loads(INFO_FILE.read_text())
    alive, ready = process_alive(info["pid"]), is_ready(info["port"])
    print(f"pid {info['pid']} alive={alive} ready={ready} model={info['model_id']} gpu={info['gpu']} "
          f"vllm={info['vllm_version']}")
    return 0 if ready else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    p_start = sub.add_parser("start")
    p_start.add_argument("--model", required=True, help="config/models.yaml의 local_vllm 모델 ID")
    p_start.add_argument("--gpu", type=int, default=0, help="사용할 GPU 번호 (1장만)")
    p_start.set_defaults(func=start)
    sub.add_parser("stop").set_defaults(func=stop)
    sub.add_parser("status").set_defaults(func=status)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
