#!/usr/bin/env python3
"""Pull sensor history from Home Assistant into data/history.json.

Usage:
    python3 fetch_history.py [--days N] [--mock]

Auth: reads HASS_TOKEN (and optional HASS_URL) from environment or a .env
file next to this script. Create a long-lived access token in Home Assistant
under Profile -> Security -> Long-lived access tokens.

--mock writes synthetic-but-realistic data so the dashboard can be built and
reviewed before a token exists.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from entities import CLIMATE_ENTITIES, POWER_AC_SENSORS, SOLAR_SENSORS, TEMPERATURE_SENSORS, WIND_SENSORS
except ImportError:
    raise SystemExit(
        'No entities.py found. It describes YOUR house, so it lives outside git:\n'
        '  cp entities.example.py entities.py\n'
        'then edit the friendly names to match your Home Assistant sensors.'
    )

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / 'data' / 'history.json'
LOCAL_TZ = ZoneInfo('America/Los_Angeles')
BUCKET_MINUTES = 10
DEFAULT_URL = 'http://homeassistant.local:8123'


def load_env():
    env = {}
    env_file = ROOT / '.env'
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                env[key.strip()] = value.strip().strip('"').strip("'")
    import os
    env.update({k: v for k, v in os.environ.items() if k.startswith('HASS_')})
    return env


def api_get(base_url, token, path, params=None):
    url = base_url.rstrip('/') + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raise SystemExit(f'HA API error {err.code} on {path}: {err.read().decode()[:200]}')
    except urllib.error.URLError as err:
        raise SystemExit(f'Cannot reach Home Assistant at {base_url}: {err.reason}')


def resolve_entity_ids(states, wanted):
    """Map friendly names -> entity_ids. Exact match first, then unique substring."""
    by_name = {}
    for state in states:
        name = state.get('attributes', {}).get('friendly_name', '')
        by_name.setdefault(name, []).append(state['entity_id'])
    resolved, missing = {}, []
    for key, spec in wanted.items():
        target = spec['match']
        ids = by_name.get(target, [])
        if not ids:
            ids = [eid for name, eids in by_name.items() if target.lower() in name.lower() for eid in eids]
        if len(ids) > 1:
            sensors_only = [eid for eid in ids if eid.startswith('sensor.')]
            if len(sensors_only) == 1:
                ids = sensors_only
        if len(ids) == 1:
            resolved[key] = ids[0]
        else:
            missing.append((key, target, ids))
    return resolved, missing


def bucket_points(raw, start, end):
    """Downsample state history to BUCKET_MINUTES means. Returns [[epoch_s, value], ...]."""
    buckets = {}
    for item in raw:
        try:
            value = float(item['state'])
        except (KeyError, TypeError, ValueError):
            continue
        ts = datetime.fromisoformat(item['last_changed'].replace('Z', '+00:00'))
        if not (start <= ts <= end):
            continue
        slot = int(ts.timestamp() // (BUCKET_MINUTES * 60))
        buckets.setdefault(slot, []).append(value)
    return [
        [slot * BUCKET_MINUTES * 60, round(sum(vals) / len(vals), 2)]
        for slot, vals in sorted(buckets.items())
    ]


def climate_segments(raw, start, end):
    """Collapse climate state history to [[start_s, end_s, state], ...] for active states."""
    segments = []
    current_state, seg_start = None, None
    for item in raw:
        state = item.get('state')
        ts = datetime.fromisoformat(item['last_changed'].replace('Z', '+00:00'))
        if state == current_state:
            continue
        if current_state not in (None, 'off', 'unavailable', 'unknown'):
            segments.append([int(seg_start.timestamp()), int(ts.timestamp()), current_state])
        current_state, seg_start = state, ts
    if current_state not in (None, 'off', 'unavailable', 'unknown') and seg_start:
        segments.append([int(seg_start.timestamp()), int(end.timestamp()), current_state])
    return segments


def energy_daily_to_power(raw, start, end):
    """Derive average watts per bucket from a cumulative daily-kWh sensor.

    The sensor climbs through the day and resets at midnight; negative diffs
    (resets) and gaps are skipped. Returns [[epoch_s, watts], ...].
    """
    bucket_s = BUCKET_MINUTES * 60
    buckets = {}
    for item in raw:
        try:
            value = float(item['state'])
        except (KeyError, TypeError, ValueError):
            continue
        ts = datetime.fromisoformat(item['last_changed'].replace('Z', '+00:00'))
        if not (start <= ts <= end):
            continue
        slot = int(ts.timestamp() // bucket_s)
        buckets[slot] = max(buckets.get(slot, 0.0), value)
    points = []
    ordered = sorted(buckets.items())
    for (slot_a, kwh_a), (slot_b, kwh_b) in zip(ordered, ordered[1:]):
        diff = kwh_b - kwh_a
        gap_hours = (slot_b - slot_a) * bucket_s / 3600
        if diff >= 0 and gap_hours <= 1:
            points.append([slot_b * bucket_s, round(diff * 1000 / gap_hours, 1)])
    return points


def power_segments(points, threshold_w, bridge_s=1800):
    """Runs of sustained draw above threshold, bridging short compressor-cycle gaps."""
    bucket_s = BUCKET_MINUTES * 60
    segments = []
    current = None
    for ts, watts in points:
        if watts >= threshold_w:
            if current and ts - current[1] <= bridge_s:
                current[1] = ts + bucket_s
            else:
                if current:
                    segments.append(current)
                current = [ts, ts + bucket_s]
    if current:
        segments.append(current)
    return [[a, b, 'cool'] for a, b in segments]


def fetch(days):
    env = load_env()
    token = env.get('HASS_TOKEN')
    base_url = env.get('HASS_URL', DEFAULT_URL)
    if not token:
        raise SystemExit(
            'No HASS_TOKEN found. Create a long-lived access token in Home Assistant\n'
            '(Profile -> Security -> Long-lived access tokens) and save it:\n'
            f'  echo "HASS_TOKEN=<token>" > {ROOT / ".env"}'
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    print(f'Fetching {days}d of history from {base_url} ...')

    states = api_get(base_url, token, '/api/states')
    all_wanted = {**TEMPERATURE_SENSORS, **SOLAR_SENSORS, **WIND_SENSORS, **CLIMATE_ENTITIES}
    resolved, missing = resolve_entity_ids(states, all_wanted)
    for key, target, ids in missing:
        print(f'  WARN: could not uniquely resolve "{target}" ({key}): {ids or "no match"}', file=sys.stderr)

    def history_for(entity_id, minimal=True):
        params = {'filter_entity_id': entity_id, 'end_time': end.isoformat()}
        if minimal:
            params['minimal_response'] = ''
            params['no_attributes'] = ''
        path = f'/api/history/period/{urllib.parse.quote(start.isoformat())}'
        result = api_get(base_url, token, path, params)
        return result[0] if result else []

    series = {}
    for key, spec in {**TEMPERATURE_SENSORS, **SOLAR_SENSORS, **WIND_SENSORS}.items():
        if key not in resolved:
            continue
        raw = history_for(resolved[key])
        points = bucket_points(raw, start, end)
        series[key] = {
            'label': spec['label'],
            'group': spec.get('group', 'solar'),
            'unit': spec.get('unit', '°F'),
            'ac': spec.get('ac'),
            'points': points,
        }
        print(f'  {spec["label"]}: {len(points)} points')

    climate = {}
    for key, spec in CLIMATE_ENTITIES.items():
        if key not in resolved:
            continue
        raw = history_for(resolved[key], minimal=False)
        segs = climate_segments(raw, start, end)
        climate[key] = {'label': spec['label'], 'segments': segs}
        print(f'  {spec["label"]}: {len(segs)} run segments')

    known_ids = {s['entity_id'] for s in states}
    acpower = {}
    for key, spec in POWER_AC_SENSORS.items():
        # 'entity_ids' (list) splices history across renames: an integration
        # rename mints a new entity id and strands the old history under the
        # old one. Same physical plug, same cumulative counter, so the raw
        # streams concatenate cleanly.
        ids = spec.get('entity_ids') or [spec['entity_id']]
        found = [eid for eid in ids if eid in known_ids]
        raw = []
        for eid in ids:
            raw.extend(history_for(eid))
        if not raw and not found:
            print(f'  WARN: none of {ids} found, skipping {spec["label"]}', file=sys.stderr)
            continue
        power = energy_daily_to_power(raw, start, end)
        segs = power_segments(power, spec['threshold_w'])
        climate[key] = {'label': spec['label'], 'segments': segs}
        acpower[key] = {'label': spec['label'], 'points': power}
        print(f'  {spec["label"]}: {len(segs)} run segments (power-derived, {len(ids)} id(s))')

    return build_payload(start, end, series, climate, acpower)


def build_payload(start, end, series, climate, acpower):
    return {
        'generated_at': datetime.now(LOCAL_TZ).isoformat(),
        'timezone': 'America/Los_Angeles',
        'range': {'start': int(start.timestamp()), 'end': int(end.timestamp())},
        'series': series,
        'climate': climate,
        'acpower': acpower,
    }


def mock(days):
    """Synthetic data with the physics we expect: outdoor diurnal sine, solar bell,
    attic amplified + lagged, rooms damped, AC pulling rooms down in the evening."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    step = BUCKET_MINUTES * 60
    t0, t1 = int(start.timestamp()), int(end.timestamp())

    def day_frac(ts):
        local = datetime.fromtimestamp(ts, LOCAL_TZ)
        return (local.hour + local.minute / 60) / 24

    def outdoor(ts):
        return 68 + 12 * math.sin(2 * math.pi * (day_frac(ts) - 0.375))

    def solar(ts):
        frac = day_frac(ts)
        if 0.25 < frac < 0.83:
            return round(6.5 * math.sin(math.pi * (frac - 0.25) / 0.58) ** 2, 3)
        return 0.0

    def attic(ts):
        lag = 5400
        return outdoor(ts - lag) + 2 + 5.2 * solar(ts - lag)

    def room(ts, damp, offset, ac_key=None):
        base = 71 + offset + (outdoor(ts - 9000) - 68) * damp + solar(ts - 10800) * damp * 1.5
        if ac_key and 0.58 < day_frac(ts) < 0.95:
            base -= 3.5
        return base

    timestamps = list(range(t0 - t0 % step, t1, step))
    series = {}
    # generators are derived from whatever layout entities.py declares, so
    # --mock works for any house: drivers get the canonical curves, rooms get
    # progressively damped/offset variants, reference zones track outdoor
    room_i = 0
    for key, spec in TEMPERATURE_SENSORS.items():
        if key == 'outdoor':
            gen = outdoor
        elif key == 'attic':
            gen = attic
        elif spec.get('group') == 'reference':
            gen = lambda ts: outdoor(ts - 3600) + 6
        else:
            damp = 0.35 + (room_i % 6) * 0.05
            offset = (room_i % 5) * 1.0
            ac_key = spec.get('ac')
            gen = lambda ts, d=damp, o=offset, a=ac_key: room(ts, d, o, a)
            room_i += 1
        series[key] = {
            'label': spec['label'], 'group': spec['group'], 'unit': '°F',
            'ac': spec.get('ac'),
            'points': [[ts, round(gen(ts) + math.sin(ts / 977) * 0.4, 2)] for ts in timestamps],
        }
    series['solar_power'] = {
        'label': 'Solar production', 'group': 'solar', 'unit': 'kW', 'ac': None,
        'points': [[ts, solar(ts)] for ts in timestamps],
    }
    series['wind_speed'] = {
        'label': 'Wind speed', 'group': 'wind', 'unit': 'mph', 'ac': None,
        'points': [[ts, round(max(0.0, 3 + 4 * math.sin(2 * math.pi * (day_frac(ts) - 0.5)) + math.sin(ts / 733) * 1.5), 2)] for ts in timestamps],
    }

    # every AC unit the layout references gets afternoon-to-evening run segments;
    # units declared in POWER_AC_SENSORS also get a synthetic power trace
    climate, acpower = {}, {}
    ac_labels = {k: s['label'] for k, s in {**CLIMATE_ENTITIES, **POWER_AC_SENSORS}.items()}
    for key, spec in TEMPERATURE_SENSORS.items():
        ac = spec.get('ac')
        if ac and ac not in ac_labels:
            ac_labels[ac] = spec['label'] + ' AC'
    for key, label in ac_labels.items():
        segs = []
        day = datetime.fromtimestamp(t0, LOCAL_TZ).replace(hour=14, minute=0, second=0, microsecond=0)
        while day.timestamp() < t1:
            seg_start = int(day.timestamp())
            segs.append([seg_start, seg_start + 8 * 3600, 'cool'])
            day += timedelta(days=1)
        climate[key] = {'label': label, 'segments': segs}
        if key in POWER_AC_SENSORS:
            acpower[key] = {'label': label, 'points': [
                [ts, 520.0 if any(a <= ts <= b for a, b, _ in segs) else 0.0] for ts in timestamps
            ]}

    return build_payload(start, end, series, climate, acpower)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=10, help='days of history (recorder default keeps 10)')
    parser.add_argument('--mock', action='store_true', help='generate synthetic data instead of fetching')
    args = parser.parse_args()

    payload = mock(args.days) if args.mock else fetch(args.days)
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload))
    kind = 'mock' if args.mock else 'live'
    print(f'Wrote {kind} data -> {DATA_FILE} ({DATA_FILE.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main()
