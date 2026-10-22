#!/usr/bin/env bash
for p in $(pgrep -f "^/home/ubuntu/342/yeonjun/evconflict/.venv/bin/python[0-9.]* /home/ubuntu/342/yeonjun/evconflict/.venv/bin/vllm serve Qwen/Qwen3-32B-FP8 .*--port ${KYAB_PORT:-8311}"); do kill "$p"; done
