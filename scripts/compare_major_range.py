#!/usr/bin/env python3
"""Optional multi-release Xeon 6 comparison, retaining the existing two-run report."""
from __future__ import annotations
from report_notifications import make_notification

import argparse
from html import escape
import json
from pathlib import Path

from compare_major import (candidates_from_github, compatible, complete, eligible,
                           generate, release, tool_source_warning)
from compare_weekly import METRICS, number
from email_font import apply_aptos
from benchmark_statistics import format_metric

RECIPES_URL = "https://recipes.vllm.ai/browse?panel=open&hw=xeon6"


def version_name(run: dict) -> str:
    return "v" + ".".join(map(str, release(run["metadata"]["version"])))


def choose_runs(candidates: list[dict], start_version: str = "", end_version: str = "",
                *, allow_tool_source_change: bool = False) -> list[dict]:
    """Select one compatible attempt per distinct release, sorted by semver."""
    if start_version and end_version and release(start_version) >= release(end_version):
        raise ValueError("start_version must be lower than end_version")
    by_version: dict[tuple[int, int, int], list[dict]] = {}
    for run in candidates:
        if eligible(run["metadata"]) and complete(run["summary"]):
            by_version.setdefault(release(run["metadata"]["version"]), []).append(run)
    versions = sorted(by_version)
    if not versions:
        raise ValueError("No eligible major validation artifacts")
    upper = release(end_version) if end_version else versions[-1]
    if upper not in by_version:
        raise ValueError(f"No eligible run found for end version {end_version}")
    if start_version:
        lower = release(start_version)
        if lower not in by_version:
            raise ValueError(f"No eligible run found for start version {start_version}")
    else:
        prior = [v for v in versions if v < upper]
        if not prior:
            raise ValueError("No lower-release validation found")
        lower = prior[-1]
    requested = [v for v in versions if lower <= v <= upper]
    # Prefer the newest run of each release, but allow an earlier attempt of that
    # same release when it is the only one compatible with adjacent releases.
    selected: list[dict] = []

    def solve(index: int) -> bool:
        if index == len(requested):
            return True
        for run in by_version[requested[index]]:
            if selected and not compatible(selected[-1], run,
                    allow_tool_source_change=allow_tool_source_change):
                continue
            selected.append(run)
            if solve(index + 1):
                return True
            selected.pop()
        return False

    if not solve(0):
        raise ValueError("No workload-compatible run for every release in the requested range")
    if len(selected) < 2:
        raise ValueError("At least two distinct releases are required")
    return selected


def status_rows(runs: list[dict]) -> list[tuple[str, list[str]]]:
    data = [{r["model"]: r for r in run["summary"]["models"]} for run in runs]
    return [(model, [rows.get(model, {}).get("status", "NOT TESTED") for rows in data])
            for model in sorted(set().union(*(set(rows) for rows in data)))]


def metric_cells(runs: list[dict], model: str, metric: str, direction: int):
    """Return values plus colors, relative to the immediately preceding version.

    No metric is assessed unless both adjacent versions passed the same model.
    The 3% neutral band matches compare_weekly.py.
    """
    rows = [{r["model"]: r for r in run["summary"]["models"]} for run in runs]
    result = []
    for i, data in enumerate(rows):
        row = data.get(model)
        value = number((row.get("metrics") or {}).get(metric)) if row and row.get("status") == "PASS" else None
        color = "neutral"
        if i and value is not None:
            prev = rows[i - 1].get(model)
            previous = number((prev.get("metrics") or {}).get(metric)) if prev and prev.get("status") == "PASS" else None
            if previous is not None and previous > 0:
                delta = (value - previous) / previous * 100
                if abs(delta) >= 3.0:
                    color = "green" if delta * direction > 0 else "red"
        result.append((value, color))
    return result


def generate_range(runs: list[dict], repo: str, output: Path) -> None:
    if len(runs) == 2:
        return generate(runs[0], runs[1], repo, output)
    versions = [version_name(r) for r in runs]
    headers = [f"{v}" for v in versions]
    title = f"Xeon 6 vLLM major validation: {versions[0]} → {versions[-1]}"
    md = [f"# {title}", "", f"Validation scope: Xeon 6 [vLLM Recipes]({RECIPES_URL}) "
          f"validated on vLLM releases **{versions[0]}** through **{versions[-1]}**.", "",
          "## Release comparison summary", "",
          "| Measure | " + " | ".join(headers) + " |",
          "| --- | " + " | ".join(["---:"] * len(runs)) + " |"]
    for name, key in (("Tested", "tested"), ("Passed", "passed"), ("Failed", "failed")):
        md.append("| " + name + " | " + " | ".join(str(r["summary"].get(key, 0)) for r in runs) + " |")
    md += ["", "## Performance by release", "",
           "Each number is compared with the immediately preceding release. "
           "🟢 improved; 🔴 regressed; unmarked = no change (<3%) or not comparable. "
           "Higher throughput is better; lower latency is better. "
           "Color represents an observed change, not a statistically established regression.", ""]
    tables = []
    all_models = [m for m, _ in status_rows(runs)]
    for metric, label, direction in METRICS:
        md += [f"### {label}", "", "| Model | " + " | ".join(headers) + " |",
               "| --- | " + " | ".join(["---:"] * len(runs)) + " |"]
        trows = []
        for model in all_models:
            cells = metric_cells(runs, model, metric, direction)
            if all(value is None for value, _ in cells):
                continue
            rendered = [("—" if value is None else format_metric(
                next(r for r in run["summary"]["models"] if r["model"] == model), metric))
                for run, (value, _) in zip(runs, cells)]
            md_rendered = [("🟢 " if color == "green" else "🔴 " if color == "red" else "") + value
                           for value, (_, color) in zip(rendered, cells)]
            md.append("| `" + model.replace("|", "\\|") + "` | " + " | ".join(md_rendered) + " |")
            trows.append((model, [(value, color) for value, (_, color) in zip(rendered, cells)]))
        if not trows:
            md.append("| — | " + " | ".join(["—"] * len(runs)) + " |")
        md.append("")
        tables.append((label, trows))
    md += ["## Comparison qualifications", "",
           "Repeated runs: throughput is mean; P99 is median of run-level P99s. "
           "± denotes sample standard deviation, not a confidence interval. "
           "Legacy single runs have no standard deviation; mixed run counts are observational.", "",
           "Only successful benchmark metrics are displayed; FAIL and NOT TESTED are blank. "
           "Adjacent-release changes are evaluated only when both models passed. "
           "Selected releases must pass recorded workload compatibility checks.", ""]
    for before, after in zip(runs, runs[1:]):
        warning = tool_source_warning(before, after)
        if warning:
            md.append(f"- **{version_name(before)} → {version_name(after)}:** {warning}")
    # HTML uses inline styles supported by Outlook (unlike GitHub Markdown, which
    # sanitizes arbitrary CSS). Always display numeric values, not only colors.
    def cell(value: object, color: str = "neutral", head: bool = False):
        tag = "th" if head else "td"
        style = "border:1px solid #cbd5e1;padding:6px;"
        if head:
            style += "background:#e9eef5;"
        if color == "green":
            style += "color:#16733a;font-weight:bold;"
        elif color == "red":
            style += "color:#b42318;font-weight:bold;"
        return f'<{tag} style="{style}">{escape(str(value))}</{tag}>'

    def table(cols, rows):
        return ('<table style="border-collapse:collapse;font-size:13px">'
                + '<tr>' + ''.join(cell(v, head=True) for v in cols) + '</tr>'
                + ''.join('<tr>' + ''.join(cell(v, col) for v, col in row) + '</tr>' for row in rows)
                + '</table>')

    summary_rows = [[(name, "neutral")] + [(r["summary"].get(key, 0), "neutral") for r in runs]
                    for name, key in (("Tested", "tested"), ("Passed", "passed"), ("Failed", "failed"))]
    html = [f'<html><body style="font-family:Arial,sans-serif;color:#202124">',
            f'<h2>{escape(title)}</h2>',
            f'<p><b>Validation scope:</b> Xeon 6 <a href="{escape(RECIPES_URL, quote=True)}">vLLM Recipes</a> '
            f'validated on vLLM releases {escape(versions[0])} through {escape(versions[-1])}.</p>',
            '<h3>Release comparison summary</h3>', table(["Measure", *headers], summary_rows),
            '<h3>Performance by release</h3>',
            '<p><span style="color:#16733a">Green = improved</span>; '
            '<span style="color:#b42318">red = regressed</span>; '
            'uncolored = neutral or not comparable. Changes use a 3% threshold relative '
            'to the immediately preceding release.</p>']
    for label, rows in tables:
        values = [[(model, "neutral")] + [(value, color)
                                             for value, color in cells] for model, cells in rows]
        html += [f'<h3>{escape(label)}</h3>', table(["Model", *headers], values)]
    html += ['<p>Tooling or hardware differences may affect observed performance. '
             'Read the GitHub Markdown report for comparison qualifications.</p>', '</body></html>']
    report_id = "-".join(versions) + "-" + "-".join(str(r["run_id"]) for r in runs)
    report_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in report_id)
    base = f"https://github.com/{repo}"
    # Preserve the standard Power Automate payload contract for range reports.
    # The range-specific HTML email is retained below.
    notice = make_notification(runs[0]["summary"], runs[-1]["summary"], repo,
                               runs[0]["url"], runs[-1]["url"])
    notice.update({
        "report_type": "major", "comparison_mode": "range",
        "title": title,
        "baseline_version": versions[0], "candidate_version": versions[-1],
        "releases": versions, "report_id": report_id,
        "report_url": f"{base}/blob/report/major/latest.md",
        "report_history_url": f"{base}/tree/report/major/history",
        "exact_report_url": f"{base}/blob/report/major/history/{report_id}.md",
        "baseline_run_url": runs[0]["url"],
        "candidate_run_url": runs[-1]["url"],
        "email_html": apply_aptos("\n".join(html)),
    })
    output.mkdir(parents=True, exist_ok=True)
    (output / "latest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (output / "notification.json").write_text(json.dumps(notice, indent=2) + "\n", encoding="utf-8")
    (output / "filename.txt").write_text(report_id + ".md\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scan-limit", type=int, default=60)
    parser.add_argument("--start-version", default="")
    parser.add_argument("--end-version", default="")
    parser.add_argument("--allow-tool-source-change", action="store_true")
    args = parser.parse_args()
    if args.scan_limit < 2:
        parser.error("scan-limit must be at least 2")
    runs = choose_runs(candidates_from_github(args.repo, args.scan_limit),
                       args.start_version, args.end_version,
                       allow_tool_source_change=args.allow_tool_source_change)
    generate_range(runs, args.repo, args.output_dir)
    print("Major release range:", " → ".join(version_name(r) for r in runs))


if __name__ == "__main__":
    main()
