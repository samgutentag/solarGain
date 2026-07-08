#!/usr/bin/env python3
"""Build the shareable product card from real data.

Reads data/history.json (run fetch_history.py first), takes the last three
complete local days, and writes docs/product-card.html with the measured
curves and stats baked in. Screenshot the #card element at 1080x1350.
"""

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / 'data' / 'history.json').read_text())
TZ = ZoneInfo(DATA['timezone'])
OUT = ROOT / 'docs' / 'product-card.html'

BUCKET_S = 600
DAYS = 3


def local_day(ts):
    return datetime.fromtimestamp(ts, TZ).date()


def day_start(date):
    return int(datetime(date.year, date.month, date.day, tzinfo=TZ).timestamp())


# ---- window: last DAYS complete local days -----------------------------------
end_dt = datetime.fromtimestamp(DATA['range']['end'], TZ)
last_complete = end_dt.date() - timedelta(days=1)
w_start = day_start(last_complete - timedelta(days=DAYS - 1))
w_end = day_start(last_complete + timedelta(days=1))


def clip(key):
    return [p for p in DATA['series'][key]['points'] if w_start <= p[0] < w_end]


outdoor = clip('outdoor')
attic = clip('attic')
solar = clip('solar_power')
room_keys = [k for k, s in DATA['series'].items() if s.get('group') == 'room']

# ---- stats -------------------------------------------------------------------
attic_by_t = dict(attic)
out_by_t = dict(outdoor)

attic_peak = max(attic, key=lambda p: p[1])
outdoor_peak = max(outdoor, key=lambda p: p[1])
max_delta = max(((t, v - out_by_t[t]) for t, v in attic if t in out_by_t), key=lambda p: p[1])

# median per-day lag between solar peak and attic peak
lags = []
for d in range(DAYS):
    ds = w_start + d * 86400
    day_solar = [p for p in solar if ds <= p[0] < ds + 86400]
    day_attic = [p for p in attic if ds <= p[0] < ds + 86400]
    if not day_solar or not day_attic:
        continue
    sp = max(day_solar, key=lambda p: p[1])
    ap = max(day_attic, key=lambda p: p[1])
    if sp[1] > 1 and 0 < ap[0] - sp[0] < 8 * 3600:
        lags.append(ap[0] - sp[0])
median_lag = statistics.median(lags) if lags else None

# AC energy across every metered unit, and overlap with panel production
solar_by_t = dict(solar)
ac_kwh = ac_kwh_solar = 0.0
for unit in DATA.get('acpower', {}).values():
    for t, watts in unit['points']:
        if not (w_start <= t < w_end):
            continue
        inc = watts / 1000 * (BUCKET_S / 3600)
        ac_kwh += inc
        if solar_by_t.get(t, 0) > 0.5:
            ac_kwh_solar += inc

# evening crossover: first time after 4 PM when outdoor < mean room temp
room_avg = {}
for key in room_keys:
    for t, v in DATA['series'][key]['points']:
        if w_start <= t < w_end:
            s, n = room_avg.get(t, (0.0, 0))
            room_avg[t] = (s + v, n + 1)
cross_secs = []
for d in range(DAYS):
    ds = w_start + d * 86400
    for t, v in outdoor:
        if ds + 16 * 3600 <= t < ds + 86400 and t in room_avg:
            s, n = room_avg[t]
            if v < s / n:
                cross_secs.append(t - ds)
                break
median_cross = statistics.median(cross_secs) if cross_secs else None

# ---- svg geometry -------------------------------------------------------------
X0, X1 = 60, 980


def x_of(t):
    return X0 + (t - w_start) / (w_end - w_start) * (X1 - X0)


def temp_paths(points, y_of, gap_s=3600):
    """Polyline path(s), broken at gaps, as one d string with M segments."""
    d, prev = [], None
    for t, v in points:
        cmd = 'M' if prev is None or t - prev > gap_s else 'L'
        d.append(f'{cmd}{x_of(t):.1f},{y_of(v):.1f}')
        prev = t
    return ' '.join(d)


t_lo = min(min(v for _, v in outdoor), min(v for _, v in attic))
t_hi = max(max(v for _, v in outdoor), max(v for _, v in attic))
# top margin keeps the hottest curve clear of the in-chart legend rows
T_TOP, T_BOT = 84, 386


def ty(v):
    return T_BOT - (v - t_lo) / (t_hi - t_lo) * (T_BOT - T_TOP)


# ribbon: attic over outdoor, on the shared bucket grid
common = sorted(t for t in attic_by_t if t in out_by_t)
ribbon = ' '.join(f'L{x_of(t):.1f},{ty(attic_by_t[t]):.1f}' for t in common)
ribbon_back = ' '.join(f'L{x_of(t):.1f},{ty(out_by_t[t]):.1f}' for t in reversed(common))
ribbon_d = 'M' + ribbon[1:] + ' ' + ribbon_back + ' Z'

s_hi = max(v for _, v in solar) or 1
S_TOP, S_BOT = 14, 128


def sy(v):
    return S_BOT - v / s_hi * (S_BOT - S_TOP)


# solar area: fill missing buckets with 0 so nights sit on the baseline
grid = range(w_start, w_end, BUCKET_S)
solar_area = 'M' + ' L'.join(f'{x_of(t):.1f},{sy(solar_by_t.get(t, 0)):.1f}' for t in grid)
solar_d = solar_area + f' L{X1},{S_BOT} L{X0},{S_BOT} Z'

# temp y ticks: every 10 F
t_ticks = [v for v in range(int(t_lo // 10 * 10), int(t_hi) + 10, 10) if t_lo <= v <= t_hi]

# x gridlines at midnights, day labels at noons
midnights = [w_start + d * 86400 for d in range(1, DAYS)]
noons = [(w_start + d * 86400 + 43200,
          datetime.fromtimestamp(w_start + d * 86400, TZ).strftime('%a %b %-d')) for d in range(DAYS)]


def fmt_clock(secs):
    h, m = int(secs // 3600), int(secs % 3600 // 60)
    ampm = 'AM' if h < 12 else 'PM'
    return f'{(h - 1) % 12 + 1}:{m:02d} {ampm}'


def fmt_lag(secs):
    return f'{int(secs // 3600)}h {int(secs % 3600 // 60):02d}m'


stat_gap = f'+{max_delta[1]:.0f}'
stat_lag = fmt_lag(median_lag) if median_lag else 'n/a'
stat_kwh = f'{ac_kwh:.0f}'
stat_solar_pct = round(ac_kwh_solar / ac_kwh * 100) if ac_kwh else 0
stat_cross = fmt_clock(median_cross) if median_cross else 'n/a'
peak_label = f'{attic_peak[1]:.1f}&#176;F'
out_peak_label = f'{outdoor_peak[1]:.1f}&#176;F'
px, py = x_of(attic_peak[0]), ty(attic_peak[1])

t_grid = '\n'.join(
    f'        <line x1="{X0}" y1="{ty(v):.1f}" x2="{X1}" y2="{ty(v):.1f}"/>' for v in t_ticks)
t_tick_labels = '\n'.join(
    f'        <text x="{X0 - 8}" y="{ty(v) + 7:.1f}">{v}&#176;</text>' for v in t_ticks)
mid_lines_t = '\n'.join(
    f'        <line x1="{x_of(t):.1f}" y1="{T_TOP}" x2="{x_of(t):.1f}" y2="{T_BOT}"/>' for t in midnights)
mid_lines_s = '\n'.join(
    f'        <line x1="{x_of(t):.1f}" y1="{S_TOP}" x2="{x_of(t):.1f}" y2="{S_BOT}"/>' for t in midnights)
day_labels = '\n'.join(
    f'        <text x="{x_of(t):.1f}" y="158" text-anchor="middle">{label}</text>' for t, label in noons)

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>solarGain product card</title>
<style>
  /* 1080 x 1350 share card, generated by docs/build_card.py. Screenshot #card. */
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0e1320; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; display: flex; justify-content: center; }}
  #card {{
    width: 1080px; height: 1350px; overflow: hidden;
    background: #0e1320; color: #e8ecf4;
    display: flex; flex-direction: column;
    padding: 52px 64px 40px;
  }}
  .brand {{ display: flex; align-items: baseline; gap: 18px; }}
  .brand h1 {{ font-size: 68px; font-weight: 800; letter-spacing: -1px; }}
  .brand h1 .gain {{ color: #f5b942; }}
  .sun {{ font-size: 48px; }}
  .tagline {{ margin-top: 8px; font-size: 29px; font-weight: 500; color: #aab4c6; }}
  .chart-panel {{
    margin-top: 24px;
    background: #161d2e;
    border: 1px solid #2a3550;
    border-radius: 20px;
    padding: 22px 26px 10px;
  }}
  .chart-title {{ font-size: 24px; font-weight: 700; color: #cdd6e4; margin-bottom: 2px; }}
  .chart-title .hint {{ font-weight: 500; color: #8a94a8; }}
  .chart-panel svg {{ display: block; margin: 0 auto; }}
  .solar-title {{ margin-top: 6px; }}
  .stats {{ margin-top: 22px; display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .stat {{
    background: #161d2e; border: 1px solid #2a3550; border-radius: 16px;
    padding: 18px 24px;
  }}
  .stat .v {{ font-size: 44px; font-weight: 800; color: #fff; }}
  .stat .v .unit {{ font-size: 27px; font-weight: 700; color: #aab4c6; }}
  .stat .l {{ margin-top: 3px; font-size: 20.5px; color: #aab4c6; line-height: 1.3; }}
  .footer {{ margin-top: auto; display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; }}
  .footer .how {{ font-size: 20.5px; color: #8a94a8; line-height: 1.45; max-width: 660px; }}
  .footer .how b {{ color: #cdd6e4; font-weight: 600; }}
  .repo {{ font-size: 22px; font-weight: 600; color: #f5b942; white-space: nowrap; }}
</style>
</head>
<body>
<div id="card">
  <div class="brand"><span class="sun">&#9728;&#65039;</span><h1>solar<span class="gain">Gain</span></h1></div>
  <div class="tagline">How much of your AC bill is really the roof? Measure it with the sensors you already own.</div>

  <div class="chart-panel">
    <div class="chart-title">Solar heat gain <span class="hint">&#183; the attic vs the outside air, last three days (measured)</span></div>
    <svg viewBox="0 0 1000 400" width="912" height="365" role="img" aria-label="Three days of measured temperatures. The outdoor line (dotted) and the attic line (solid) rise together each morning, but the attic climbs far higher every afternoon; the shaded gap between them, labeled solar heat gain stored overhead, peaks at {peak_label} in the attic.">
      <g stroke="#242e46" stroke-width="1">
{t_grid}
{mid_lines_t}
      </g>
      <g font-size="19" fill="#6b7690" text-anchor="end">
{t_tick_labels}
      </g>
      <path d="{ribbon_d}" fill="#e34948" opacity="0.16"/>
      <path d="{temp_paths(outdoor, ty)}" fill="none" stroke="#5aa0f2" stroke-width="4" stroke-dasharray="2 12" stroke-linecap="round"/>
      <path d="{temp_paths(attic, ty)}" fill="none" stroke="#f07040" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="{px:.1f}" cy="{py:.1f}" r="8" fill="#f07040" stroke="#0e1320" stroke-width="3"/>
      <text x="{px:.1f}" y="{py - 16:.1f}" font-size="24" font-weight="700" fill="#f07040" text-anchor="middle">{peak_label}</text>
      <!-- in-chart legend, top left -->
      <g font-size="21" font-weight="600">
        <line x1="{X0 + 14}" y1="24" x2="{X0 + 58}" y2="24" stroke="#f07040" stroke-width="5" stroke-linecap="round"/>
        <text x="{X0 + 68}" y="31" fill="#f07040">Attic (solid)</text>
        <line x1="{X0 + 234}" y1="24" x2="{X0 + 278}" y2="24" stroke="#5aa0f2" stroke-width="4" stroke-dasharray="2 12" stroke-linecap="round"/>
        <text x="{X0 + 288}" y="31" fill="#5aa0f2">Outdoor (dotted), peaked {out_peak_label}</text>
        <rect x="{X0 + 14}" y="52" width="44" height="16" fill="#e34948" opacity="0.3"/>
        <text x="{X0 + 68}" y="66" fill="#ef9b9b">shaded gap: solar heat stored overhead</text>
      </g>
    </svg>
  </div>

  <div class="chart-panel solar-title">
    <div class="chart-title">Solar panel production <span class="hint">&#183; electricity, used here as the sunshine meter</span></div>
    <svg viewBox="0 0 1000 170" width="912" height="155" role="img" aria-label="Three days of solar panel output in kilowatts, one bell curve per day, time-aligned with the temperature chart above.">
      <g stroke="#242e46" stroke-width="1">
        <line x1="{X0}" y1="{S_BOT}" x2="{X1}" y2="{S_BOT}"/>
{mid_lines_s}
      </g>
      <g font-size="19" fill="#6b7690" text-anchor="end">
        <text x="{X0 - 8}" y="{S_BOT + 6}">0</text>
        <text x="{X0 - 8}" y="{sy(s_hi) + 14:.1f}">{s_hi:.0f} kW</text>
      </g>
      <path d="{solar_d}" fill="#f5b942" opacity="0.55"/>
      <path d="{solar_area}" fill="none" stroke="#f5b942" stroke-width="2.5" stroke-linejoin="round"/>
      <g font-size="19" fill="#6b7690">
{day_labels}
      </g>
    </svg>
  </div>

  <div class="stats">
    <div class="stat"><div class="v">{stat_gap}<span class="unit">&#176;F</span></div><div class="l">hottest the attic ran above the outside air</div></div>
    <div class="stat"><div class="v">{stat_lag}</div><div class="l">sunshine peaks first; the attic peaks this much later, then radiates into the evening</div></div>
    <div class="stat"><div class="v">{stat_kwh}<span class="unit"> kWh</span></div><div class="l">of AC in three days, {stat_solar_pct}% of it while the panels were producing</div></div>
    <div class="stat"><div class="v">{stat_cross}</div><div class="l">when outdoor air finally drops below the rooms and open windows start to work</div></div>
  </div>

  <div class="footer">
    <div class="how"><b>Same sunshine, two fates:</b> the panels turn it into electricity, the roof turns it into attic heat that bridges through the ceiling into the rooms all evening. Built on Home Assistant history; one self-contained dashboard.</div>
    <div class="repo">github.com/samgutentag/solarGain</div>
  </div>
</div>
</body>
</html>
"""

OUT.write_text(html)
window = f"{datetime.fromtimestamp(w_start, TZ):%b %-d} to {datetime.fromtimestamp(w_end - 1, TZ):%b %-d}"
print(f'Wrote {OUT} ({OUT.stat().st_size // 1024} KB), window {window}')
print(f'  attic peak {attic_peak[1]:.1f}F, outdoor peak {outdoor_peak[1]:.1f}F, max gap +{max_delta[1]:.1f}F')
print(f'  median lag {stat_lag}, AC {ac_kwh:.1f} kWh ({stat_solar_pct}% solar), crossover {stat_cross}')
