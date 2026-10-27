#!/usr/bin/env python3
"""Compare the newest two eligible Xeon 6 pinned-release validation runs."""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from compare_weekly import compare
from report_notifications import make_notification
from send_teams_report import build_email_html


def release(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if not match:
        raise ValueError(f"Not a pinned vLLM release: {value!r}")
    return tuple(map(int, match.groups()))


def gh(*arguments: str) -> str:
    return subprocess.check_output(["gh", *arguments], text=True)


def eligible(meta: dict) -> bool:
    """Require full catalog, pinned image, same benchmarking policy."""
    try:
        release(meta["version"])
    except (KeyError, ValueError, TypeError):
        return False
    return not meta.get("image_override") and not meta.get("model")


def legacy_metadata(summary: dict) -> dict | None:
    """Recover a pinned release from an older artifact's recorded image tag.

    This is intentionally restricted to official pinned CPU image names, never
    the workflow run number. Legacy run input equivalence is not verifiable.
    """
    recipe = summary.get("recipe_tools") or {}
    if recipe.get("source") != "image":
        return None
    image = str(recipe.get("image") or "")
    match = re.fullmatch(r"vllm/vllm-openai-cpu:(v\d+\.\d+\.\d+)-x86_64", image)
    if not match or not recipe.get("image_id"):
        return None
    return {
        "version": match.group(1),
        "runner": "legacy-unverified", "recipe_api_base": "legacy-unverified",
        "vllm_env_vars": "legacy-unverified", "recipe_tools_repo": "",
        "recipe_tools_ref": "legacy-unverified", "bench_num_prompts": "legacy-unverified",
        "model": "", "image_override": "", "legacy_inferred": True,
    }


def settings(meta: dict) -> tuple:
    """Benchmark settings; physical runner differences are reported separately."""
    return tuple(str(meta.get(k, "")) for k in (
        "recipe_api_base", "vllm_env_vars", "recipe_tools_repo",
        "recipe_tools_ref", "bench_num_prompts",
    )) + (str(meta.get("bench_num_warmups", "0")),)


def complete(summary: dict, discovery: dict | None = None) -> bool:
    """All discovered models must have terminal results; FAIL is acceptable."""
    models = summary.get("models") or []
    ids = [row.get("model") for row in models]
    if not ids or any(not mid for mid in ids) or len(set(ids)) != len(ids):
        return False
    if any(row.get("status") not in ("PASS", "FAIL") for row in models):
        return False
    if (summary.get("tested") != len(models) or
            summary.get("passed") != sum(row["status"] == "PASS" for row in models) or
            summary.get("failed") != sum(row["status"] == "FAIL" for row in models)):
        return False
    if discovery is not None:
        if discovery.get("discovery_errors"):
            return False
        expected = [row.get("hf_id") for row in discovery.get("models", [])]
        if not expected or len(set(expected)) != len(expected) or set(expected) != set(ids):
            return False
    return True


def hardware(system_info: str) -> dict:
    """Read stable CPU and memory fields from collect_system_info.sh output."""
    fields = {"Architecture", "Model name", "CPU(s)", "Socket(s)",
              "Core(s) per socket", "Thread(s) per core", "NUMA node(s)"}
    result = {}
    section = ""
    for line in system_info.splitlines():
        if line.startswith("===== ") and line.endswith(" ====="):
            section = line.removeprefix("===== ").removesuffix(" =====")
            continue
        if section == "lscpu" and ":" in line:
            key, value = line.split(":", 1)
            if key.strip() in fields:
                result[key.strip()] = value.strip()
        if section == "free -h" and line.startswith("Mem:"):
            parts = line.split()
            if len(parts) > 1:
                result["Memory"] = parts[1]
    return result


def compatible(first: dict, second: dict) -> bool:
    a, b = first["metadata"], second["metadata"]
    # Historical runs without metadata are comparable only when the catalogs
    # agree. Their unverified settings are called out explicitly in the report.
    if a.get("legacy_inferred") or b.get("legacy_inferred"):
        return ({row["model"] for row in first["summary"]["models"]} ==
                {row["model"] for row in second["summary"]["models"]})
    return settings(a) == settings(b)


def select_pair(candidates: list[dict]) -> tuple[dict, dict]:
    """Prefer newer releases, with the newest complete run for each release.

    Candidates arrive newest-first from GitHub Actions; repeated attempts for
    one version supersede older ones, whether PASS or FAIL at workflow level.
    """
    by_release = {}
    for run in candidates:
        if eligible(run["metadata"]) and complete(run["summary"]):
            by_release.setdefault(release(run["metadata"]["version"]), run)
    versions = sorted(by_release, reverse=True)
    for candidate_version in versions:
        candidate = by_release[candidate_version]
        for baseline_version in versions:
            if baseline_version < candidate_version:
                baseline = by_release[baseline_version]
                if compatible(baseline, candidate):
                    return baseline, candidate
    raise ValueError("Need two different pinned releases with complete full-catalog "
                     "results and compatible workload settings. Failed workflow "
                     "runs are eligible when all models finished.")


def candidates_from_github(repo: str, limit: int) -> list[dict]:
    """Use actual run artifacts, never guess pinned version from run number."""
    api = f"repos/{repo}/actions/workflows/major-version-xeon6.yml/runs?status=completed&per_page=100"
    # --paginate emits one JSON object per page; --jq flattens consistently.
    records = gh("api", "--paginate", api, "--jq",
                 '.workflow_runs[] | select(.event == "workflow_dispatch" and .head_branch == "main") | [.id,.run_number,.run_attempt,.html_url] | @tsv')
    found = []
    for line in records.splitlines()[:limit]:
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        run_id, run_number, attempt, url = parts
        artifact = f"xeon6-major-{run_number}-{attempt}"
        with tempfile.TemporaryDirectory(prefix="major-report-") as directory:
            try:
                subprocess.run(["gh", "run", "download", run_id, "--repo", repo,
                                "--name", artifact, "--dir", directory], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            root = Path(directory)
            meta_file = next(root.rglob("major-run-metadata.json"), None)
            summary_file = next(root.rglob("weekly-summary.json"), None)
            discovery_file = next(root.rglob("discovery.json"), None)
            system_file = next(root.rglob("system-info.txt"), None)
            if not summary_file:
                continue
            try:
                summary = json.loads(summary_file.read_text())
                if summary.get("run_id") != f"major-{run_number}-attempt-{attempt}":
                    continue
                meta = (json.loads(meta_file.read_text()) if meta_file
                        else legacy_metadata(summary))
                # Prefer recorded per-model workload settings. Older artifacts
                # had no warmup setting and therefore used zero warmups.
                warmups = {str((m.get("workload") or {}).get("num_warmups", 0))
                           for m in summary.get("models", [])}
                if len(warmups) != 1:
                    continue  # Mixed warmup policies are not comparable.
                if meta is not None:
                    meta["bench_num_warmups"] = warmups.pop()
                discovery = json.loads(discovery_file.read_text()) if discovery_file else None
                if not meta or not eligible(meta) or not complete(summary, discovery):
                    continue
            except (ValueError, OSError, TypeError):
                continue
            found.append({"metadata": meta, "summary": summary, "url": url,
                          "run_id": run_id,
                          "hardware": hardware(system_file.read_text()) if system_file else {},
                          "catalog_verified": discovery is not None})
            # Scan the full window: newer-by-version may have run earlier in time.
            # Only selecting the first available pair could pick older versions.
    return found


def generate(baseline: dict, candidate: dict, repo: str, output: Path) -> None:
    bv = "v" + ".".join(map(str, release(baseline["metadata"]["version"])))
    cv = "v" + ".".join(map(str, release(candidate["metadata"]["version"])))
    old, new = baseline["summary"], candidate["summary"]
    document = compare(old, new, baseline["url"], candidate["url"])
    document = (document.replace("# Xeon 6 — Weekly Recipe Validation Comparison",
                                 f"# Xeon 6 — Major Release Comparison: {bv} → {cv}")
                .replace("**Previous:**", f"**Baseline ({bv}):**")
                .replace("**Latest:**", f"**Candidate ({cv}):**")
                .replace("| Previous | Latest |", "| Baseline | Candidate |")
                .replace("| Model | Previous | Latest |", "| Model | Baseline | Candidate |")
                .replace("Not tested in latest", "Not tested in candidate"))
    old_hw = baseline.get("hardware") or {}
    new_hw = candidate.get("hardware") or {}
    hw_fields = ("Model name", "Architecture", "CPU(s)", "Socket(s)",
                 "Core(s) per socket", "Thread(s) per core", "NUMA node(s)", "Memory")
    hw_rows = [("Runner", baseline["metadata"].get("runner"),
                candidate["metadata"].get("runner"))]
    hw_rows += [(field, old_hw.get(field), new_hw.get(field)) for field in hw_fields]
    changes = [(field, a, b) for field, a, b in hw_rows if a and b and a != b]
    unknown = [field for field, a, b in hw_rows if not a or not b]
    status = ("⚠️ Hardware differences detected. Performance deltas may reflect "
              "hardware changes, not just the vLLM release." if changes else
              "Hardware fields recorded for both runs match." if not unknown else
              "⚠️ Hardware equivalence cannot be fully verified: some fields are missing.")
    hw_table = ["", "## Hardware comparison", "", f"**{status}**", "",
                "| Field | Baseline | Candidate | Assessment |",
                "| --- | --- | --- | --- |"]
    for field, a, b in hw_rows:
        assessment = "⚠️ Different" if a and b and a != b else "Same" if a and b else "Unknown"
        hw_table.append(f"| {field} | {str(a or 'Not recorded').replace('|', '/')} | "
                        f"{str(b or 'Not recorded').replace('|', '/')} | {assessment} |")
    hw_table += ["", "Hardware comes from the archived system-info.txt. "
                 "A missing value is not evidence of matching hardware.", ""]
    document += "\n".join(hw_table)
    baseline_warmups = baseline["metadata"].get("bench_num_warmups", "0")
    candidate_warmups = candidate["metadata"].get("bench_num_warmups", "0")
    document += ("\n## Benchmark warmup policy\n\n"
                 "| Setting | Baseline | Candidate |\n| --- | ---: | ---: |\n"
                 f"| Unmeasured warmup requests per model | {baseline_warmups} | "
                 f"{candidate_warmups} |\n\n"
                 "Warmup requests are excluded from the measured prompt count. "
                 "Historical major artifacts without this field are treated as zero warmups.\n")
    if baseline["metadata"].get("legacy_inferred") or candidate["metadata"].get("legacy_inferred"):
        document += "\n⚠️ **Legacy metadata:** Workload equivalence cannot be confirmed.\n"
    if not baseline.get("catalog_verified", True) or not candidate.get("catalog_verified", True):
        document += "\n⚠️ **Catalog evidence:** At least one run lacked archived discovery.json. " \
                    "Only the summary's terminal result counts could be checked.\n"
    document += ("\n**Selection:** Newest eligible completed full-catalog run, paired with the "
                 "newest distinct release with compatible workload settings. "
                 "Baseline always uses the lower semantic version. "
                 "Legacy artifacts can use the exact recorded Docker image tag and "
                 "digest to recover the pinned release. For legacy runs, runner, "
                 "prompt count, environment overrides, and recipe API settings "
                 "cannot be independently verified; confirm they match before "
                 "attributing performance differences to the vLLM release.\n")
    notification = make_notification(old, new, repo, baseline["url"], candidate["url"])
    identifier = f"{cv}-vs-{bv}-{candidate['run_id']}-{baseline['run_id']}"
    identifier = re.sub(r"[^A-Za-z0-9_.-]", "_", identifier)
    base = f"https://github.com/{repo}"
    notification.update({
        "report_type": "major", "baseline_version": bv, "candidate_version": cv,
        "title": f"Xeon 6 vLLM major validation: {bv} → {cv}",
        "report_id": identifier,
        "report_url": f"{base}/blob/report/major/latest.md",
        "report_history_url": f"{base}/tree/report/major/history",
        "exact_report_url": f"{base}/blob/report/major/history/{identifier}.md",
        "baseline_run_url": baseline["url"], "candidate_run_url": candidate["url"],
    })
    # Power Automate can use email_html for a complete standalone Outlook email.
    html = build_email_html(notification)
    html = (html.replace("Xeon 6 Weekly vLLM Recipes Validation", notification["title"])
            .replace("Weekly summary", "Release comparison summary")
            .replace("Previous", f"Baseline ({bv})")
            .replace("Latest", f"Candidate ({cv})")
            .replace("This weekly report", "This major release report"))
    hardware_html = ("<h2>Hardware comparison</h2>"
                     + "<p><strong>" + html_lib.escape(status) + "</strong></p>"
                     + "<table border='1' cellpadding='5'><tr><th>Field</th>"
                       "<th>Baseline</th><th>Candidate</th><th>Assessment</th></tr>")
    for field, a, b in hw_rows:
        assessment = "Different" if a and b and a != b else "Same" if a and b else "Unknown"
        hardware_html += "<tr>" + "".join(
            "<td>" + html_lib.escape(str(value)) + "</td>"
            for value in (field, a or "Not recorded", b or "Not recorded", assessment)
        ) + "</tr>"
    hardware_html += "</table>"
    if "</body>" in html:
        html = html.replace("</body>", hardware_html + "</body>")
    else:
        html += hardware_html
    warmup_html = ("<h2>Benchmark warmup policy</h2><p>Unmeasured warmup requests "
                   f"per model: baseline {html_lib.escape(str(baseline_warmups))}; "
                   f"candidate {html_lib.escape(str(candidate_warmups))}. "
                   "Historical runs without recorded warmups count as zero.</p>")
    html = html.replace("</body>", warmup_html + "</body>") if "</body>" in html else html + warmup_html
    notification["email_html"] = html
    output.mkdir(parents=True, exist_ok=True)
    (output / "latest.md").write_text(document, encoding="utf-8")
    (output / "notification.json").write_text(json.dumps(notification, indent=2) + "\n", encoding="utf-8")
    (output / "filename.txt").write_text(identifier + ".md\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scan-limit", type=int, default=60)
    args = parser.parse_args()
    if args.scan_limit < 2:
        parser.error("scan-limit must be at least 2")
    pair = select_pair(candidates_from_github(args.repo, args.scan_limit))
    generate(*pair, repo=args.repo, output=args.output_dir)
    print("Major comparison:", pair[0]["metadata"]["version"], "→",
          pair[1]["metadata"]["version"])


if __name__ == "__main__":
    main()
