#!/usr/bin/env python3
"""Build report notifications, Wiki index, and deliver Power Automate webhook."""
from __future__ import annotations

import argparse
import json
from html import escape
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
    # Keep performance comparisons consistent with compare_weekly.py.
    from compare_weekly import METRICS, number
    performance = {}
    comparable = sorted(
        model for model in old.keys() & new.keys()
        if old[model].get("status") == "PASS" and new[model].get("status") == "PASS"
    )
    for field, label, direction in METRICS:
        rows = []
        for model in comparable:
            before = number((old[model].get("metrics") or {}).get(field))
            after = number((new[model].get("metrics") or {}).get(field))
            if before is None or after is None or before <= 0:
                continue
            delta = (after - before) / before * 100
            assessment = ("No change" if abs(delta) < 3.0 else
                          "Improved" if delta * direction > 0 else "Regressed")
            rows.append({"model": model, "previous": round(before, 2),
                         "latest": round(after, 2), "delta_pct": round(delta, 2),
                         "assessment": assessment})
        performance[field] = {"label": label, "rows": rows}

    return {
        "performance": performance,
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



def nightly_email_html(nightly: dict) -> str:
    """Email-safe HTML fragment, ready for one Power Automate expression."""
    if not nightly:
        return ""
    def cell(value):
        return escape(str(value), quote=True)
    def table(headers, rows):
        th = ''.join(f'<th style="border:1px solid #ddd;padding:6px;text-align:left">{cell(h)}</th>' for h in headers)
        body = ''.join('<tr>' + ''.join(f'<td style="border:1px solid #ddd;padding:6px">{value}</td>' for value in row) + '</tr>' for row in rows)
        return f'<table style="border-collapse:collapse;width:100%"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
    daily = []
    for run in nightly.get('runs', []):
        url = str(run.get('url', ''))
        link = (f'<a href="{cell(url)}">{cell(run.get("run_id", ""))}</a>'
                if url.startswith('https://github.com/') else cell(run.get('run_id', '')))
        if run.get('status') == 'available':
            data = [cell(run.get(k, 0)) for k in ('tested', 'passed', 'failed')]
        else:
            data = ['&mdash;', '&mdash;', cell(run.get('status', 'missing'))]
        daily.append([cell(run.get('date', '')), link, *data])
    models = [
        [cell(m.get('model', '')), cell(f"{m.get('failed_nights', 0)}/{m.get('tested_nights', 0)}"),
         cell(m.get('passed_nights', 0))]
        for m in nightly.get('failed_models', [])
    ]
    html = '<h2>Nightly Validation Summary</h2>'
    html += (f'<p><b>Window (UTC):</b> {cell(nightly.get("start", ""))} to '
             f'{cell(nightly.get("end", ""))}<br>'
             f'<b>Scheduled runs:</b> {cell(nightly.get("scheduled_runs", 0))}; '
             f'<b>Missing summaries:</b> {cell(nightly.get("missing_summaries", 0))}<br>'
             f'<b>Model executions:</b> {cell(nightly.get("tested", 0))} tested, '
             f'{cell(nightly.get("passed", 0))} passed, {cell(nightly.get("failed", 0))} failed</p>')
    if daily:
        html += table(['Date (UTC)', 'Run', 'Tested', 'Passed', 'Failed'], daily)
    else:
        html += '<p>No scheduled nightly runs found in this window.</p>'
    html += '<h3>Models with nightly failures</h3>'
    html += (table(['Model', 'Failed / Tested nights', 'Passed nights'], models)
             if models else '<p>No recorded model failures.</p>')
    html += '<p><i>Missing summaries are not counted as passes.</i></p>'
    return html

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
    build.add_argument("--nightly", help="Optional structured nightly summary JSON")
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
        if args.nightly:
            n['nightly'] = json.loads(Path(args.nightly).read_text())
            n['nightly_email_html'] = nightly_email_html(n['nightly'])
        Path(args.output).write_text(json.dumps(n, indent=2) + "\n")
    elif args.command == "wiki":
        Path(args.output).write_text(wiki_page(json.loads(Path(args.notification).read_text())))
    else:
        send(json.loads(Path(args.notification).read_text()))


if __name__ == "__main__":
    main()
