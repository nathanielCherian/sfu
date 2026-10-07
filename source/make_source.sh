#!/usr/bin/env bash
# Build a broadcast-like MPEG-TS file from a raw test clip:
#   H.264 High @ 8 Mbps, closed 2 s GOP, + 48 kHz AAC sine tone (the Xiph clips have no audio).
# usage: source/make_source.sh [input_clip] [output.ts]
set -euo pipefail
source "$(dirname "$0")/env.sh"
CLIP="${1:-$HOME/encoder-test/xiph/vidyo1_720p_60fps.y4m}"
OUT="${2:-$SOURCE_TS}"
mkdir -p "$(dirname "$OUT")"

FPS=$("${FFMPEG%ffmpeg}ffprobe" -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$CLIP" 2>/dev/null || echo 60/1)
GOP=$(python3 -c "from fractions import Fraction as F; print(round(F('$FPS')*2))")

"$FFMPEG" -hide_banner -y -i "$CLIP" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000" \
  -map 0:v:0 -map 1:a:0 -shortest \
  -c:v libx264 -preset medium -profile:v high -pix_fmt yuv420p \
  -b:v 8M -maxrate 8M -bufsize 8M -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
  -c:a aac -b:a 128k -ac 2 \
  -f mpegts -metadata service_name="sfu-test mock feed" "$OUT"
echo "wrote $OUT"
