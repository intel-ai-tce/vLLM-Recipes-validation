#!/usr/bin/env python3
"""Compare the newest two eligible Xeon 6 pinned-release validation runs."""
from __future__ import annotations

import argparse
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
    return tuple(str(meta.get(k, "")) for k in (
        "runner", "recipe_api_base", "vllm_env_vars", "recipe_tools_repo",
        "recipe_tools_ref", "bench_num_prompts",
    ))


def select_pair(candidates: list[dict]) -> tuple[dict, dict]:
    """Newest eligible run, then newest *other* version with matching inputs.

    The baseline is the lower semantic version, regardless of execution order.
    """
    newest = next((r for r in candidates if eligible(r["metadata"])), None)
    if newest is None:
        raise ValueError("No eligible pinned-version major validation run")
    other = next((r for r in candidates if eligible(r["metadata"])
                  and release(r["metadata"]["version"]) != release(newest["metadata"]["version"])
                  and settings(r["metadata"]) == settings(newest["metadata"])), None)
    if other is None:
        raise ValueError("Need two different pinned releases with matching runner and workload settings")
    pair = tuple(sorted((newest, other), key=lambda r: release(r["metadata"]["version"])))
    if any(r["metadata"].get("legacy_inferred") for r in pair):
        left, right = (set(m.get("model") for m in r["summary"].get("models", []))
                       for r in pair)
        if not left or left != right:
            raise ValueError("Legacy artifact model catalogs differ; cannot compare")
    return pair


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
            if not summary_file:
                continue
            try:
                summary = json.loads(summary_file.read_text())
                if summary.get("run_id") != f"major-{run_number}-attempt-{attempt}":
                    continue
                meta = (json.loads(meta_file.read_text()) if meta_file
                        else legacy_metadata(summary))
                if not meta or not eligible(meta) or not summary.get("models"):
                    continue
            except (ValueError, OSError, TypeError):
                continue
            found.append({"metadata": meta, "summary": summary, "url": url,
                          "run_id": run_id})
            if len(found) >= 2:
                try:
                    select_pair(found)
                    break
                except ValueError:
                    pass
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
    document += ("\n**Selection:** Newest eligible completed full-catalog run, paired with the "
                 "newest available distinct release on matching runner and workload settings. "
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
