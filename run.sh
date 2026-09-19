#!/usr/bin/env bash
# run.sh - Cat clip le roi dang, cho video NGOAI kenh Ho So Tu Nhien.
# Luong chinh dung autopost.py; script nay chi de xu ly file roi.
#
#   ./run.sh video.mp4                  # cat 4 Shorts roi dang (YouTube private)
#   ./run.sh video.mp4 6                # cat 6 Shorts
#   ./run.sh video.mp4 4 --dry-run      # xem truoc, khong dang
set -euo pipefail
cd "$(dirname "$0")"

VIDEO="${1:?Dung: ./run.sh <video.mp4> [so_luong] [tham_so_upload...]}"
if [[ "${2:-}" =~ ^[0-9]+$ ]]; then COUNT="$2"; shift 2; else COUNT=4; shift 1; fi

echo "==> Buoc 1/2: cat Shorts"
python3 cut_shorts.py "$VIDEO" -n "$COUNT"

STEM="$(basename "${VIDEO%.*}")"
DIR="$(cd "$(dirname "$VIDEO")" && pwd)/${STEM}_shorts"
REPORT="$DIR/report.json"
[ -f "$REPORT" ] || { echo "Khong thay $REPORT"; exit 1; }

echo "==> Buoc 2/2: dang len nen tang"
python3 upload.py --from-report "$REPORT" --platform both "$@"
