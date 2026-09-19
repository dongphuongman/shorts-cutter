#!/bin/bash
# Day cac Short YouTube con ton dong, chay hang ngay sau khi quota reset.
# Quota YouTube reset luc 00:00 gio Thai Binh Duong = 14:00 gio Viet Nam.
cd "$(dirname "$0")" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
/opt/homebrew/bin/python3 autopost.py push --days 12 --only youtube --delay 20
