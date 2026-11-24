#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
: "${MODEL:?MODEL must be an exact Recipes model ID}"
HARDWARE=${HARDWARE:-arc_pro_b70}
SWEEP_STAGE=${SWEEP_STAGE:-all}
VLLM_IMAGE=${VLLM_IMAGE:-vllm/vllm-openai-xpu:latest}
RECIPES_BASE_URL=${RECIPES_BASE_URL:-https://recipes.vllm.ai}
RECIPE_TOOLS_REPO=${RECIPE_TOOLS_REPO-https://github.com/intel-ai-tce/vllm.git}
RECIPE_TOOLS_REF=${RECIPE_TOOLS_REF-xpu_vllm_recipes_tool}
INPUT_TOKENS=${INPUT_TOKENS:-128}
OUTPUT_TOKENS=${OUTPUT_TOKENS:-128}
CONCURRENCY=${CONCURRENCY:-32}
TTFT_SLA_MS=${TTFT_SLA_MS:-3000}
TPOT_SLA_MS=${TPOT_SLA_MS:-100}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-}
ENABLE_CONTEXT_SWEEP=${ENABLE_CONTEXT_SWEEP:-false}
CONTEXT_LENGTHS=${CONTEXT_LENGTHS:-4096,8192,16384,32768}
CONTEXT_NUM_PROMPTS=${CONTEXT_NUM_PROMPTS:-100}
SWEEP_SERVER_READY_TIMEOUT=${SWEEP_SERVER_READY_TIMEOUT:-1800}
WORK_ROOT=${WORK_ROOT:-${GITHUB_WORKSPACE:-$ROOT}/work/xpu-sweep}
RESULT_ROOT=${RESULT_ROOT:-${GITHUB_WORKSPACE:-$ROOT}/results/xpu-sweep}
HF_HOME=${HF_HOME:-$HOME/.cache/huggingface}

[[ "$HARDWARE" =~ ^[a-z][a-z0-9_]*$ ]] || { echo 'Invalid Recipes hardware identifier' >&2; exit 2; }
case "$SWEEP_STAGE" in all|parallel-layout|concurrency|scheduler) ;; *) echo 'Invalid SWEEP_STAGE' >&2; exit 2 ;; esac
for v in INPUT_TOKENS OUTPUT_TOKENS CONCURRENCY; do
  [[ "${!v}" =~ ^[1-9][0-9]*$ ]] || { echo "$v must be a positive integer" >&2; exit 2; }
done
if [[ -n "$MAX_MODEL_LEN" ]]; then
  [[ "$MAX_MODEL_LEN" =~ ^[1-9][0-9]*$ ]] || { echo 'MAX_MODEL_LEN must be a positive integer' >&2; exit 2; }
  (( MAX_MODEL_LEN >= INPUT_TOKENS + OUTPUT_TOKENS )) || {
    echo 'MAX_MODEL_LEN must cover input + output tokens' >&2; exit 2;
  }
fi
if [[ "$ENABLE_CONTEXT_SWEEP" != true && "$ENABLE_CONTEXT_SWEEP" != false ]]; then
  echo 'ENABLE_CONTEXT_SWEEP must be true or false' >&2; exit 2
fi
if [[ "$ENABLE_CONTEXT_SWEEP" == true ]]; then
  [[ "$SWEEP_STAGE" == all ]] || {
    echo 'Context sweep requires SWEEP_STAGE=all' >&2; exit 2
  }
  [[ -z "$MAX_MODEL_LEN" ]] || {
    echo 'Use either MAX_MODEL_LEN or context sweeping, not both' >&2; exit 2
  }
  [[ "$CONTEXT_NUM_PROMPTS" =~ ^[1-9][0-9]*$ ]] || {
    echo 'CONTEXT_NUM_PROMPTS must be a positive integer' >&2; exit 2
  }
  [[ "$CONTEXT_LENGTHS" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]] || {
    echo 'CONTEXT_LENGTHS must be comma-separated positive integers' >&2; exit 2
  }
  [[ -n "$RECIPE_TOOLS_REPO" && -n "$RECIPE_TOOLS_REF" ]] || {
    echo 'Context sweep requires a Recipes source ref containing context_length_sweep.py' >&2
    exit 2
  }
fi
[[ "$WORK_ROOT" = /* && "$RESULT_ROOT" = /* && "$HF_HOME" = /* ]] || {
  echo 'WORK_ROOT, RESULT_ROOT, and HF_HOME must be absolute paths' >&2; exit 2;
}
[[ -d /dev/dri ]] && compgen -G '/dev/dri/renderD*' >/dev/null || {
  echo 'No Intel XPU render nodes detected under /dev/dri' >&2; exit 2;
}
command -v docker >/dev/null && docker info >/dev/null || { echo 'Docker is unavailable' >&2; exit 2; }
mkdir -p "$WORK_ROOT" "$HF_HOME" "$RESULT_ROOT"
# Leave other runs untouched; workflow uses a unique result directory.
"$ROOT/scripts/collect_system_info.sh" "$RESULT_ROOT/system-info.txt"
{ echo; echo '===== xpu-smi discovery ====='; xpu-smi discovery 2>&1 || true;
  echo; echo '===== DRM render nodes ====='; ls -l /dev/dri/renderD*;
} >> "$RESULT_ROOT/system-info.txt"

echo "==> Pulling XPU image: $VLLM_IMAGE"
docker pull "$VLLM_IMAGE"
docker image inspect "$VLLM_IMAGE" > "$RESULT_ROOT/docker-image.json"

TOOL_PATH=/vllm-workspace/tools/recipes
TOOLS_DIR=''
TOOLS_SHA=''
TOOLS_MOUNTS=()
if [[ -n "$RECIPE_TOOLS_REPO" || -n "$RECIPE_TOOLS_REF" ]]; then
  [[ -n "$RECIPE_TOOLS_REPO" && -n "$RECIPE_TOOLS_REF" ]] || {
    echo 'RECIPE_TOOLS_REPO and RECIPE_TOOLS_REF must be set together' >&2; exit 2;
  }
  TOOLS_DIR=$(mktemp -d "$WORK_ROOT/recipe-tools.XXXXXX")
  trap '[[ -z "$TOOLS_DIR" ]] || rm -rf -- "$TOOLS_DIR"' EXIT
  git -C "$TOOLS_DIR" init -q
  git -C "$TOOLS_DIR" remote add origin "$RECIPE_TOOLS_REPO"
  git -C "$TOOLS_DIR" fetch --depth 1 origin "$RECIPE_TOOLS_REF"
  git -C "$TOOLS_DIR" checkout -q --detach FETCH_HEAD
  TOOLS_SHA=$(git -C "$TOOLS_DIR" rev-parse HEAD)
  [[ -f "$TOOLS_DIR/tools/recipes/recipe_json_to_vllm_config.py" &&
     -f "$TOOLS_DIR/tools/recipes/sweep/sweep_generation.py" ]] || {
    echo 'Recipes sweep tools are missing in requested ref' >&2; exit 2;
  }
  if [[ "$ENABLE_CONTEXT_SWEEP" == true &&
        ! -f "$TOOLS_DIR/tools/recipes/sweep/context_length_sweep.py" ]]; then
    echo 'Selected Recipes ref does not contain context_length_sweep.py' >&2
    exit 2
  fi
  TOOL_PATH=/recipes
  TOOLS_MOUNTS=(-v "$TOOLS_DIR/tools/recipes:/recipes:ro")
fi

python3 - "$RESULT_ROOT/recipe-tools.json" "$RECIPE_TOOLS_REPO" "$RECIPE_TOOLS_REF" "$TOOLS_SHA" "$VLLM_IMAGE" "$RESULT_ROOT/docker-image.json" <<'PY'
import json, sys
from pathlib import Path
out, repo, ref, sha, image, info = sys.argv[1:]
data = json.loads(Path(info).read_text())[0]
Path(out).write_text(json.dumps({"source": "github" if repo else "image", "repository": repo or None,
 "ref": ref or None, "commit": sha or None, "image": image, "image_id": data.get("Id"),
 "image_digests": data.get("RepoDigests", [])}, indent=2) + "\n")
PY

GROUP_ARGS=()
# The runner UID needs the numeric host render-node group(s) inside Docker.
while IFS= read -r gid; do
  [[ -n "$gid" ]] && GROUP_ARGS+=(--group-add "$gid")
done < <(stat -c '%g' /dev/dri/renderD* | sort -un)

DOCKER_ENV=(
 -e "SWEEP_MODEL=$MODEL" -e "SWEEP_HARDWARE=$HARDWARE"
 -e "SWEEP_STAGE=$SWEEP_STAGE" -e "SWEEP_RECIPES_BASE_URL=$RECIPES_BASE_URL"
 -e "SWEEP_INPUT_TOKENS=$INPUT_TOKENS" -e "SWEEP_OUTPUT_TOKENS=$OUTPUT_TOKENS"
 -e "SWEEP_CONCURRENCY=$CONCURRENCY" -e "SWEEP_TTFT_SLA_MS=$TTFT_SLA_MS"
 -e "SWEEP_TPOT_SLA_MS=$TPOT_SLA_MS" -e "SWEEP_MAX_MODEL_LEN=$MAX_MODEL_LEN"
 -e "SWEEP_SERVER_READY_TIMEOUT=$SWEEP_SERVER_READY_TIMEOUT"
 -e "ENABLE_CONTEXT_SWEEP=$ENABLE_CONTEXT_SWEEP"
 -e "CONTEXT_LENGTHS=$CONTEXT_LENGTHS"
 -e "CONTEXT_NUM_PROMPTS=$CONTEXT_NUM_PROMPTS"
 -e "RECIPE_TOOL_PATH=$TOOL_PATH" -e HF_HOME=/hf-cache -e HF_HUB_CACHE=/hf-cache/hub
 -e HOME=/tmp/vllm-home
)
if [[ -n "${HF_TOKEN:-}" ]]; then DOCKER_ENV+=(-e "HF_TOKEN=$HF_TOKEN"); fi
for name in ZE_AFFINITY_MASK ONEAPI_DEVICE_SELECTOR SYCL_DEVICE_FILTER HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  [[ -z "${!name:-}" ]] || DOCKER_ENV+=(-e "$name=${!name}")
done

set +e
docker run --rm \
  --user "$(id -u):$(id -g)" \
  "${GROUP_ARGS[@]}" \
  --entrypoint bash \
  --device /dev/dri:/dev/dri \
  --security-opt seccomp=unconfined \
  --cap-add SYS_NICE \
  --shm-size=4g \
  "${DOCKER_ENV[@]}" \
  "${TOOLS_MOUNTS[@]}" \
  -v "$RESULT_ROOT:/output" \
  -v "$HF_HOME:/hf-cache" \
  -w /output \
  "$VLLM_IMAGE" -lc '
    set -euo pipefail
    mkdir -p "$HOME"
    python3 - <<"PY"
import json, torch
if not torch.xpu.is_available():
    raise SystemExit("PyTorch XPU is unavailable inside the container")
devices = [{"index": i, "name": torch.xpu.get_device_name(i)} for i in range(torch.xpu.device_count())]
if not devices:
    raise SystemExit("No process-visible XPU devices")
print("Process-visible XPU devices:", devices, flush=True)
with open("/output/xpu-devices.json", "w", encoding="utf-8") as f:
    json.dump(devices, f, indent=2)
PY
    CONVERTER_ARGS=()
    if [[ -n "$SWEEP_MAX_MODEL_LEN" ]]; then
       CONVERTER_ARGS+=(--max-model-len "$SWEEP_MAX_MODEL_LEN")
    fi
    python3 "$RECIPE_TOOL_PATH/recipe_json_to_vllm_config.py" \
      --model "$SWEEP_MODEL" --hardware "$SWEEP_HARDWARE" \
      --api-base "$SWEEP_RECIPES_BASE_URL" --detect-hardware \
      --auto-fit-xpu-parallelism \
      --validation-result-out /output/recipe-validation-status.json \
      --input-tokens "$SWEEP_INPUT_TOKENS" --output-tokens "$SWEEP_OUTPUT_TOKENS" \
      --concurrency "$SWEEP_CONCURRENCY" \
      --ttft-sla-ms "$SWEEP_TTFT_SLA_MS" --tpot-sla-ms "$SWEEP_TPOT_SLA_MS" \
      --config-out /output/config.yml --env-out /output/env.sh \
      --sweep-stage "$SWEEP_STAGE" --sweep-out-dir /output/sweep \
      "${CONVERTER_ARGS[@]}"
    cd /output/sweep
    case "$SWEEP_STAGE" in
      all)
        if [[ "$ENABLE_CONTEXT_SWEEP" == true ]]; then
          # The context sweep must use the selected layout; then downstream
          # stages consume the updated parallel-layout-config.yml.
          bash ./run_parallel_layout_sweep.sh \
            --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
          python3 ./recommend_parallel_layout.py
          python3 "$RECIPE_TOOL_PATH/sweep/context_length_sweep.py" \
            --config /output/sweep/parallel-layout-config.yml \
            --env /output/env.sh \
            --output-dir /output/sweep/context-length \
            --candidates "$CONTEXT_LENGTHS" \
            --input-tokens "$SWEEP_INPUT_TOKENS" \
            --output-tokens "$SWEEP_OUTPUT_TOKENS" \
            --concurrency "$SWEEP_CONCURRENCY" \
            --num-prompts "$CONTEXT_NUM_PROMPTS" \
            --ttft-sla-ms "$SWEEP_TTFT_SLA_MS" \
            --tpot-sla-ms "$SWEEP_TPOT_SLA_MS" \
            --server-timeout "$SWEEP_SERVER_READY_TIMEOUT" \
            --apply
          bash ./run_full_sweep.sh --stages concurrency,scheduler \
            --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
        else
          bash ./run_full_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
        fi
        ;;
      parallel-layout)
        bash ./run_parallel_layout_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
        python3 ./recommend_parallel_layout.py
        ;;
      concurrency)
        bash ./run_concurrency_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
        python3 ./recommend_concurrency.py
        ;;
      scheduler)
        bash ./run_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
        python3 ./recommend.py
        ;;
    esac
  '
SWEEP_RC=$?
set -e

python3 - "$RESULT_ROOT/sweep-metadata.json" "$SWEEP_RC" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
 "generated_at": datetime.now(timezone.utc).isoformat(),
 "hardware": os.environ["HARDWARE"], "model": os.environ["MODEL"],
 "stage": os.environ["SWEEP_STAGE"], "input_tokens": int(os.environ["INPUT_TOKENS"]),
 "output_tokens": int(os.environ["OUTPUT_TOKENS"]), "concurrency": int(os.environ["CONCURRENCY"]),
 "max_model_len": os.environ.get("MAX_MODEL_LEN") or None,
 "context_sweep_enabled": os.environ.get("ENABLE_CONTEXT_SWEEP") == "true",
 "context_lengths": os.environ.get("CONTEXT_LENGTHS") if os.environ.get("ENABLE_CONTEXT_SWEEP") == "true" else None,
 "sweep_rc": int(sys.argv[2])}, indent=2) + "\n")
PY
exit "$SWEEP_RC"
