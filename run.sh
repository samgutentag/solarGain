#!/bin/zsh
# Fetch fresh history from Home Assistant and rebuild the dashboard.
set -e
cd "$(dirname "$0")"
python3 fetch_history.py "$@"
python3 build_dashboard.py
open dashboard.html
