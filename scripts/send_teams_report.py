#!/usr/bin/env python3
"""Post the existing weekly report notification as a Teams Workflow Adaptive Card."""
from __future__ import annotations

import argparse
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
                "Throughput, P99 TTFT and P99 TPOT comparisons are in the full report. "
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
    payload = build_payload(notification)
    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    send(payload, os.environ.get("TEAMS_WEBHOOK_URL", ""))


if __name__ == "__main__":
    main()
