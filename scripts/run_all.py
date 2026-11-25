#!/usr/bin/env python3
"""Sequentially run all discovered Xeon 6 model validations."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", required=True)
    ap.add_argument(
        "--vllm-dir",
        help="Optional checkout supplying tools/recipes; default: image-bundled tools",
    )
    ap.add_argument("--image", required=True)
    ap.add_argument("--recipes-base-url", default="https://recipes.vllm.ai")
    ap.add_argument("--hf-home", required=True)
    ap.add_argument("--work-root", required=True)
    ap.add_argument("--result-root", required=True)
    ap.add_argument("--hardware", default="xeon6")
    ap.add_argument("--input-len", type=int, default=128)
    ap.add_argument("--output-len", type=int, default=128)
    ap.add_argument("--num-prompts", type=int, default=5)
    ap.add_argument("--benchmark-runs", type=int, default=1)
    ap.add_argument("--num-warmups", type=int, default=0)
    ap.add_argument("--max-concurrency", type=int, default=1)
    ap.add_argument("--request-rate", default="inf")
    ap.add_argument("--server-timeout", type=int, default=3600)
    ap.add_argument("--bench-timeout", type=int, default=1800)
    ap.add_argument("--only", action="append", help="Test only exact model ID; repeatable")
    ap.add_argument("--skip", action="append", default=[],
                    help="Exact model ID to mark SKIP without starting a container")
    args = ap.parse_args()
    if args.benchmark_runs < 1:
        ap.error("--benchmark-runs must be positive")

    discovery = json.loads(Path(args.discovery).read_text())
    models = discovery["models"]
    if args.only:
        wanted = set(args.only)
        models = [m for m in models if m["hf_id"] in wanted]
        missing = wanted - {m["hf_id"] for m in models}
        if missing:
            print(
                "Requested model(s) not found in Xeon6 discovery: "
                + ", ".join(sorted(missing)),
                file=sys.stderr,
            )
            return 2

    runner = Path(__file__).with_name("run_one_model.py")
    skipped_ids = set(args.skip)
    skipped_ids.update(os.environ.get("SKIP_MODELS", "").replace(",", " ").split())
    failures = []
    skipped = []
    for i, item in enumerate(models, start=1):
        model = item["hf_id"]
        print(f"\n===== [{i}/{len(models)}] {model} =====", flush=True)
        if model in skipped_ids:
            reason = "Explicitly excluded from this validation run"
            destination = Path(args.result_root) / model.replace("/", "__")
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "result.json").write_text(json.dumps({
                "model": model,
                "hardware": args.hardware,
                "recipe_url": item.get("recipe_url"),
                "status": "SKIP",
                "skip_reason": reason,
                "stages": {"preflight": "SKIP"},
                "metrics": {},
            }, indent=2) + "\n")
            skipped.append(model)
            print(f"SKIP: {model} ({reason})", flush=True)
            continue
        cmd = [
            sys.executable,
            str(runner),
            "--model",
            model,
            "--recipe-url",
            item["recipe_url"],
            "--image",
            args.image,
            "--recipes-base-url",
            args.recipes_base_url,
            "--hf-home",
            args.hf_home,
            "--work-root",
            args.work_root,
            "--result-root",
            args.result_root,
            "--hardware",
            args.hardware,
            "--input-len",
            str(args.input_len),
            "--output-len",
            str(args.output_len),
            "--num-prompts",
            str(args.num_prompts),
            "--benchmark-runs",
            str(args.benchmark_runs),
            "--num-warmups",
            str(args.num_warmups),
            "--max-concurrency",
            str(args.max_concurrency),
            "--request-rate",
            str(args.request_rate),
            "--server-timeout",
            str(args.server_timeout),
            "--bench-timeout",
            str(args.bench_timeout),
        ]
        if args.vllm_dir:
            cmd.extend(["--vllm-dir", args.vllm_dir])
        rc = subprocess.run(cmd, env=os.environ.copy()).returncode
        result_path = Path(args.result_root) / model.replace("/", "__") / "result.json"
        try:
            model_result = json.loads(result_path.read_text())
        except (OSError, ValueError):
            model_result = {}
        if model_result.get("status") == "SKIP":
            skipped.append(model)
            print(
                f"SKIP: {model} ({model_result.get('skip_reason', 'unsupported')})",
                flush=True,
            )
        elif rc or model_result.get("status") != "PASS":
            failures.append(model)
            print(f"FAIL: {model}", file=sys.stderr)
        else:
            print(f"PASS: {model}")

    summary = {
        "tested": len(models),
        "attempted": len(models) - len(skipped),
        "passed": len(models) - len(failures) - len(skipped),
        "failed": len(failures),
        "skipped": len(skipped),
        "failed_models": failures,
        "skipped_models": skipped,
    }
    Path(args.result_root).mkdir(parents=True, exist_ok=True)
    (Path(args.result_root) / "run_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))

    # Do not stop later model tests because one model fails. Return non-zero only
    # after the complete catalog has been attempted.
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
