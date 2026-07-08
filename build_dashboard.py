#!/usr/bin/env python3
"""Inject data/history.json into dashboard_template.html -> dashboard.html."""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    data_file = ROOT / 'data' / 'history.json'
    if not data_file.exists():
        raise SystemExit('No data/history.json — run fetch_history.py first (or with --mock).')
    payload = json.loads(data_file.read_text())

    template = (ROOT / 'dashboard_template.html').read_text()
    generated = datetime.fromisoformat(payload['generated_at']).strftime('%b %-d, %Y %-I:%M %p')
    html = template.replace('__DATA_JSON__', json.dumps(payload, separators=(',', ':')))
    html = html.replace('__GENERATED_AT__', generated)

    out = ROOT / 'dashboard.html'
    out.write_text(html)
    print(f'Wrote {out} ({out.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main()
