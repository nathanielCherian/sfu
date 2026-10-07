#!/usr/bin/env bash
# Play the mock feed as a live MPEG-TS stream over UDP, paced in real time, looping forever.
# usage: source/serve_source.sh [udp_url] [source.ts]
set -euo pipefail
source "$(dirname "$0")/env.sh"
URL="${1:-udp://127.0.0.1:5000?pkt_size=1316}"
SRC="${2:-$SOURCE_TS}"
[ -f "$SRC" ] || { echo "missing $SRC - run source/make_source.sh first" >&2; exit 1; }
echo "streaming $SRC -> $URL (Ctrl-C to stop)"
exec "$FFMPEG" -hide_banner -loglevel warning -re -stream_loop -1 -i "$SRC" \
  -map 0 -c copy -f mpegts "$URL"
