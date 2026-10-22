#!/usr/bin/env bash
# Second local model for the exploratory live pilot: Qwen3-32B-FP8 (block-wise FP8 e4m3 weights) on vLLM.
# Same conventions as serve_qwen3_8b.sh: up only for the run, down right after (scripts/stop_qwen3_32b_fp8.sh).
# max-model-len 32768 (KYAB_MAX_LEN): max_output_tokens 8192 (10/05 reply) on a 3-turn script needs
#   max_model_len − 8192 ≥ 2 × 8192 + prompt reserve (same budget rule as the team runner). max-num-seqs 16
#   (KYAB_MAX_SEQS) ≥ runner concurrency 8 and keeps the sampler warm-up small on the shared GPU.
#   The output can depend on max_model_len, so keep it fixed for a whole evaluation.
set -euo pipefail
VENV=/home/ubuntu/342/yeonjun/evconflict/.venv          # vLLM 0.11.0, torch 2.8.0+cu128
REV=aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df            # HF commit of the cached checkpoint
exec env CUDA_VISIBLE_DEVICES="${KYAB_GPU:-1}" HF_HUB_OFFLINE=1 "$VENV/bin/vllm" serve Qwen/Qwen3-32B-FP8 \
  --revision "$REV" \
  --served-model-name Qwen/Qwen3-32B-FP8 \
  --port "${KYAB_PORT:-8311}" \
  --gpu-memory-utilization "${KYAB_GPU_UTIL:-0.60}" \
  --max-model-len "${KYAB_MAX_LEN:-32768}" \
  --max-num-seqs "${KYAB_MAX_SEQS:-16}" \
  --generation-config vllm \
  --seed 0
