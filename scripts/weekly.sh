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

# A fresh checkout fetches the requested remote ref on every run, even on a
# persistent runner or after switching forks. Only tools/recipes is mounted.
TOOLS_ARGS=()
TOOLS_DIR=""
TOOLS_SHA=""
if [[ -n "$RECIPE_TOOLS_REPO" ]]; then
  TOOLS_DIR=$(mktemp -d "$WORK_ROOT/recipe-tools.XXXXXX")
  trap 'rm -rf "$TOOLS_DIR"' EXIT
  git -C "$TOOLS_DIR" init
  git -C "$TOOLS_DIR" remote add origin "$RECIPE_TOOLS_REPO"
  git -C "$TOOLS_DIR" fetch --depth 1 origin "$RECIPE_TOOLS_REF"
  git -C "$TOOLS_DIR" checkout --detach FETCH_HEAD
  TOOLS_SHA=$(git -C "$TOOLS_DIR" rev-parse HEAD)
  if [[ ! -f "$TOOLS_DIR/tools/recipes/serve_with_recipe.sh" ]]; then
    echo "ERROR: tools/recipes/serve_with_recipe.sh not found in requested source" >&2
    exit 2
  fi
  TOOLS_ARGS+=(--vllm-dir "$TOOLS_DIR")
else
  echo "==> Using recipe tools bundled in $VLLM_IMAGE"
  docker run --rm --entrypoint /usr/bin/test "$VLLM_IMAGE" \
    -x /vllm-workspace/tools/recipes/serve_with_recipe.sh
fi

rm -rf "$RESULT_ROOT"
mkdir -p "$RESULT_ROOT"

"$ROOT/scripts/collect_system_info.sh" "$RESULT_ROOT/system-info.txt"
docker image inspect "$VLLM_IMAGE" > "$RESULT_ROOT/docker-image.json"

python3 - "$RESULT_ROOT/recipe-tools.json" "$RECIPE_TOOLS_REPO" "$RECIPE_TOOLS_REF" "$TOOLS_SHA" "$VLLM_IMAGE" "$RESULT_ROOT/docker-image.json" <<'PYMETA'
import json
import sys
from pathlib import Path

out, repo, ref, sha, image, image_info = sys.argv[1:]
info = json.loads(Path(image_info).read_text())[0]
Path(out).write_text(json.dumps({
    "source": "github" if repo else "image",
    "repository": repo or None,
    "ref": ref if repo else None,
    "commit": sha or None,
    "image": image,
    "image_id": info.get("Id"),
    "image_digests": info.get("RepoDigests", []),
}, indent=2) + "\n")
PYMETA

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
  "${TOOLS_ARGS[@]}" \
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
  --recipe-tools-metadata "$RESULT_ROOT/recipe-tools.json"

cp "$RESULT_ROOT/discovery.json" "$HISTORY_DIR/discovery.json"
cp "$RESULT_ROOT/system-info.txt" "$HISTORY_DIR/system-info.txt"
cp "$RESULT_ROOT/docker-image.json" "$HISTORY_DIR/docker-image.json"
cp "$RESULT_ROOT/recipe-tools.json" "$HISTORY_DIR/recipe-tools.json"
cp "$RESULT_ROOT/models/weekly-summary.json" "$RESULT_ROOT/weekly-summary.json"
cp "$RESULT_ROOT/models/weekly-summary.html" "$RESULT_ROOT/weekly-summary.html"

python3 - "$RESULT_ROOT/run-metadata.json" "$RUN_DATE" "$RUN_ID" "$RESULT_ROOT/recipe-tools.json" <<'PY'
import json
import sys
from pathlib import Path

out, run_date, run_id, tools_metadata = sys.argv[1:]
Path(out).write_text(
    json.dumps(
        {
            "run_date": run_date,
            "run_id": run_id,
            "history_path": f"history/{run_date}/{run_id}",
            "recipe_tools": json.loads(Path(tools_metadata).read_text()),
        },
        indent=2,
    )
    + "\n"
)
PY

exit "$RUN_RC"
