#!/usr/bin/env bash
# Write a rendition as MPEG-TS to stdout, so it can be watched on another machine over ssh:
#
#   ssh server ~/sfu-test/tools/relay.sh mid | ffplay -fflags nobuffer -flags low_delay -framedrop -
#
# usage: tools/relay.sh NAME            live rendition from its udp:// output in the config (copy, no re-encode)
#        tools/relay.sh mosaic          all live renditions side by side (left->right in config order),
#                                       each upscaled to 960x540 like a player would, near-lossless re-encode
#        tools/relay.sh file NAME       replay the recorded out/NAME.ts in real time
#
# A unicast UDP port delivers to one reader at a time, so don't relay the same rendition twice at once
# (and "mosaic" takes all of them).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/source/env.sh"
CONFIG="${CONFIG:-$ROOT/config/renditions.yaml}"
TILE="${TILE:-960x540}"

# udp:// output URL of a rendition (or of all renditions, one per line, when NAME is empty)
udp_urls() {
  "$ROOT/.venv/bin/python" - "$CONFIG" "${1:-}" <<'EOF'
import sys, yaml
cfg, want = yaml.safe_load(open(sys.argv[1])), sys.argv[2]
hits = [o for r in cfg["renditions"] if not want or r["name"] == want
        for o in r.get("outputs", []) if o.startswith("udp://")][: None if not want else 1]
if not hits:
    names = [r["name"] for r in cfg["renditions"]]
    sys.exit(f"no udp output for {want!r}; renditions: {names}")
print("\n".join(hits))
EOF
}

# The transcoder sends to these ports; we receive on them (drop pkt_size, add a receive buffer).
as_input() { echo "${1%%\?*}?overrun_nonfatal=1&fifo_size=50000&timeout=5000000"; }

FF=("$FFMPEG" -hide_banner -loglevel error -nostdin)
case "${1:-}" in
  ""|-h|--help)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  file)
    f="$ROOT/out/${2:?usage: relay.sh file NAME}.ts"
    exec "${FF[@]}" -re -i "$f" -map 0 -c copy -f mpegts - ;;
  mosaic)
    mapfile -t urls < <(udp_urls)
    inputs=(); chains=""; labels=""
    for i in "${!urls[@]}"; do
      inputs+=(-fflags nobuffer -i "$(as_input "${urls[$i]}")")
      chains+="[$i:v]scale=${TILE/x/:}:flags=bicubic,setsar=1[v$i];"
      labels+="[v$i]"
    done
    exec "${FF[@]}" "${inputs[@]}" \
      -filter_complex "${chains}${labels}hstack=inputs=${#urls[@]}[out]" -map "[out]" \
      -c:v libx264 -preset ultrafast -tune zerolatency -crf 16 -pix_fmt yuv420p -f mpegts - ;;
  *)
    url="$(udp_urls "$1")"
    exec "${FF[@]}" -fflags nobuffer -i "$(as_input "$url")" -map 0 -c copy -f mpegts - ;;
esac
