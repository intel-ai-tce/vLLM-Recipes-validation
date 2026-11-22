#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate an XPU sweep GitHub step summary from existing recipe artifacts."""

import argparse
import json
from pathlib import Path


def read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def safe(value):
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.results
    sweep = root / "sweep"
    info = read_json(root / "sweep-metadata.json") or {}
    tools = read_json(root / "recipe-tools.json") or {}
    devices = read_json(root / "xpu-devices.json") or []

    lines = [
        "# Intel XPU Recipe Sweep", "",
        f"**Recipes hardware:** `{safe(info.get('hardware'))}`  ",
        f"**Model:** `{safe(info.get('model'))}`  ",
        f"**Stage:** `{safe(info.get('stage'))}`  ",
        f"**Status:** `{'PASS' if info.get('sweep_rc') == 0 else 'FAILED / INCOMPLETE'}`  ",
        f"**Image:** `{safe(tools.get('image'))}`  ",
        f"**Recipes tools:** `{safe(tools.get('repository'))}` @ `{safe(tools.get('commit') or tools.get('ref'))}`  ",
        f"**Visible XPU count:** `{len(devices)}`", "",
        "## Workload", "",
        "| Input tokens | Output tokens | Seed concurrency | Fixed max-model-len |",
        "| ---: | ---: | ---: | ---: |",
        f"| {safe(info.get('input_tokens'))} | {safe(info.get('output_tokens'))} | "
        f"{safe(info.get('concurrency'))} | {safe(info.get('max_model_len'))} |", "",
        "## Sweep recommendations", "",
        "| Stage | Status | TP | DP | Concurrency | max-num-seqs | max-num-batched-tokens |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    count = 0
    for title, filename in (
        ("Parallel layout", "parallel-layout-recommendation.json"),
        ("Concurrency", "concurrency-recommendation.json"),
        ("Scheduler", "recommendation.json"),
    ):
        rec = read_json(sweep / filename)
        if not isinstance(rec, dict):
            continue
        candidate = rec.get("recommended") or {}
        if not isinstance(candidate, dict):
            candidate = {}
        count += 1
        lines.append(
            f"| {title} | {safe(rec.get('status'))} | "
            f"{safe(candidate.get('tensor_parallel_size'))} | "
            f"{safe(candidate.get('data_parallel_size'))} | "
            f"{safe(candidate.get('max_concurrency'))} | "
            f"{safe(candidate.get('max_num_seqs'))} | "
            f"{safe(candidate.get('max_num_batched_tokens'))} |"
        )
    if count == 0:
        lines.append("| No recommendations produced | - | - | - | - | - | - |")

    context = read_json(sweep / "context-length" / "context-length-results.json")
    lines += ["", "## Optional context-length sweep", ""]
    if isinstance(context, dict):
        selected = context.get("selected_max_model_len")
        lines.append(f"**Selected max-model-len:** `{safe(selected)}`")
        lines += [
            "",
            "| max-model-len | Status | Feasible | P99 TTFT ms | P99 TPOT ms | Output tok/s |",
            "| ---: | --- | --- | ---: | ---: | ---: |",
        ]
        for candidate in context.get("results", []):
            lines.append(
                f"| {safe(candidate.get('max_model_len'))} | "
                f"{safe(candidate.get('status'))} | "
                f"{safe(candidate.get('feasible'))} | "
                f"{safe(candidate.get('p99_ttft_ms'))} | "
                f"{safe(candidate.get('p99_tpot_ms'))} | "
                f"{safe(candidate.get('output_throughput'))} |"
            )
    else:
        lines.append("Disabled or no context-length results generated.")

    skipped = read_json(sweep / "parallel_layout_skips.json")
    if isinstance(skipped, list):
        lines += ["", f"**Candidate layouts excluded by policy:** {len(skipped)}"]
    lines += ["", "Full benchmark results, configs, logs and HTML report (if generated) are in the workflow artifact."]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
