#!/usr/bin/env python3
"""Summarize completed scheduled nightly validation artifacts in a weekly window."""
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', type=Path, required=True, help='JSON list of {id,url,created_at,event,summary}')
    p.add_argument('--start', required=True, help='UTC ISO timestamp, inclusive')
    p.add_argument('--end', required=True, help='UTC ISO timestamp, exclusive')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--json-output', type=Path, help='Optional machine-readable summary for notifications')
    a = p.parse_args()
    start, end = parse_time(a.start), parse_time(a.end)
    entries = json.loads(a.manifest.read_text())
    entries = [x for x in entries if x.get('event') == 'schedule' and start <= parse_time(x['created_at']) < end]
    entries.sort(key=lambda x: x['created_at'])
    model_stats = defaultdict(lambda: {'pass': 0, 'fail': 0, 'other': 0})
    lines = ['## Nightly validation summary', '', f'**Window (UTC):** {start.isoformat()} to {end.isoformat()} (end exclusive)', '',
             f'**Scheduled nightly runs:** {len(entries)}', '',
             '| Date (UTC) | Run | Tested | Passed | Failed |', '| --- | --- | ---: | ---: | ---: |']
    totals = [0, 0, 0]
    missing = 0
    daily = []
    for x in entries:
        url = x.get('url', '')
        label = f"[{x['id']}]({url})" if url.startswith('https://github.com/') else str(x['id'])
        file = Path(x['summary']) if x.get('summary') else None
        if not file or not file.is_file():
            missing += 1
            daily.append({'date': x['created_at'][:10], 'url': url, 'run_id': str(x['id']), 'status': 'missing'})
            lines.append(f"| {x['created_at'][:10]} | {label} | — | — | Missing artifact |")
            continue
        try:
            data = json.loads(file.read_text())
        except (ValueError, OSError):
            missing += 1
            daily.append({'date': x['created_at'][:10], 'url': url, 'run_id': str(x['id']), 'status': 'invalid'})
            lines.append(f"| {x['created_at'][:10]} | {label} | — | — | Invalid artifact |")
            continue
        values = [int(data.get(k, 0)) for k in ('tested', 'passed', 'failed')]
        totals = [a + b for a, b in zip(totals, values)]
        daily.append({'date': x['created_at'][:10], 'url': url, 'run_id': str(x['id']),
                      'status': 'available', 'tested': values[0], 'passed': values[1], 'failed': values[2]})
        lines.append(f"| {x['created_at'][:10]} | {label} | {values[0]} | {values[1]} | {values[2]} |")
        for m in data.get('models', []):
            name = m.get('model')
            if name:
                status = m.get('status', '').upper()
                model_stats[name]['pass' if status == 'PASS' else 'fail' if status == 'FAIL' else 'other'] += 1
    if not entries:
        lines.append('| — | — | — | — | No scheduled nightly runs found |')
    lines += ['', f'**Model executions:** {totals[0]} tested, {totals[1]} passed, {totals[2]} failed.',
              f'**Missing/unreadable summaries:** {missing}.', '',
              '### Models with nightly failures', '',
              '| Model | Failed / Tested nights | Passed nights |', '| --- | ---: | ---: |']
    failed_models = [(name, s) for name, s in model_stats.items() if s['fail']]
    for name, s in sorted(failed_models, key=lambda item: (-item[1]['fail'], item[0])):
        lines.append(f"| `{name.replace('|', chr(92)+'|')}` | {s['fail']}/{sum(s.values())} | {s['pass']} |")
    if not failed_models:
        lines.append('| — | — | No recorded model failures |')
    lines += ['', '_Failure counts reflect model executions, not unique failed models. Missing summaries are not treated as passes._', '']
    a.output.write_text('\n'.join(lines) + '\n')
    if a.json_output:
        report = {
            'start': start.isoformat(), 'end': end.isoformat(),
            'scheduled_runs': len(entries), 'missing_summaries': missing,
            'tested': totals[0], 'passed': totals[1], 'failed': totals[2],
            'runs': daily,
            'failed_models': [
                {'model': name, 'failed_nights': s['fail'],
                 'tested_nights': sum(s.values()), 'passed_nights': s['pass']}
                for name, s in sorted(failed_models, key=lambda item: (-item[1]['fail'], item[0]))
            ],
        }
        a.json_output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
