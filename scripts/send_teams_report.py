#!/usr/bin/env python3
"""Post the existing weekly report notification as a Teams Workflow Adaptive Card."""
from __future__ import annotations

import argparse
from html import escape
import json
import os
import time
from pathlib import Path
from urllib import error, request


def build_payload(n: dict) -> dict:
    """Teams Workflows webhook requires a message with adaptive-card attachments."""
    def txt(value: object) -> str:
        return str(value if value is not None else "")

    def fact(label: str, value: object) -> dict:
        return {"title": label, "value": txt(value)}

    failures = n.get("new_failures") or []
    recovered = n.get("recovered_models") or []
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Xeon 6 weekly validation report",
             "size": "Medium", "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "text": "Weekly comparison published successfully",
             "wrap": True},
            {"type": "FactSet", "facts": [
                fact("Date", n.get("date") or "Not supplied"),
                fact("Tested", n.get("tested", 0)),
                fact("Passed", n.get("passed", 0)),
                fact("Failed", n.get("failed", 0)),
                fact("New failures", len(failures)),
                fact("Recovered", len(recovered)),
            ]},
            {"type": "TextBlock", "text": (
                "New failures: " + ", ".join(map(txt, failures[:8])) +
                (f" (+{len(failures) - 8} more)" if len(failures) > 8 else "")
                if failures else "No newly failing models"), "wrap": True},
            {"type": "TextBlock", "text": (
                "Recovered: " + ", ".join(map(txt, recovered[:8])) +
                (f" (+{len(recovered) - 8} more)" if len(recovered) > 8 else "")
                if recovered else "No newly recovered models"), "wrap": True},
            {"type": "TextBlock", "text": (
                "Full throughput, P99 TTFT and P99 TPOT tables are in the email report. "
                "Verify matched workloads before attributing performance regressions."),
             "wrap": True, "isSubtle": True, "size": "Small"},
        ],
        "actions": [
            {"type": "Action.OpenUrl", "title": "View this report",
             "url": n["exact_report_url"]},
            {"type": "Action.OpenUrl", "title": "Latest report",
             "url": n["report_url"]},
            {"type": "Action.OpenUrl", "title": "Validation run",
             "url": n["latest_run_url"]},
        ],
    }
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "content": card,
    }]}


def weekly_facts(n: dict) -> list[dict]:
    """Repeat the weekly summary on every card so each post stands alone."""
    return [{"title": label, "value": str(value)} for label, value in (
        ("Date", n.get("date") or "Not supplied"),
        ("Tested", n.get("tested", 0)),
        ("Passed", n.get("passed", 0)),
        ("Failed", n.get("failed", 0)),
        ("New failures", len(n.get("new_failures") or [])),
        ("Recovered", len(n.get("recovered_models") or [])),
    )]


def card_message(card: dict) -> dict:
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "content": card,
    }]}


def metric_payload(n: dict, key: str) -> dict:
    """Use FactSet rows to fit a complete model table into a Teams v1.4 card."""
    metric = (n.get("performance") or {}).get(key) or {}
    rows = metric.get("rows") or []
    label = metric.get("label") or key
    facts = []
    for row in rows:
        assessment = row["assessment"]
        symbol = {"Improved": "🟢", "Regressed": "🔴", "No change": "⚪"}[assessment]
        facts.append({
            "title": row["model"],
            "value": (f"{row['previous']:.2f} → {row['latest']:.2f} | "
                      f"{row['delta_pct']:+.2f}% {symbol} {assessment}"),
        })
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": f"Xeon 6 weekly — {label}",
             "size": "Medium", "weight": "Bolder", "wrap": True},
            {"type": "FactSet", "facts": weekly_facts(n)},
            {"type": "TextBlock", "text": "Model | Previous → Latest | Delta | Assessment",
             "weight": "Bolder", "wrap": True, "separator": True},
            {"type": "FactSet", "facts": facts} if facts else
            {"type": "TextBlock", "text": "No comparable performance data", "wrap": True},
            {"type": "TextBlock", "text": (
                "🟢 Improved · 🔴 Regressed · ⚪ No change (<3% variance). "
                "Higher throughput and lower latency are better. "
                "Compare matched workloads only."), "wrap": True,
             "size": "Small", "isSubtle": True},
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Full report",
                     "url": n["exact_report_url"]}],
    }
    return card_message(card)


def build_email_html(n: dict) -> str:
    """Standalone Outlook-friendly email with all comparable model rows."""
    def e(value: object) -> str:
        return escape(str(value if value is not None else ""), quote=True)

    def cell(value: object, header: bool = False) -> str:
        tag = "th" if header else "td"
        style = ("background:#e9eef5;font-weight:bold;" if header else "")
        return f'<{tag} style="border:1px solid #cbd5e1;padding:6px;{style}">{e(value)}</{tag}>'

    def table(headers: list[str], rows: list[list[object]]) -> str:
        head = "<tr>" + "".join(cell(x, True) for x in headers) + "</tr>"
        body = "".join("<tr>" + "".join(cell(x) for x in row) + "</tr>" for row in rows)
        return '<table style="border-collapse:collapse;width:100%;font-size:13px">' + head + body + "</table>"

    summary = table(["Date", "Tested", "Passed", "Failed", "New failures", "Recovered"], [[
        n.get("date", ""), n.get("tested", 0), n.get("passed", 0), n.get("failed", 0),
        len(n.get("new_failures") or []), len(n.get("recovered_models") or []),
    ]])
    parts = ['<html><body style="font-family:Arial,sans-serif;color:#202124">',
             '<h2>Xeon 6 Weekly vLLM Recipes Validation</h2>',
             '<h3>Weekly summary</h3>', summary]
    for name, items in (("Newly failing models", n.get("new_failures") or []),
                        ("Recovered models", n.get("recovered_models") or [])):
        parts.append(f'<p><strong>{e(name)}:</strong> {e(", ".join(items) if items else "None")}</p>')

    # Show nightly results before the performance tables.
    nightly_html = n.get("nightly_email_html") or ""
    if nightly_html:
        parts.append(nightly_html)

    for key, heading in (("output_throughput", "Output Throughput (tok/s)"),
                         ("p99_ttft_ms", "P99 TTFT (ms)"),
                         ("p99_tpot_ms", "P99 TPOT (ms)")):
        info = (n.get("performance") or {}).get(key) or {}
        rows = []
        for r in info.get("rows") or []:
            state = r.get("assessment", "No change")
            symbol = {"Improved": "🟢", "Regressed": "🔴", "No change": "⚪"}.get(state, "⚪")
            rows.append([r["model"], f'{r["previous"]:.2f}', f'{r["latest"]:.2f}',
                         f'{r["delta_pct"]:+.2f}%', symbol + " " + state])
        parts.append(f'<h3>{e(heading)}</h3>')
        parts.append(table(["Model", "Previous", "Latest", "Delta", "Assessment"], rows) if rows
                     else '<p>No comparable performance data.</p>')
    parts.append('<p>🟢 Improved · 🔴 Regressed · ⚪ No change (&lt;3%). '
                 'Higher throughput and lower latency are better. '
                 'Verify equivalent workloads before attributing regressions.</p>')
    for label, key in (("This weekly report", "exact_report_url"),
                       ("Latest report", "report_url"), ("Validation run", "latest_run_url")):
        link = str(n.get(key) or "")
        if link.startswith("https://github.com/"):
            parts.append(f'<p><a href="{e(link)}">{e(label)}</a></p>')
    parts.append('</body></html>')
    return "\n".join(parts)


def build_payloads(n: dict) -> list[dict]:
    """One Teams overview post, carrying full HTML for the email branch."""
    if not n.get("performance"):
        raise ValueError("Notification JSON lacks performance; rerun weekly report generation")
    overview = build_payload(n)
    overview["email_html"] = build_email_html(n)
    overview["date"] = n.get("date", "")
    overview["nightly_email_html"] = n.get("nightly_email_html", "")
    return [overview]


def send(payload: dict, url: str) -> None:
    if not url.startswith("https://"):
        raise ValueError("TEAMS_WEBHOOK_URL must be an HTTPS URL")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        try:
            req = request.Request(url, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
            with request.urlopen(req, timeout=30) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError("Teams webhook returned an unexpected response")
            print("Teams webhook accepted weekly report notification")
            return
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            retryable = not isinstance(exc, error.HTTPError) or exc.code in (429, 500, 502, 503, 504)
            if attempt == 2 or not retryable:
                # Never expose the signed webhook URL in logs or exceptions.
                raise RuntimeError("Teams webhook notification failed; check Power Automate run history") from None
            time.sleep(2 ** attempt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notification", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Print card JSON; do not POST")
    args = parser.parse_args()
    notification = json.loads(args.notification.read_text(encoding="utf-8"))
    payloads = build_payloads(notification)
    if args.dry_run:
        print(json.dumps(payloads, indent=2, ensure_ascii=False))
        return
    url = os.environ.get("TEAMS_WEBHOOK_URL", "")
    for payload in payloads:
        send(payload, url)
    print("Posted Teams overview card with email HTML")


if __name__ == "__main__":
    main()
