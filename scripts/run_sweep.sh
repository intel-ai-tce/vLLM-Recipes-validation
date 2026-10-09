#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

: "${MODEL:?MODEL is required}"

SWEEP_STAGE=${SWEEP_STAGE:-all}
VLLM_IMAGE=${VLLM_IMAGE:-vllm/vllm-openai-cpu:latest-x86_64}
RECIPES_BASE_URL=${RECIPES_BASE_URL:-https://recipes.vllm.ai}
RECIPE_TOOLS_REPO=${RECIPE_TOOLS_REPO:-https://github.com/intel-ai-tce/vllm.git}
RECIPE_TOOLS_REF=${RECIPE_TOOLS_REF:-recipes-staged-sweep-workflow}
HARDWARE=${HARDWARE:-xeon6}
INPUT_TOKENS=${INPUT_TOKENS:-128}
OUTPUT_TOKENS=${OUTPUT_TOKENS:-128}
CONCURRENCY=${CONCURRENCY:-32}
TTFT_SLA_MS=${TTFT_SLA_MS:-3000}
TPOT_SLA_MS=${TPOT_SLA_MS:-100}
SWEEP_SERVER_READY_TIMEOUT=${SWEEP_SERVER_READY_TIMEOUT:-5400}
WORK_ROOT=${WORK_ROOT:-${GITHUB_WORKSPACE:-$ROOT}/work/sweep}
RESULT_ROOT=${RESULT_ROOT:-${GITHUB_WORKSPACE:-$ROOT}/results/sweep}
HF_HOME=${HF_HOME:-$HOME/.cache/huggingface}

case "$SWEEP_STAGE" in
  all|parallel-layout|concurrency|scheduler) ;;
  *)
    echo "ERROR: SWEEP_STAGE must be one of: all, parallel-layout, concurrency, scheduler" >&2
    exit 2
    ;;
esac

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not available to the GitHub runner user." >&2
  exit 2
fi

mkdir -p "$WORK_ROOT" "$HF_HOME"
rm -rf "$RESULT_ROOT"
mkdir -p "$RESULT_ROOT"

echo "==> Pulling vLLM CPU image: $VLLM_IMAGE"
docker pull "$VLLM_IMAGE"

echo "==> Fetching recipe sweep tools: $RECIPE_TOOLS_REPO @ $RECIPE_TOOLS_REF"
TOOLS_DIR=$(mktemp -d "$WORK_ROOT/recipe-tools.XXXXXX")
trap 'rm -rf "$TOOLS_DIR"' EXIT

git -C "$TOOLS_DIR" init -q
git -C "$TOOLS_DIR" remote add origin "$RECIPE_TOOLS_REPO"
git -C "$TOOLS_DIR" fetch --depth 1 origin "$RECIPE_TOOLS_REF"
git -C "$TOOLS_DIR" checkout -q --detach FETCH_HEAD
TOOLS_SHA=$(git -C "$TOOLS_DIR" rev-parse HEAD)

if [[ ! -f "$TOOLS_DIR/tools/recipes/recipe_json_to_vllm_config.py" ]]; then
  echo "ERROR: recipe_json_to_vllm_config.py was not found in requested recipe tools source" >&2
  exit 2
fi
if [[ ! -d "$TOOLS_DIR/tools/recipes/sweep" ]]; then
  echo "ERROR: tools/recipes/sweep was not found in requested recipe tools source" >&2
  exit 2
fi

"$ROOT/scripts/collect_system_info.sh" "$RESULT_ROOT/system-info.txt"
docker image inspect "$VLLM_IMAGE" > "$RESULT_ROOT/docker-image.json"

python3 - "$RESULT_ROOT/recipe-tools.json" \
  "$RECIPE_TOOLS_REPO" "$RECIPE_TOOLS_REF" "$TOOLS_SHA" \
  "$VLLM_IMAGE" "$RESULT_ROOT/docker-image.json" <<'PYMETA'
import json
import sys
from pathlib import Path

out, repo, ref, sha, image, image_info = sys.argv[1:]
info = json.loads(Path(image_info).read_text())[0]
Path(out).write_text(json.dumps({
    "source": "github",
    "repository": repo,
    "ref": ref,
    "commit": sha,
    "image": image,
    "image_id": info.get("Id"),
    "image_digests": info.get("RepoDigests", []),
}, indent=2) + "\n")
PYMETA

DOCKER_ENV=(
  -e "SWEEP_MODEL=$MODEL"
  -e "SWEEP_STAGE=$SWEEP_STAGE"
  -e "SWEEP_HARDWARE=$HARDWARE"
  -e "SWEEP_RECIPES_BASE_URL=$RECIPES_BASE_URL"
  -e "SWEEP_INPUT_TOKENS=$INPUT_TOKENS"
  -e "SWEEP_OUTPUT_TOKENS=$OUTPUT_TOKENS"
  -e "SWEEP_CONCURRENCY=$CONCURRENCY"
  -e "SWEEP_TTFT_SLA_MS=$TTFT_SLA_MS"
  -e "SWEEP_TPOT_SLA_MS=$TPOT_SLA_MS"
  -e "SWEEP_SERVER_READY_TIMEOUT=$SWEEP_SERVER_READY_TIMEOUT"
  -e "HF_HOME=/hf-cache"
  -e "HOME=/tmp/vllm-home"
  -e "HOST_UID=$(id -u)"
  -e "HOST_GID=$(id -g)"
)
if [[ -n "${HF_TOKEN:-}" ]]; then
  DOCKER_ENV+=(-e "HF_TOKEN=$HF_TOKEN")
fi

# Preserve common proxy settings when the self-hosted runner requires them.
for proxy_name in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  if [[ -n "${!proxy_name:-}" ]]; then
    DOCKER_ENV+=(-e "$proxy_name=${!proxy_name}")
  fi
done

echo "==> Running $SWEEP_STAGE sweep for $MODEL"
set +e
docker run --rm \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  --security-opt seccomp=unconfined \
  --cap-add SYS_NICE \
  --shm-size=4g \
  "${DOCKER_ENV[@]}" \
  -v "$TOOLS_DIR/tools/recipes:/recipes:ro" \
  -v "$RESULT_ROOT:/output" \
  -v "$HF_HOME:/hf-cache" \
  -w /output \
  "$VLLM_IMAGE" \
  -lc '
    set -euo pipefail

    mkdir -p "$HOME"

    # The container runs as the GitHub runner UID/GID, so bind-mounted sweep
    # output and the Hugging Face cache remain accessible to the host runner.

    python3 /recipes/recipe_json_to_vllm_config.py \
      --model "$SWEEP_MODEL" \
      --hardware "$SWEEP_HARDWARE" \
      --api-base "$SWEEP_RECIPES_BASE_URL" \
      --detect-hardware \
      --input-tokens "$SWEEP_INPUT_TOKENS" \
      --output-tokens "$SWEEP_OUTPUT_TOKENS" \
      --concurrency "$SWEEP_CONCURRENCY" \
      --ttft-sla-ms "$SWEEP_TTFT_SLA_MS" \
      --tpot-sla-ms "$SWEEP_TPOT_SLA_MS" \
      --config-out /output/config.yml \
      --env-out /output/env.sh \
      --sweep-stage "$SWEEP_STAGE" \
      --sweep-out-dir /output/sweep

    cd /output/sweep
    set +e
    case "$SWEEP_STAGE" in
      all)
        bash ./run_full_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT"
        SWEEP_RC=$?
        ;;
      parallel-layout)
        bash ./run_parallel_layout_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT" && python3 ./recommend_parallel_layout.py
        SWEEP_RC=$?
        ;;
      concurrency)
        bash ./run_concurrency_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT" && python3 ./recommend_concurrency.py
        SWEEP_RC=$?
        ;;
      scheduler)
        bash ./run_sweep.sh --server-ready-timeout "$SWEEP_SERVER_READY_TIMEOUT" && python3 ./recommend.py
        SWEEP_RC=$?
        ;;
    esac
    set -e

    # The report generator accepts partial stage data, but requires at least
    # one recommendation JSON. Avoid masking the real sweep failure with a
    # secondary "No recommendation files found" traceback.
    if [[ -f parallel-layout-recommendation.json ||
          -f concurrency-recommendation.json ||
          -f recommendation.json ]]; then
      python3 ./report.py \
        --title "vLLM Recipe Sweep - $SWEEP_MODEL" \
        --output /output/sweep/sweep-report.html || true
    else
      echo "Warning: no sweep recommendation was produced; skipping HTML report." >&2
    fi

    exit "$SWEEP_RC"
  '
SWEEP_RC=$?
set -e

python3 - "$RESULT_ROOT/sweep-metadata.json" "$SWEEP_RC" <<'PYMETA'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

out = Path(os.sys.argv[1])
sweep_rc = int(os.sys.argv[2])
out.write_text(json.dumps({
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "model": os.environ["MODEL"],
    "stage": os.environ.get("SWEEP_STAGE", "all"),
    "hardware": os.environ.get("HARDWARE", "xeon6"),
    "input_tokens": int(os.environ.get("INPUT_TOKENS", "128")),
    "output_tokens": int(os.environ.get("OUTPUT_TOKENS", "128")),
    "seed_concurrency": int(os.environ.get("CONCURRENCY", "32")),
    "ttft_sla_ms": float(os.environ.get("TTFT_SLA_MS", "3000")),
    "tpot_sla_ms": float(os.environ.get("TPOT_SLA_MS", "100")),
    "sweep_rc": sweep_rc,
}, indent=2) + "\n")
PYMETA

exit "$SWEEP_RC"
