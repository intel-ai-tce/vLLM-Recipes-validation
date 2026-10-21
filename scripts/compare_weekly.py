#!/usr/bin/env python3
"""Compare two Xeon validation summaries, producing a standalone Markdown report."""
import argparse
import json
import math
from pathlib import Path

METRICS = (
    ("output_throughput", "Output tok/s", 1),
    ("p99_ttft_ms", "P99 TTFT (ms)", -1),
    ("p99_tpot_ms", "P99 TPOT (ms)", -1),
)


def esc(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def model_map(data):
    return {item["model"]: item for item in data.get("models", []) if item.get("model")}


def run_link(label, url):
    return f"[{esc(label)}]({url})" if url.startswith("https://github.com/") else f"`{esc(label)}`"


def compare(previous, latest, previous_url="", latest_url=""):
    old = model_map(previous)
    new = model_map(latest)
    old_id = previous.get("run_id", "unknown")
    new_id = latest.get("run_id", "unknown")
    lines = [
        "# Xeon 6 — Weekly Recipe Validation Comparison", "",
        f"**Previous:** {run_link(old_id, previous_url)} ({esc(previous.get('run_date', '-'))})  ",
        f"**Latest:** {run_link(new_id, latest_url)} ({esc(latest.get('run_date', '-'))})  ",
        "", "## Overall result", "",
        "| Measure | Previous | Latest | Delta |", "| --- | ---: | ---: | ---: |",
    ]
    for field in ("tested", "passed", "failed"):
        a = int(previous.get(field, 0))
        b = int(latest.get(field, 0))
        lines.append(f"| {field.title()} | {a} | {b} | {b-a:+d} |")

    fixed, regressed, added, removed, other = [], [], [], [], []
    for model in sorted(old.keys() | new.keys()):
        if model not in old:
            added.append(model)
        elif model not in new:
            removed.append(model)
        else:
            a = old[model].get("status", "UNKNOWN")
            b = new[model].get("status", "UNKNOWN")
            if a == "PASS" and b != "PASS":
                regressed.append(model)
            elif a != "PASS" and b == "PASS":
                fixed.append(model)
            elif a != b:
                other.append(model)

    lines += [
        "", "## Status transitions", "",
        f"**Regressions:** {len(regressed)} | **Fixes:** {len(fixed)} | "
        f"**Newly tested:** {len(added)} | **No longer tested:** {len(removed)}", "",
        "| Model | Previous | Latest | Assessment |", "| --- | --- | --- | --- |",
    ]
    transitions = ([(m, "Regression") for m in regressed] +
                   [(m, "Fixed") for m in fixed] +
                   [(m, "Newly tested") for m in added] +
                   [(m, "Not tested in latest") for m in removed] +
                   [(m, "Status changed") for m in other])
    if not transitions:
        lines.append("| — | — | — | No status changes |")
    for model, label in transitions:
        lines.append(
            f"| `{esc(model)}` | {esc(old.get(model, {}).get('status', 'NOT TESTED'))} | "
            f"{esc(new.get(model, {}).get('status', 'NOT TESTED'))} | {label} |"
        )

    lines += [
        "", "## Performance (models passing both runs)", "",
        "🟢 **Improved** · 🔴 **Regressed** · ⚪ **No change** (absolute change < 3%; "
        "treated as run-to-run variance). Exactly 3% is considered a change.",
        "Higher throughput is better; lower P99 TTFT/TPOT is better.",
    ]
    comparable = [model for model in sorted(old.keys() & new.keys())
                  if old[model].get("status") == "PASS" and new[model].get("status") == "PASS"]
    for field, label, direction in METRICS:
        lines += ["", f"### {label}", "",
                  "| Model | Previous | Latest | Delta | Assessment |",
                  "| --- | ---: | ---: | ---: | --- |"]
        rows = 0
        for model in comparable:
            a = number((old[model].get("metrics") or {}).get(field))
            b = number((new[model].get("metrics") or {}).get(field))
            if a is None or b is None or a <= 0:
                continue
            delta = (b - a) / a * 100
            # Use unrounded delta for the threshold: rounding 2.99% to 3.0%
            # must not turn expected run-to-run variance into a regression.
            if abs(delta) < 3.0:
                assessment = "⚪ No change"
            elif delta * direction > 0:
                assessment = "🟢 Improved"
            else:
                assessment = "🔴 Regressed"
            lines.append(
                f"| `{esc(model)}` | {a:.2f} | {b:.2f} | {delta:+.2f}% | **{assessment}** |"
            )
            rows += 1
        if not rows:
            lines.append("| — | — | — | — | No comparable performance data |")

    def metadata(data):
        recipe = data.get("recipe_tools") or {}
        return (recipe.get("source", "unknown"), recipe.get("repository") or "image-bundled",
                recipe.get("ref") or "-", recipe.get("commit") or "-")

    lines += [
        "", "## Recipe tools provenance", "",
        "| Field | Previous | Latest |", "| --- | --- | --- |",
    ]
    for label, a, b in zip(("Source", "Repository", "Ref", "Commit"), metadata(previous), metadata(latest)):
        lines.append(f"| {label} | `{esc(a)}` | `{esc(b)}` |")
    lines += [
        "", "**Comparison caveat:** The summaries do not fully encode host CPU, container image, "
        "prompt count, and benchmark configuration. Performance changes are observational "
        "until those settings and per-model workloads are verified to match. A missing model "
        "is not treated as a validation regression. The 3% neutral band is a heuristic, not "
        "a measured statistical confidence interval.", "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-url", default="")
    parser.add_argument("--latest-url", default="")
    args = parser.parse_args()
    previous = json.loads(args.previous.read_text())
    latest = json.loads(args.latest.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(compare(previous, latest, args.previous_url, args.latest_url))


if __name__ == "__main__":
    main()
