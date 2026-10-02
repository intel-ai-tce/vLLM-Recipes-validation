#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$ROOT/config/settings.env"

RUN_DATE=$(date -u +%F)
RUN_ID=${VALIDATION_RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)}
HISTORY_DIR="$ROOT/history/$RUN_DATE/$RUN_ID"

mkdir -p "$WORK_ROOT" "$RESULT_ROOT" "$VALIDATED_ROOT" "$HISTORY_DIR" "$HF_HOME"

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not available to the GitHub runner user." >&2
  echo "Verify Docker is installed and the runner account can access the daemon." >&2
  exit 2
fi

echo "==> Pulling official vLLM CPU image: $VLLM_IMAGE"
docker pull "$VLLM_IMAGE"

VLLM_DIR="$WORK_ROOT/vllm"
if [[ ! -d "$VLLM_DIR/.git" ]]; then
  git clone "$VLLM_REPO" "$VLLM_DIR"
fi
git -C "$VLLM_DIR" fetch --tags origin
git -C "$VLLM_DIR" checkout --detach "$VLLM_REF" || {
  git -C "$VLLM_DIR" fetch origin "$VLLM_REF"
  git -C "$VLLM_DIR" checkout --detach FETCH_HEAD
}
VLLM_SHA=$(git -C "$VLLM_DIR" rev-parse HEAD)

if [[ ! -f "$VLLM_DIR/tools/recipes/serve_with_recipe.sh" ]]; then
  echo "ERROR: $VLLM_DIR/tools/recipes/serve_with_recipe.sh not found" >&2
  exit 2
fi

rm -rf "$RESULT_ROOT"
mkdir -p "$RESULT_ROOT"

"$ROOT/scripts/collect_system_info.sh" "$RESULT_ROOT/system-info.txt"
docker image inspect "$VLLM_IMAGE" > "$RESULT_ROOT/docker-image.json"

python3 "$ROOT/scripts/discover_xeon6_models.py" \
  --base-url "$RECIPES_BASE_URL" \
  --output "$RESULT_ROOT/discovery.json"

ONLY_ARGS=()
if [[ -n "$TEST_MODEL" ]]; then
  echo "==> Manual single-model validation: $TEST_MODEL"
  ONLY_ARGS+=(--only "$TEST_MODEL")
fi

# Continue through all models even when individual models fail. Each model uses
# one official CPU-image container. serve_with_recipe.sh generates the config
# and starts vLLM; vllm bench serve is then executed with docker exec in that
# same running container instance.
set +e
python3 "$ROOT/scripts/run_all.py" \
  --discovery "$RESULT_ROOT/discovery.json" \
  --vllm-dir "$VLLM_DIR" \
  --image "$VLLM_IMAGE" \
  --recipes-base-url "$RECIPES_BASE_URL" \
  --hf-home "$HF_HOME" \
  --work-root "$WORK_ROOT/models" \
  --result-root "$RESULT_ROOT/models" \
  --hardware "$HARDWARE" \
  --input-len "$BENCH_INPUT_LEN" \
  --output-len "$BENCH_OUTPUT_LEN" \
  --num-prompts "$BENCH_NUM_PROMPTS" \
  --max-concurrency "$BENCH_MAX_CONCURRENCY" \
  --request-rate "$BENCH_REQUEST_RATE" \
  --server-timeout "$SERVER_START_TIMEOUT_SEC" \
  --bench-timeout "$BENCH_TIMEOUT_SEC" \
  "${ONLY_ARGS[@]}"
RUN_RC=$?
set -e

# Add system info to each model bundle for provenance.
for d in "$RESULT_ROOT"/models/*; do
  [[ -d "$d" ]] || continue
  cp "$RESULT_ROOT/system-info.txt" "$d/system-info.txt"
done

python3 "$ROOT/scripts/promote_and_report.py" \
  --results "$RESULT_ROOT/models" \
  --validated "$VALIDATED_ROOT/xeon6" \
  --history "$ROOT/history" \
  --run-id "$RUN_ID" \
  --run-date "$RUN_DATE" \
  --vllm-ref "$VLLM_REF" \
  --vllm-sha "$VLLM_SHA"

cp "$RESULT_ROOT/discovery.json" "$HISTORY_DIR/discovery.json"
cp "$RESULT_ROOT/system-info.txt" "$HISTORY_DIR/system-info.txt"
cp "$RESULT_ROOT/docker-image.json" "$HISTORY_DIR/docker-image.json"
cp "$RESULT_ROOT/models/weekly-summary.json" "$RESULT_ROOT/weekly-summary.json"
cp "$RESULT_ROOT/models/weekly-summary.html" "$RESULT_ROOT/weekly-summary.html"

python3 - "$RESULT_ROOT/run-metadata.json" "$RUN_DATE" "$RUN_ID" <<'PY'
import json
import sys
from pathlib import Path

out, run_date, run_id = sys.argv[1:]
Path(out).write_text(
    json.dumps(
        {
            "run_date": run_date,
            "run_id": run_id,
            "history_path": f"history/{run_date}/{run_id}",
        },
        indent=2,
    )
    + "\n"
)
PY

exit "$RUN_RC"
