# solarGain

Quantify how solar gain drives attic and room temperatures in the main house, and figure out
where portable AC effort is best spent. Data comes from Home Assistant's recorder via the REST
API; output is a self-contained `dashboard.html`.

## Scope

Main structure only: bedroom, north/south bedrooms, both bathrooms, hallway, living room,
kitchen — all under the one attic. Office is excluded (separate attic). Garage and shed are
kept as no-attic reference zones. Drivers: Ecowitt outdoor temp, attic sensor, and Enphase
solar production as the irradiance proxy.

## Setup

1. In Home Assistant: Profile → Security → Long-lived access tokens → Create token.
2. `cp .env.example .env` and paste the token.
3. `./run.sh` — fetches ~10 days of history (the recorder default), builds, and opens the dashboard.

`./run.sh --mock` builds against synthetic data to preview the dashboard without a token.
`./run.sh --days 3` limits the fetch window.

## Files

- `entities.py` — friendly-name → role map; edit here to add/drop sensors
- `fetch_history.py` — resolves entity IDs, pulls history, downsamples to 10-min means → `data/history.json`
- `build_dashboard.py` — injects the JSON into `dashboard_template.html` → `dashboard.html`
- `dashboard_template.html` — all chart code (vanilla SVG, no dependencies)
