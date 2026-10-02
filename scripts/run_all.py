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
    ap.add_argument("--vllm-dir", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--recipes-base-url", default="https://recipes.vllm.ai")
    ap.add_argument("--hf-home", required=True)
    ap.add_argument("--work-root", required=True)
    ap.add_argument("--result-root", required=True)
    ap.add_argument("--hardware", default="xeon6")
    ap.add_argument("--input-len", type=int, default=128)
    ap.add_argument("--output-len", type=int, default=128)
    ap.add_argument("--num-prompts", type=int, default=5)
    ap.add_argument("--max-concurrency", type=int, default=1)
    ap.add_argument("--request-rate", default="inf")
    ap.add_argument("--server-timeout", type=int, default=3600)
    ap.add_argument("--bench-timeout", type=int, default=1800)
    ap.add_argument("--only", action="append", help="Test only exact model ID; repeatable")
    args = ap.parse_args()

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
    failures = []
    for i, item in enumerate(models, start=1):
        model = item["hf_id"]
        print(f"\n===== [{i}/{len(models)}] {model} =====", flush=True)
        cmd = [
            sys.executable,
            str(runner),
            "--model",
            model,
            "--recipe-url",
            item["recipe_url"],
            "--vllm-dir",
            args.vllm_dir,
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
            "--max-concurrency",
            str(args.max_concurrency),
            "--request-rate",
            str(args.request_rate),
            "--server-timeout",
            str(args.server_timeout),
            "--bench-timeout",
            str(args.bench_timeout),
        ]
        rc = subprocess.run(cmd, env=os.environ.copy()).returncode
        if rc:
            failures.append(model)
            print(f"FAIL: {model}", file=sys.stderr)
        else:
            print(f"PASS: {model}")

    summary = {
        "tested": len(models),
        "passed": len(models) - len(failures),
        "failed": len(failures),
        "failed_models": failures,
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
