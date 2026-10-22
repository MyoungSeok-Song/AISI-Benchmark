#!/usr/bin/env bash
# Local target model for the exploratory live pilot: Qwen3-8B (bf16) on vLLM's OpenAI-compatible server.
# Shared GPU host: bring it up only for the run, take it down right after (scripts/stop_qwen3_8b.sh).
# --generation-config vllm: do NOT inherit Qwen's generation_config.json sampling defaults
#   (temperature 0.6, top_k 20, top_p 0.95); the runner sends temperature/top_p explicitly.
set -euo pipefail
VENV=/home/ubuntu/342/yeonjun/evconflict/.venv          # vLLM 0.11.0, torch 2.8.0+cu128
REV=b968826d9c46dd6066d109eabc6255188de91218            # HF commit of the cached checkpoint
exec env CUDA_VISIBLE_DEVICES="${KYAB_GPU:-1}" HF_HUB_OFFLINE=1 "$VENV/bin/vllm" serve Qwen/Qwen3-8B \
  --revision "$REV" \
  --served-model-name Qwen/Qwen3-8B \
  --dtype bfloat16 \
  --port "${KYAB_PORT:-8310}" \
  --gpu-memory-utilization "${KYAB_GPU_UTIL:-0.45}" \
  --max-model-len 16384 \
  --generation-config vllm \
  --seed 0
