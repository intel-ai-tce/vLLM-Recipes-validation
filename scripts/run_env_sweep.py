#!/usr/bin/env python3
"""Opt-in final Xeon Recipes sweep of VLLM_CPU_NUM_OF_RESERVED_CPU.

Run inside the same vLLM image that executed the Recipes sweeps.
The server is restarted for each candidate; scheduler and workload stay fixed.
"""
import argparse
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request

import yaml

KEY = "VLLM_CPU_NUM_OF_RESERVED_CPU"


def candidates(raw, tp):
    values = [0, tp, 2 * tp] if not raw.strip() else [int(v.strip()) for v in raw.split(",")]
    if not values or any(v < 0 for v in values) or len(set(values)) != len(values):
        raise ValueError("Candidates must be unique non-negative integers")
    if len(values) > 16:
        raise ValueError("A maximum of 16 environment candidates is allowed")
    return values


def metric(record, *names):
    for name in names:
        value = record.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def wait_ready(process, url, timeout):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError(f"vLLM exited during startup with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
    raise TimeoutError(f"vLLM did not become ready in {timeout} seconds")


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def html_section(report):
    rows = []
    for item in report["candidates"]:
        vals = [item.get("value"), item.get("status"), item.get("output_throughput"),
                item.get("p99_ttft_ms"), item.get("p99_tpot_ms"), item.get("error", "")]
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(x if x is not None else '-'))}</td>" for x in vals) + "</tr>")
    return ("<section><h2>Environment variable sweep (final stage)</h2>"
            f"<p>{KEY}: selected {html.escape(str(report.get('recommended')))}; "
            f"TP={report['tp']}, concurrency={report['concurrency']}; "
            f"scheduler source={html.escape(str(report.get('scheduler_source', 'unknown')))}; "
            f"CPU binding={html.escape(report.get('cpu_binding', 'unknown'))}; "
            "throughput differences within 3% treated as equivalent.</p>"
            "<table border='1'><tr><th>Reserved CPUs</th><th>Status</th>"
            "<th>Output tok/s</th><th>P99 TTFT ms</th><th>P99 TPOT ms</th><th>Error</th></tr>"
            + "".join(rows) + "</table></section>")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sweep-dir", type=Path, required=True)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--values", default="")
    p.add_argument("--model", required=True)
    p.add_argument("--input-tokens", type=int, required=True)
    p.add_argument("--output-tokens", type=int, required=True)
    p.add_argument("--seed-concurrency", type=int, required=True)
    p.add_argument("--ttft-sla-ms", type=float, required=True)
    p.add_argument("--tpot-sla-ms", type=float, required=True)
    p.add_argument("--ready-timeout", type=int, default=5400)
    args = p.parse_args()
    sweep = args.sweep_dir.resolve()
    config_path = sweep / "recommended-config.yml"
    if not config_path.is_file():
        raise SystemExit("No scheduler recommended-config.yml; cannot run final environment sweep")
    config = yaml.safe_load(config_path.read_text())
    if not isinstance(config, dict):
        raise SystemExit("Invalid scheduler recommendation YAML")
    tp = int(config.get("tensor-parallel-size", config.get("tensor_parallel_size", 1)))
    if tp < 1:
        raise SystemExit("Invalid recommended tensor parallel size")
    recommendation = json.loads((sweep / "concurrency-recommendation.json").read_text())
    concurrency = int((recommendation.get("recommended") or {}).get("max_concurrency") or args.seed_concurrency)
    if concurrency < 1:
        raise SystemExit("Invalid selected concurrency")
    values = candidates(args.values, tp)
    request_model = config.get("served-model-name") or config.get("model") or args.model
    if isinstance(request_model, list):
        request_model = request_model[0]
    tokenizer = config.get("model") or args.model
    output_dir = sweep / "env-sweep"
    output_dir.mkdir(exist_ok=True)
    scheduler_path = sweep / "recommendation.json"
    scheduler_report = (json.loads(scheduler_path.read_text())
                        if scheduler_path.is_file() else {})
    scheduler_source = scheduler_report.get("status", "unknown")
    results = {"variable": KEY, "tp": tp, "concurrency": concurrency,
               "cpu_binding": "auto", "ipc_isolation": "fresh-docker-container",
               "scheduler_source": scheduler_source,
               "values": values, "recommended": None, "candidates": [],
               "ttft_sla_ms": args.ttft_sla_ms, "tpot_sla_ms": args.tpot_sla_ms,
               "variance_percent": 3.0}
    report_file = sweep / "env-sweep-results.json"
    # A fixed request count makes values comparable; mimic Recipes' workload policy.
    num_prompts = min(1000, max(100, concurrency * 10))
    for value in values:
        item = {"value": value, "status": "failed"}
        results["candidates"].append(item)
        server_log = output_dir / f"server-{value}.log"
        bench_log = output_dir / f"benchmark-{value}.log"
        bench_json = output_dir / f"benchmark-{value}.json"
        server = None
        try:
            # The generated recipe env.sh must be loaded before overriding the candidate.
            # Use the same path for CLI and worker subprocesses.
            cmd = (f"source {__import__('shlex').quote(str(args.env_file.resolve()))}; "
                   "export VLLM_CPU_OMP_THREADS_BIND=auto; "
                   f"export {KEY}={value}; "
                   "cd /vllm-workspace 2>/dev/null || true; "
                   f"exec vllm serve --config {__import__('shlex').quote(str(config_path))}")
            with server_log.open("w") as log:
                server = subprocess.Popen(["bash", "-lc", cmd], stdout=log,
                                          stderr=subprocess.STDOUT, start_new_session=True)
            wait_ready(server, "http://127.0.0.1:8000/health", args.ready_timeout)
            benchmark = ["vllm", "bench", "serve", "--backend", "vllm", "--model", str(request_model),
                         "--tokenizer", str(tokenizer), "--dataset-name", "random",
                         "--random-input-len", str(args.input_tokens),
                         "--random-output-len", str(args.output_tokens), "--request-rate", "inf",
                         "--num-prompts", str(num_prompts), "--max-concurrency", str(concurrency),
                         "--ignore-eos", "--metric-percentiles", "99", "--save-result",
                         "--result-dir", str(output_dir), "--result-filename", bench_json.name]
            with bench_log.open("w") as log:
                rc = subprocess.run(benchmark, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
            if rc != 0:
                raise RuntimeError(f"benchmark exited {rc}; see {bench_log.name}")
            raw = json.loads(bench_json.read_text())
            item.update(output_throughput=metric(raw, "output_throughput"),
                        p99_ttft_ms=metric(raw, "p99_ttft_ms"),
                        p99_tpot_ms=metric(raw, "p99_tpot_ms"),
                        completed=metric(raw, "completed"),
                        errors=metric(raw, "failed"))
            if None in (item["output_throughput"], item["p99_ttft_ms"], item["p99_tpot_ms"]):
                raise RuntimeError("Required benchmark metrics missing")
            item["status"] = ("pass" if item["p99_ttft_ms"] <= args.ttft_sla_ms
                              and item["p99_tpot_ms"] <= args.tpot_sla_ms
                              and item["completed"] == num_prompts
                              and (item["errors"] is None or item["errors"] == 0)
                              else "sla_fail")
        except Exception as exc:
            item["error"] = str(exc)
        finally:
            if server is not None:
                stop(server)
            report_file.write_text(json.dumps(results, indent=2) + "\n")
    passing = [x for x in results["candidates"] if x["status"] == "pass"]
    if passing:
        peak = max(x["output_throughput"] for x in passing)
        equivalent = [x for x in passing if x["output_throughput"] >= peak * 0.97]
        # Prefer the smaller reservation if no significant throughput benefit.
        winner = min(equivalent, key=lambda x: x["value"])
        results["recommended"] = winner["value"]
        results["status"] = "pass"
    else:
        results["status"] = "no_sla_pass"
    report_file.write_text(json.dumps(results, indent=2) + "\n")
    page = sweep / "sweep-report.html"
    if page.is_file():
        content = page.read_text()
        section = html_section(results)
        content = re.sub(r"</body\s*>", lambda m: section + "\n" + m.group(), content, count=1, flags=re.I)
        if section not in content:
            content += section
        page.write_text(content)
    if not passing:
        raise SystemExit("No environment candidate satisfied both P99 SLAs; see env-sweep-results.json")


if __name__ == "__main__":
    main()
