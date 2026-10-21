#!/usr/bin/env python3
"""Build report notifications, Wiki index, and deliver Power Automate webhook."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib import error, request


def make_notification(previous: dict, latest: dict, repo: str, previous_url: str, latest_url: str) -> dict:
    old = {r["model"]: r for r in previous.get("models", []) if r.get("model")}
    new = {r["model"]: r for r in latest.get("models", []) if r.get("model")}
    regressions = sorted(m for m in old.keys() & new.keys()
                         if old[m].get("status") == "PASS" and new[m].get("status") != "PASS")
    recovered = sorted(m for m in old.keys() & new.keys()
                       if old[m].get("status") != "PASS" and new[m].get("status") == "PASS")
    latest_id = str(latest.get("run_id") or "unknown")
    previous_id = str(previous.get("run_id") or "unknown")
    base = f"https://github.com/{repo}"
    exact_id = re.sub(r"[^A-Za-z0-9_.-]", "_", latest_id) + "-vs-" + re.sub(r"[^A-Za-z0-9_.-]", "_", previous_id)
    return {
        "title": "Xeon 6 weekly vLLM Recipes validation",
        "report_id": exact_id,
        "date": latest.get("run_date", ""),
        "repository": repo,
        "report_url": f"{base}/blob/report/weekly/latest.md",
        "report_history_url": f"{base}/tree/report/weekly/history",
        "exact_report_url": f"{base}/blob/report/weekly/history/{exact_id}.md",
        "wiki_url": f"{base}/wiki/Weekly-Validation-Reports",
        "latest_run_url": latest_url,
        "previous_run_url": previous_url,
        "latest_run_id": latest_id,
        "previous_run_id": previous_id,
        "tested": int(latest.get("tested", len(new))),
        "passed": int(latest.get("passed", 0)),
        "failed": int(latest.get("failed", 0)),
        "previous_tested": int(previous.get("tested", len(old))),
        "previous_passed": int(previous.get("passed", 0)),
        "previous_failed": int(previous.get("failed", 0)),
        "new_failures": regressions,
        "recovered_models": recovered,
        "newly_tested": sorted(new.keys() - old.keys()),
        "not_tested_latest": sorted(old.keys() - new.keys()),
        "notice": "Performance deltas require matching workload and hardware for valid comparison.",
    }


def wiki_page(notification: dict) -> str:
    n = notification
    return ("# Weekly Validation Reports\n\n"
            "Automatically updated after weekly Xeon 6 comparison reports.\n\n"
            f"- **Latest weekly report:** [View comparison]({n['report_url']})\n"
            f"- **This comparison:** [Permanent report]({n['exact_report_url']})\n"
            f"- **Report archive:** [Weekly history]({n['report_history_url']})\n"
            f"- **Latest validation:** [GitHub Actions run]({n['latest_run_url']})\n"
            f"- **Previous validation:** [GitHub Actions run]({n['previous_run_url']})\n\n"
            f"Latest: **{n['tested']} tested, {n['passed']} passed, {n['failed']} failed**. "
            f"New failures: **{len(n['new_failures'])}**; recovered: **{len(n['recovered_models'])}**.\n\n"
            "## Report interpretation\n\n"
            "The report compares status transitions and observed output throughput, P99 TTFT, "
            "and P99 TPOT. Performance changes should only be treated as regressions "
            "after checking equivalent workload, hardware, model settings, and image.\n")


def send(notification: dict):
    url = os.environ.get("POWER_AUTOMATE_REPORT_URL", "")
    if not url.startswith("https://"):
        raise ValueError("POWER_AUTOMATE_REPORT_URL must be an HTTPS webhook URL")
    body = json.dumps(notification).encode("utf-8")
    for attempt in range(3):
        req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=30) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Power Automate returned HTTP {response.status}")
            print("Report notification delivered to Power Automate")
            return
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            # Never print the webhook URL, which can contain a secret signature.
            if attempt == 2 or isinstance(exc, error.HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError("Power Automate notification failed; inspect webhook and flow status") from None
            time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    for field in ("previous", "latest", "repo", "output"):
        build.add_argument(f"--{field}", required=True)
    build.add_argument("--previous-url", default="")
    build.add_argument("--latest-url", default="")
    wiki = sub.add_parser("wiki")
    wiki.add_argument("--notification", required=True)
    wiki.add_argument("--output", required=True)
    deliver = sub.add_parser("send")
    deliver.add_argument("--notification", required=True)
    args = parser.parse_args()
    if args.command == "build":
        n = make_notification(json.loads(Path(args.previous).read_text()),
                              json.loads(Path(args.latest).read_text()),
                              args.repo, args.previous_url, args.latest_url)
        Path(args.output).write_text(json.dumps(n, indent=2) + "\n")
    elif args.command == "wiki":
        Path(args.output).write_text(wiki_page(json.loads(Path(args.notification).read_text())))
    else:
        send(json.loads(Path(args.notification).read_text()))


if __name__ == "__main__":
    main()
