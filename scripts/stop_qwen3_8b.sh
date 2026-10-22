#!/usr/bin/env bash
# Stop the pilot vLLM server (anchored match: only the server started by serve_qwen3_8b.sh on port 8310).
for p in $(pgrep -f "^/home/ubuntu/342/yeonjun/evconflict/.venv/bin/python[0-9.]* /home/ubuntu/342/yeonjun/evconflict/.venv/bin/vllm serve Qwen/Qwen3-8B .*--port ${KYAB_PORT:-8310}"); do kill "$p"; done
