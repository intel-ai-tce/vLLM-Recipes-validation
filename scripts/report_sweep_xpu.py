#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Report Intel XPU recipe sweep recommendations and every measured candidate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


STAGES = (
    ("Parallel layout", "parallel-layout-recommendation.json"),
    ("Concurrency", "concurrency-recommendation.json"),
    ("Scheduler", "recommendation.json"),
)


def read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def safe(value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return (str(value).replace("|", "\\|").replace("\n", " ")
            .replace("\r", " ").replace("`", "'"))


def fmt(value, digits=1):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return "-"
    return f"{value:.{digits}f}"


def setting(value):
    return "vLLM default" if value is None else safe(value)


def table(lines, headers, rows):
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(safe(v) for v in row) + " |")
    if not rows:
        lines.append("| " + " | ".join(["No data"] + ["-"] * (len(headers) - 1)) + " |")


def system_fields(text):
    result = {}
    if "===== lscpu =====" in text:
        section = text.split("===== lscpu =====", 1)[1].split("=====", 1)[0]
        for line in section.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip()
    for line in text.splitlines():
        if line.startswith("hostname="):
            result["hostname"] = line.partition("=")[2]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    root = args.results
    sweep = root / "sweep"
    info = read_json(root / "sweep-metadata.json") or {}
    validation = read_json(root / "recipe-validation-status.json") or {}
    adjustment = validation.get("parallelism_adjustment") or {}
    tools = read_json(root / "recipe-tools.json") or {}
    devices = read_json(root / "xpu-devices.json") or []
    if not isinstance(devices, list):
        devices = []
    system_path = root / "system-info.txt"
    system = system_fields(system_path.read_text(errors="replace") if system_path.is_file() else "")
    reports = [(label, read_json(sweep / name)) for label, name in STAGES]
    parallel, concurrency, scheduler = (r if isinstance(r, dict) else None for _, r in reports)

    lines = [
        "# Intel XPU Recipe Sweep", "",
        f"**Run:** `{safe(os.getenv('GITHUB_RUN_NUMBER'))}`  ",
        f"**Generated:** `{safe(info.get('generated_at'))}`  ",
        f"**Model:** `{safe(info.get('model'))}`  ",
        f"**Recipes hardware:** `{safe(info.get('hardware'))}`  ",
        f"**Status:** `{'PASS' if info.get('sweep_rc') == 0 else 'FAILED / INCOMPLETE'}`  ",
        f"**Recipe tools source:** `{safe(tools.get('source'))}`  ",
        f"**Recipe tools repository:** `{safe(tools.get('repository'))}`  ",
        f"**Recipe tools ref / commit:** `{safe(tools.get('ref'))}` / `{safe(tools.get('commit'))}`", "",
        "## Run configuration", "",
    ]
    table(lines, ["Setting", "Value"], [
        ("GitHub runner", os.getenv("RUNNER_NAME")),
        ("Runner label", os.getenv("RUNNER_GPU_LABEL")),
        ("Runner tag", os.getenv("RUNNER_TAG")),
        ("Docker image", tools.get("image") or os.getenv("VLLM_IMAGE")),
        ("Recipes URL", os.getenv("RECIPES_BASE_URL")),
        ("Sweep stage", info.get("stage")),
        ("Input tokens", info.get("input_tokens")),
        ("Output tokens", info.get("output_tokens")),
        ("Seed concurrency", info.get("concurrency")),
        ("TTFT SLA", f"{os.getenv('TTFT_SLA_MS')} ms" if os.getenv("TTFT_SLA_MS") else None),
        ("TPOT SLA", f"{os.getenv('TPOT_SLA_MS')} ms" if os.getenv("TPOT_SLA_MS") else None),
        ("Fixed max-model-len", info.get("max_model_len")),
        ("Context sweep enabled", info.get("context_sweep_enabled", False)),
        ("Context candidates", info.get("context_lengths")),
    ])
    lines += ["", "## Recipe parallelism adjustment", ""]
    if adjustment:
        original = adjustment.get("recipe") or {}
        effective = adjustment.get("effective") or {}
        table(lines, ["Field", "Value"], [
            ("Downgraded", adjustment.get("changed")),
            ("Original TP / DP", f"{original.get('tp')} / {original.get('dp')}"),
            ("Effective TP / DP", f"{effective.get('tp')} / {effective.get('dp')}"),
            ("Visible B70 GPUs", adjustment.get("visible_gpus")),
            ("Memory fit", adjustment.get("fit_status")),
            ("Estimated VRAM per GPU", adjustment.get("estimated_per_gpu_bytes")),
            ("Usable VRAM per GPU", adjustment.get("usable_per_gpu_bytes")),
        ])
        lines.append("Memory fit is estimated; the benchmark verifies startup and inference.")
    else:
        lines.append(
            f"No adjustment recorded; conversion status: {safe(validation.get('status'))}."
        )
    lines += ["", "## System", ""]
    table(lines, ["Setting", "Value"], [
        ("Host", system.get("hostname")),
        ("CPU model", system.get("Model name")),
        ("Sockets", system.get("Socket(s)")),
        ("Logical CPUs", system.get("CPU(s)")),
        ("NUMA nodes", system.get("NUMA node(s)")),
        ("Process-visible XPU count", len(devices) if devices else None),
    ])
    if devices:
        lines += ["", "### Visible XPU devices", ""]
        table(lines, ["Index", "Name"], [(d.get("index"), d.get("name")) for d in devices if isinstance(d, dict)])

    pr = (parallel or {}).get("recommended") or {}
    cr = (concurrency or {}).get("recommended") or {}
    sr = (scheduler or {}).get("recommended") or {}
    # A missing scheduler override means the actual vLLM default is retained.
    effective_tp = sr.get("tensor_parallel_size", pr.get("tensor_parallel_size"))
    effective_dp = sr.get("data_parallel_size", pr.get("data_parallel_size"))
    lines += ["", "## Sweep Recommendation", ""]
    table(lines, ["Parameter", "Recommended"], [
        ("tensor-parallel-size", effective_tp),
        ("data-parallel-size", effective_dp),
        ("max concurrency", cr.get("max_concurrency")),
        ("max-num-seqs", setting(sr.get("max_num_seqs")) if scheduler else "-"),
        ("max-num-batched-tokens", setting(sr.get("max_num_batched_tokens")) if scheduler else "-"),
    ])

    lines += ["", "## Sweep Results", ""]
    stage_rows = []
    for label, rec in reports:
        if not isinstance(rec, dict):
            continue
        measured = rec.get("measured") or rec.get("baseline_measured") or {}
        stage_rows.append((label, str(rec.get("status", "unknown")).replace("_", " "),
                           fmt(measured.get("mean_output_throughput")),
                           fmt(measured.get("mean_request_goodput"), 3),
                           fmt(measured.get("mean_p99_ttft_ms"), 0),
                           fmt(measured.get("mean_p99_tpot_ms"))))
    table(lines, ["Stage", "Status", "Output tok/s", "Request goodput", "P99 TTFT ms", "P99 TPOT ms"], stage_rows)
    if scheduler and scheduler.get("status") == "fallback_concurrency_baseline":
        lines += ["", "Scheduler fallback reuses the concurrency-stage baseline; the scheduler row is not a new measurement."]

    lines += ["", "## All Sweep Candidates", ""]
    candidate_rows = []
    for label, rec in reports:
        if not isinstance(rec, dict):
            continue
        candidates = rec.get("candidates") or []
        if not isinstance(candidates, list):
            continue
        for item in candidates:
            if not isinstance(item, dict):
                continue
            eligible = item.get("sla_eligible")
            candidate_rows.append((
                label,
                item.get("tensor_parallel_size"),
                item.get("data_parallel_size"),
                item.get("max_concurrency"),
                setting(item.get("max_num_seqs")) if label != "Concurrency" else "-",
                setting(item.get("max_num_batched_tokens")) if label != "Concurrency" else "-",
                item.get("run_count"),
                fmt(item.get("mean_output_throughput")),
                fmt(item.get("mean_request_goodput"), 3),
                fmt(item.get("mean_p99_ttft_ms"), 0),
                fmt(item.get("mean_p99_tpot_ms")),
                "yes" if eligible is True else "no" if eligible is False else "unknown",
            ))
    table(lines, ["Stage", "TP", "DP", "Max conc.", "Max seqs", "Max batched tokens", "Runs", "Output tok/s", "Request goodput", "P99 TTFT ms", "P99 TPOT ms", "SLA eligible"], candidate_rows)
    lines += ["", "*Candidate performance columns show means across repeated runs; eligibility is taken from the Recipes recommender (which also considers compliance).*", ""]

    context = read_json(sweep / "context-length" / "context-length-results.json")
    lines += ["## Optional context-length sweep", ""]
    if isinstance(context, dict):
        lines.append(f"**Selected max-model-len:** `{safe(context.get('selected_max_model_len'))}`  ")
        rows = []
        for item in context.get("results", []):
            if isinstance(item, dict):
                rows.append((item.get("max_model_len"), item.get("status"), item.get("feasible"),
                             fmt(item.get("output_throughput")), fmt(item.get("p99_ttft_ms"), 0),
                             fmt(item.get("p99_tpot_ms")), item.get("error")))
        table(lines, ["max-model-len", "Status", "Feasible", "Output tok/s", "P99 TTFT ms", "P99 TPOT ms", "Error"], rows)
    else:
        lines.append("Disabled." if not info.get("context_sweep_enabled") else "Enabled, but no results were generated; inspect logs.")

    skipped = read_json(sweep / "parallel_layout_skips.json")
    lines += ["", "## Excluded parallel layouts", ""]
    if isinstance(skipped, list) and skipped:
        table(lines, ["Candidate", "Policy", "Reason"], [
            (item.get("_benchmark_name"), item.get("policy"), item.get("reason"))
            for item in skipped if isinstance(item, dict)
        ])
    else:
        lines.append("None recorded.")

    lines += ["", "## Sweep HTML Report", ""]
    if (sweep / "sweep-report.html").is_file():
        lines.append("`sweep-report.html` was generated and is included in the uploaded sweep artifact.")
    else:
        lines.append("No HTML report was found; inspect recommendation JSON and raw results.")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if repo and run_id:
        lines += ["", "## Sweep Report Artifact", "",
                  f"[Open this workflow run's uploaded artifacts](https://github.com/{repo}/actions/runs/{run_id}#artifacts)",
                  "", "Artifact file: `sweep/sweep-report.html` (when generated)."]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
