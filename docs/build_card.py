#!/usr/bin/env python3
"""Build the shareable product card from real data.

Reads data/history.json (run fetch_history.py first) and writes
docs/product-card.html: three measured days of attic vs outdoor up top,
then the ridge-vent and R-38 scenario charts fitted exactly the way the
dashboard fits them. Screenshot the #card element at 1080x1350.
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

BUCKET = 600
DAYS = 3

series = DATA['series']
out_by_t = dict(series['outdoor']['points'])
attic_by_t = dict(series['attic']['points'])
solar_by_t = dict(series['solar_power']['points'])


def day_start(date):
    return int(datetime(date.year, date.month, date.day, tzinfo=TZ).timestamp())


def local_hour(ts):
    return datetime.fromtimestamp(ts, TZ).hour


# ---- window: last DAYS complete local days -----------------------------------
end_dt = datetime.fromtimestamp(DATA['range']['end'], TZ)
last_complete = end_dt.date() - timedelta(days=1)
w_start = day_start(last_complete - timedelta(days=DAYS - 1))
w_end = day_start(last_complete + timedelta(days=1))


def clip(key, a, b):
    return [p for p in series[key]['points'] if a <= p[0] < b]


outdoor = clip('outdoor', w_start, w_end)
attic = clip('attic', w_start, w_end)
solar = clip('solar_power', w_start, w_end)

# ---- headline stats ------------------------------------------------------------
attic_peak = max(attic, key=lambda p: p[1])
max_delta = max(((t, v - out_by_t[t]) for t, v in attic if t in out_by_t), key=lambda p: p[1])

lags = []
for d in range(DAYS):
    ds = w_start + d * 86400
    day_solar = [p for p in solar if ds <= p[0] < ds + 86400]
    day_attic = [p for p in attic if ds <= p[0] < ds + 86400]
    if day_solar and day_attic:
        sp = max(day_solar, key=lambda p: p[1])
        ap = max(day_attic, key=lambda p: p[1])
        if sp[1] > 1 and 0 < ap[0] - sp[0] < 8 * 3600:
            lags.append(ap[0] - sp[0])
median_lag = statistics.median(lags) if lags else None

ac_kwh = ac_kwh_solar = 0.0
for unit in DATA.get('acpower', {}).values():
    for t, watts in unit['points']:
        if w_start <= t < w_end:
            inc = watts / 1000 * (BUCKET / 3600)
            ac_kwh += inc
            if solar_by_t.get(t, 0) > 0.5:
                ac_kwh_solar += inc
stat_solar_pct = round(ac_kwh_solar / ac_kwh * 100) if ac_kwh else 0

# ---- attic model (port of the dashboard's fitAtticModel) -----------------------
t0 = -(-DATA['range']['start'] // BUCKET) * BUCKET
grid = []
last_solar = 0.0
for t in range(t0, DATA['range']['end'] + 1, BUCKET):
    last_solar = solar_by_t.get(t, last_solar)
    grid.append((t, last_solar))


def ema_for(tau):
    alpha = BUCKET / tau
    ema, s = {}, 0.0
    for t, v in grid:
        s = alpha * v + (1 - alpha) * s
        ema[t] = s
    return ema


MODEL = None
for tau in range(1800, 28801, 1800):
    ema = ema_for(tau)
    xs, ys = [], []
    for t, av in attic_by_t.items():
        if t in out_by_t and t in ema:
            xs.append(ema[t])
            ys.append(av - out_by_t[t])
    if len(xs) < 50:
        continue
    n = len(xs)
    xm, ym = sum(xs) / n, sum(ys) / n
    sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    sxx = sum((x - xm) ** 2 for x in xs)
    syy = sum((y - ym) ** 2 for y in ys)
    if not sxx or not syy:
        continue
    b = sxy / sxx
    r2 = sxy * sxy / (sxx * syy)
    if MODEL is None or r2 > MODEL['r2']:
        MODEL = {'tau': tau, 'a': ym - b * xm, 'b': b, 'r2': r2, 'ema': ema}

# hottest local day inside the card window drives both scenario charts
hot_day = max(range(DAYS), key=lambda d: max(
    (v for t, v in attic if w_start + d * 86400 <= t < w_start + (d + 1) * 86400), default=-999))
dr_start = w_start + hot_day * 86400
dr_end = dr_start + 86400


def vent_scenario(k):
    ema = MODEL['ema'] if k == 0 else ema_for(MODEL['tau'] / (1 + k))
    return [
        [t, out_by_t[t] + (MODEL['a'] + MODEL['b'] * ema[t]) / (1 + k)]
        for t, _ in clip('attic', dr_start, dr_end) if t in out_by_t and t in ema
    ]


vent_fitted = vent_scenario(0)
vent_central = vent_scenario(1.0)
vent_low = vent_scenario(0.5)
vent_high = vent_scenario(1.5)
attic_day = [p for p in clip('attic', dr_start, dr_end) if p[0] in out_by_t]


def peak_of(pts):
    return max(v for _, v in pts)


def at_9pm(pts):
    vals = [v for t, v in pts if local_hour(t) == 21]
    return sum(vals) / len(vals) if vals else None


vent_peak_cut = peak_of(vent_fitted) - peak_of(vent_central)
vent_eve_from, vent_eve_to = at_9pm(vent_fitted), at_9pm(vent_central)

# ---- room model for R-38 (port of the dashboard's fitRoomModel) ---------------
ROOM = 'hallway_bathroom'
room_pts = series[ROOM]['points']
out_dense, delta_dense = [], []
last_out = last_delta = None
for t, _ in grid:
    if t in out_by_t:
        last_out = out_by_t[t]
        if t in attic_by_t:
            last_delta = attic_by_t[t] - out_by_t[t]
    out_dense.append((t, last_out))
    delta_dense.append((t, last_delta))

FIT = None
for tau in range(3600, 28801, 3600):
    alpha = BUCKET / tau
    ema_out, ema_delta = {}, {}
    so = sd = None
    for (t, ov), (_, dv) in zip(out_dense, delta_dense):
        if ov is not None:
            so = ov if so is None else alpha * ov + (1 - alpha) * so
            ema_out[t] = so
        if dv is not None:
            sd = dv if sd is None else alpha * dv + (1 - alpha) * sd
            ema_delta[t] = sd
    rows = [(ema_out[t], ema_delta[t], y) for t, y in room_pts if t in ema_out and t in ema_delta]
    if len(rows) < 100:
        continue
    n = len(rows)
    s1 = s2 = sy = s11 = s22 = s12 = s1y = s2y = syy = 0.0
    for x1, x2, y in rows:
        s1 += x1; s2 += x2; sy += y
        s11 += x1 * x1; s22 += x2 * x2; s12 += x1 * x2
        s1y += x1 * y; s2y += x2 * y; syy += y * y
    det = n * (s11 * s22 - s12 * s12) - s1 * (s1 * s22 - s12 * s2) + s2 * (s1 * s12 - s11 * s2)
    if abs(det) < 1e-9:
        continue
    p = (sy * (s11 * s22 - s12 * s12) - s1 * (s1y * s22 - s12 * s2y) + s2 * (s1y * s12 - s11 * s2y)) / det
    q = (n * (s1y * s22 - s12 * s2y) - sy * (s1 * s22 - s12 * s2) + s2 * (s1 * s2y - s1y * s2)) / det
    c = (n * (s11 * s2y - s1y * s12) - s1 * (s1 * s2y - s1y * s2) + sy * (s1 * s12 - s11 * s2)) / det
    sse = sum((y - (p + q * x1 + c * x2)) ** 2 for x1, x2, y in rows)
    sst = syy - sy * sy / n
    r2 = 1 - sse / sst if sst else 0
    if FIT is None or r2 > FIT['r2']:
        FIT = {'tau': tau, 'p': p, 'q': q, 'c': c, 'r2': r2, 'emaOut': ema_out, 'emaDelta': ema_delta}


def r38_scenario(r_now):
    factor = r_now / 38
    return [
        [t, FIT['p'] + FIT['q'] * FIT['emaOut'][t] + FIT['c'] * factor * FIT['emaDelta'][t]]
        for t, _ in clip(ROOM, dr_start, dr_end) if t in FIT['emaOut'] and t in FIT['emaDelta']
    ]


r38_central = r38_scenario(12)
r38_low = r38_scenario(15)
r38_high = r38_scenario(10)
room_day = clip(ROOM, dr_start, dr_end)
r38_peak_from, r38_peak_to = peak_of(room_day), peak_of(r38_central)
r38_eve_from, r38_eve_to = at_9pm(room_day), at_9pm(r38_central)

# ---- svg builders --------------------------------------------------------------


def build_panel(x0, x1, top, bot, w_a, w_b):
    """Returns (x_of, y_of factory) for a time window and temp domain."""
    def x_of(t):
        return x0 + (t - w_a) / (w_b - w_a) * (x1 - x0)
    return x_of


def path_of(points, x_of, y_of, gap_s=3600):
    d, prev = [], None
    for t, v in points:
        cmd = 'M' if prev is None or t - prev > gap_s else 'L'
        d.append(f'{cmd}{x_of(t):.1f},{y_of(v):.1f}')
        prev = t
    return ' '.join(d)


def band_of(upper, lower, x_of, y_of):
    fwd = ' '.join(f'L{x_of(t):.1f},{y_of(v):.1f}' for t, v in upper)
    back = ' '.join(f'L{x_of(t):.1f},{y_of(v):.1f}' for t, v in reversed(lower))
    return 'M' + fwd[1:] + ' ' + back + ' Z'


# ---- top panel: 3 days, attic vs outdoor ---------------------------------------
X0, X1 = 60, 980
T_TOP, T_BOT = 82, 348
t_lo = min(min(v for _, v in outdoor), min(v for _, v in attic))
t_hi = max(max(v for _, v in outdoor), max(v for _, v in attic))
x_main = build_panel(X0, X1, T_TOP, T_BOT, w_start, w_end)


def ty(v):
    return T_BOT - (v - t_lo) / (t_hi - t_lo) * (T_BOT - T_TOP)


common = sorted(t for t in attic_by_t if t in out_by_t and w_start <= t < w_end)
ribbon_d = ('M' + ' '.join(f'L{x_main(t):.1f},{ty(attic_by_t[t]):.1f}' for t in common)[1:] +
            ' ' + ' '.join(f'L{x_main(t):.1f},{ty(out_by_t[t]):.1f}' for t in reversed(common)) + ' Z')

t_ticks = [v for v in range(int(t_lo // 10 * 10), int(t_hi) + 10, 10) if t_lo <= v <= t_hi]
midnights = [w_start + d * 86400 for d in range(1, DAYS)]
noons = [(w_start + d * 86400 + 43200,
          datetime.fromtimestamp(w_start + d * 86400, TZ).strftime('%a %b %-d')) for d in range(DAYS)]
px, py = x_main(attic_peak[0]), ty(attic_peak[1])
peak_label = f'{attic_peak[1]:.1f}&#176;F'

t_grid = '\n'.join(f'        <line x1="{X0}" y1="{ty(v):.1f}" x2="{X1}" y2="{ty(v):.1f}"/>' for v in t_ticks)
t_tick_labels = '\n'.join(f'        <text x="{X0 - 8}" y="{ty(v) + 7:.1f}">{v}&#176;</text>' for v in t_ticks)
mid_lines = '\n'.join(f'        <line x1="{x_main(t):.1f}" y1="{T_TOP}" x2="{x_main(t):.1f}" y2="{T_BOT}"/>' for t in midnights)
day_labels = '\n'.join(f'        <text x="{x_main(t):.1f}" y="378" text-anchor="middle">{label}</text>' for t, label in noons)


# ---- scenario panels ------------------------------------------------------------
def scenario_svg(measured, central, low, high, m_color, mod_color, aria):
    sx0, sx1, s_top, s_bot = 46, 484, 16, 348
    all_v = ([v for _, v in measured] + [v for _, v in low] + [v for _, v in high] +
             [out_by_t[t] for t, _ in measured if t in out_by_t])
    lo, hi = min(all_v), max(all_v)
    x_of = build_panel(sx0, sx1, s_top, s_bot, dr_start, dr_end)

    def y_of(v):
        return s_bot - (v - lo) / (hi - lo) * (s_bot - s_top)

    ticks = [v for v in range(int(lo // 10 * 10), int(hi) + 10, 10) if lo <= v <= hi]
    grid_l = '\n'.join(f'      <line x1="{sx0}" y1="{y_of(v):.1f}" x2="{sx1}" y2="{y_of(v):.1f}"/>' for v in ticks)
    tick_l = '\n'.join(f'      <text x="{sx0 - 7}" y="{y_of(v) + 6:.1f}">{v}&#176;</text>' for v in ticks)
    hours = [(dr_start + 6 * 3600, '6 AM'), (dr_start + 12 * 3600, 'noon'), (dr_start + 18 * 3600, '6 PM')]
    hour_grid = '\n'.join(f'      <line x1="{x_of(t):.1f}" y1="{s_top}" x2="{x_of(t):.1f}" y2="{s_bot}"/>' for t, _ in hours)
    hour_l = '\n'.join(f'      <text x="{x_of(t):.1f}" y="374" text-anchor="middle">{label}</text>' for t, label in hours)
    outdoor_day = [p for p in clip('outdoor', dr_start, dr_end)]

    return f'''<svg viewBox="0 0 500 392" width="424" height="332" role="img" aria-label="{aria}">
      <g stroke="#242e46" stroke-width="1">
{grid_l}
{hour_grid}
      </g>
      <g font-size="19" fill="#6b7690" text-anchor="end">
{tick_l}
      </g>
      <path d="{band_of(low, high, x_of, y_of)}" fill="{mod_color}" opacity="0.18"/>
      <path d="{path_of(outdoor_day, x_of, y_of)}" fill="none" stroke="#5aa0f2" stroke-width="2.5" stroke-dasharray="2 10" stroke-linecap="round" opacity="0.65"/>
      <path d="{path_of(measured, x_of, y_of)}" fill="none" stroke="{m_color}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="{path_of(central, x_of, y_of)}" fill="none" stroke="{mod_color}" stroke-width="4" stroke-dasharray="13 9" stroke-linecap="round" stroke-linejoin="round"/>
      <g font-size="19" fill="#6b7690">
{hour_l}
      </g>
    </svg>'''


C_VENT = '#4ade80'
C_INSUL = '#a78bfa'
C_ATTIC = '#f07040'
C_ROOM = '#2dd4a7'

vent_svg = scenario_svg(
    attic_day, vent_central, vent_low, vent_high, C_ATTIC, C_VENT,
    f'Hottest day: the measured attic line vs the same day replayed with ridge and soffit venting; the modeled peak drops {vent_peak_cut:.1f} degrees and the 9 PM tail drops from {vent_eve_from:.1f} to {vent_eve_to:.1f}.')
insul_svg = scenario_svg(
    room_day, r38_central, r38_low, r38_high, C_ROOM, C_INSUL,
    f'Hottest day: the measured hallway bathroom vs the same day replayed with R-38 attic insulation; the peak drops from {r38_peak_from:.1f} to {r38_peak_to:.1f} degrees.')


def legend_row(items):
    spans = []
    for color, dash, label in items:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
        spans.append(
            f'<span class="li"><svg viewBox="0 0 40 10" width="34" height="9">'
            f'<line x1="2" y1="5" x2="38" y2="5" stroke="{color}" stroke-width="4"{dash_attr} stroke-linecap="round"/></svg>{label}</span>')
    return '<div class="mini-legend">' + ''.join(spans) + '</div>'


hot_day_name = datetime.fromtimestamp(dr_start, TZ).strftime('%A')

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
    padding: 46px 64px 34px;
  }}
  .brand {{ display: flex; align-items: baseline; gap: 16px; }}
  .brand h1 {{ font-size: 60px; font-weight: 800; letter-spacing: -1px; }}
  .brand h1 .gain {{ color: #f5b942; }}
  .sun {{ font-size: 42px; }}
  .chart-panel {{
    margin-top: 18px;
    background: #161d2e;
    border: 1px solid #2a3550;
    border-radius: 18px;
    padding: 20px 24px 10px;
  }}
  .chart-title {{ font-size: 23px; font-weight: 700; color: #cdd6e4; margin-bottom: 2px; }}
  .chart-title .hint {{ font-weight: 500; color: #8a94a8; }}
  .chart-panel svg {{ display: block; margin: 0 auto; }}
  .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .row .chart-panel {{ padding: 18px 20px 10px; }}
  .mini-legend {{ display: flex; gap: 18px; margin: 6px 0 2px; font-size: 17.5px; color: #aab4c6; }}
  .mini-legend .li {{ display: flex; align-items: center; gap: 7px; }}
  .mini-legend svg {{ flex: none; }}
  .verdict {{ font-size: 19px; color: #cdd6e4; margin: 6px 2px 6px; }}
  .verdict b {{ color: #fff; }}
  .stats {{ margin-top: 18px; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }}
  .stat {{
    background: #161d2e; border: 1px solid #2a3550; border-radius: 14px;
    padding: 20px 22px;
  }}
  .stat .v {{ font-size: 38px; font-weight: 800; color: #fff; }}
  .stat .v .unit {{ font-size: 24px; font-weight: 700; color: #aab4c6; }}
  .stat .l {{ margin-top: 2px; font-size: 18.5px; color: #aab4c6; line-height: 1.28; }}
  .footer {{ margin-top: auto; display: flex; justify-content: space-between; align-items: baseline; gap: 24px; }}
  .footer .note {{ font-size: 19px; color: #8a94a8; }}
  .repo {{ font-size: 21px; font-weight: 600; color: #f5b942; white-space: nowrap; }}
</style>
</head>
<body>
<div id="card">
  <div class="brand"><span class="sun">&#9728;&#65039;</span><h1>solar<span class="gain">Gain</span></h1></div>

  <div class="chart-panel">
    <div class="chart-title">Solar heat gain <span class="hint">&#183; the attic vs the outside air, three measured days</span></div>
    <svg viewBox="0 0 1000 392" width="912" height="358" role="img" aria-label="Three days of measured temperatures. The outdoor line (dotted) and the attic line (solid) rise together each morning, but the attic climbs far higher every afternoon; the shaded gap between them peaks at {peak_label} in the attic.">
      <g stroke="#242e46" stroke-width="1">
{t_grid}
{mid_lines}
      </g>
      <g font-size="19" fill="#6b7690" text-anchor="end">
{t_tick_labels}
      </g>
      <path d="{ribbon_d}" fill="#e34948" opacity="0.16"/>
      <path d="{path_of(outdoor, x_main, ty)}" fill="none" stroke="#5aa0f2" stroke-width="4" stroke-dasharray="2 12" stroke-linecap="round"/>
      <path d="{path_of(attic, x_main, ty)}" fill="none" stroke="{C_ATTIC}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="{px:.1f}" cy="{py:.1f}" r="8" fill="{C_ATTIC}" stroke="#0e1320" stroke-width="3"/>
      <text x="{px:.1f}" y="{py - 16:.1f}" font-size="24" font-weight="700" fill="{C_ATTIC}" text-anchor="middle">{peak_label}</text>
      <g font-size="21" font-weight="600">
        <line x1="{X0 + 14}" y1="24" x2="{X0 + 58}" y2="24" stroke="{C_ATTIC}" stroke-width="5" stroke-linecap="round"/>
        <text x="{X0 + 68}" y="31" fill="{C_ATTIC}">Attic (solid)</text>
        <line x1="{X0 + 234}" y1="24" x2="{X0 + 278}" y2="24" stroke="#5aa0f2" stroke-width="4" stroke-dasharray="2 12" stroke-linecap="round"/>
        <text x="{X0 + 288}" y="31" fill="#5aa0f2">Outdoor (dotted)</text>
        <rect x="{X0 + 14}" y="52" width="44" height="16" fill="#e34948" opacity="0.3"/>
        <text x="{X0 + 68}" y="66" fill="#ef9b9b">shaded gap: solar heat stored overhead</text>
      </g>
      <g font-size="19" fill="#6b7690">
{day_labels}
      </g>
    </svg>
  </div>

  <div class="row" style="margin-top:16px">
    <div class="chart-panel" style="margin-top:0">
      <div class="chart-title">What a ridge vent would do</div>
      {legend_row([(C_ATTIC, '', 'attic, measured'), (C_VENT, '13 9', 'with venting (modeled)')])}
      {vent_svg}
      <div class="verdict">Peak <b>&#8722;{vent_peak_cut:.1f}&#176;F</b> &#183; 9 PM attic <b>{vent_eve_from:.0f} &#8594; {vent_eve_to:.0f}&#176;F</b></div>
    </div>
    <div class="chart-panel" style="margin-top:0">
      <div class="chart-title">What R-38 insulation would do</div>
      {legend_row([(C_ROOM, '', 'bathroom, measured'), (C_INSUL, '13 9', 'at R-38 (modeled)')])}
      {insul_svg}
      <div class="verdict">Room peak <b>{r38_peak_from:.0f} &#8594; {r38_peak_to:.0f}&#176;F</b> on the same day</div>
    </div>
  </div>

  <div class="stats">
    <div class="stat"><div class="v">+{max_delta[1]:.0f}<span class="unit">&#176;F</span></div><div class="l">hottest the attic ran above the outside air</div></div>
    <div class="stat"><div class="v">{int(median_lag // 3600)}h {int(median_lag % 3600 // 60):02d}m</div><div class="l">attic peak lags the sunshine peak, then radiates all evening</div></div>
    <div class="stat"><div class="v">{ac_kwh:.0f}<span class="unit"> kWh</span></div><div class="l">of AC in three days, {stat_solar_pct}% while the panels produced</div></div>
  </div>

  <div class="footer">
    <div class="note">Scenario curves: {hot_day_name} replayed through this house's own fitted thermal model (dotted blue = outdoor). Built on Home Assistant history.</div>
    <div class="repo">github.com/samgutentag/solarGain</div>
  </div>
</div>
</body>
</html>
"""

OUT.write_text(html)
window = f"{datetime.fromtimestamp(w_start, TZ):%b %-d} to {datetime.fromtimestamp(w_end - 1, TZ):%b %-d}"
print(f'Wrote {OUT} ({OUT.stat().st_size // 1024} KB), window {window}, hottest day {datetime.fromtimestamp(dr_start, TZ):%a %b %-d}')
print(f'  attic model: R2={MODEL["r2"]:.2f}, tau={MODEL["tau"] // 60}min, a={MODEL["a"]:.1f}, b={MODEL["b"]:.2f}')
print(f'  vent: peak cut {vent_peak_cut:.1f}F, 9PM {vent_eve_from:.1f} -> {vent_eve_to:.1f}F')
print(f'  room fit: R2={FIT["r2"]:.2f}, tau={FIT["tau"] // 3600}h; R-38 peak {r38_peak_from:.1f} -> {r38_peak_to:.1f}F, 9PM {r38_eve_from:.1f} -> {r38_eve_to:.1f}F')
print(f'  stats: gap +{max_delta[1]:.1f}F, AC {ac_kwh:.1f} kWh ({stat_solar_pct}% solar)')
