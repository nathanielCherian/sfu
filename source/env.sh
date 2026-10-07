# Shared settings for the source scripts. Override any of these from the environment.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FFMPEG="${FFMPEG:-$(command -v ffmpeg || echo "$HOME/encoder-test/tools/ffmpeg")}"
SOURCE_TS="${SOURCE_TS:-$ROOT/media/source.ts}"
