# sfu-test

Building blocks for a small SFU. **Phase 1** (this repo today): take a live MPEG-TS feed, decode it
once, and produce several constant-bitrate H.264 renditions that are ready to be packetised for
WebRTC simulcast in phase 2.

```
 source/serve_source.sh                     python -m transcoder                         phase 2 (todo)
 ┌───────────────────────┐  MPEG-TS/UDP  ┌────────────────────────────────────────────┐
 │ media/source.ts       │ ────────────► │ demux ─► decode once ─┬─► scale ─► x264 CBR ─┼─► out/high.ts  udp://:5001 ─► WebRTC
 │ (720p60 H.264 + AAC,  │  :5000        │                       ├─► scale ─► x264 CBR ─┼─► out/mid.ts   udp://:5002 ─► simulcast
 │  looped, real-time)   │               │                       └─► scale ─► x264 CBR ─┼─► out/low.ts   udp://:5003 ─► layers
 └───────────────────────┘               │ audio (AAC) copied into every rendition    │   (or on_packet callback in-process)
                                         └────────────────────────────────────────────┘
```

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The source scripts need an `ffmpeg` with libx264. They use `$FFMPEG`, then `ffmpeg` on `PATH`, then
`~/encoder-test/tools/ffmpeg` (a static ffmpeg 7.0.2 build). `tools/check_renditions.py` likewise
looks for `ffprobe`.

## Run it

```bash
# 1. Build the mock broadcast feed (once). Default clip: Xiph vidyo1 720p60; pass any clip as $1.
source/make_source.sh                      # -> media/source.ts

# 2. Terminal A: play it as a live MPEG-TS stream over UDP, looping, paced in real time.
source/serve_source.sh                     # -> udp://127.0.0.1:5000

# 3. Terminal B: transcode the live feed (Ctrl-C to stop; --duration N to stop after N seconds).
.venv/bin/python -m transcoder -c config/renditions.yaml --duration 60

# 4. Verify the output files.
.venv/bin/python tools/check_renditions.py out/summary.json

# Watch a rendition while it runs:
ffplay udp://127.0.0.1:5001
```

Offline/fast mode, with no UDP: `python -m transcoder -i media/source.ts --no-udp` reads the file
as fast as the encoders allow and applies back-pressure instead of dropping frames.

## Configuration (`config/renditions.yaml`)

Each rendition is mainly a **bitrate**. With `resolution: auto` (the default) the transcoder picks
the largest standard height (never above the source, keeping its aspect ratio) where
`bitrate / (width * height * fps) >= auto_resolution.min_bpp`. With the shipped `min_bpp: 0.04`
and a 720p60 source:

| rendition | bitrate | auto resolution |
|-----------|---------|-----------------|
| high      | 2500k   | 1280x720        |
| mid       | 800k    | 640x360         |
| low       | 250k    | 320x180         |

Set `resolution: 1280x720` to pin a size. x264 itself never changes resolution; this rule replaces
that missing step. Raise `min_bpp` for sharper and smaller renditions; lower it for larger and softer ones.

Encoder settings, chosen for WebRTC:

* **Constrained Baseline, no B-frames, `tune=zerolatency`**: decodable by every WebRTC client, with
  no reordering delay.
* **Strict CBR**: `nal-hrd=cbr` with `vbv-maxrate = bitrate` and a 1 s VBV buffer. x264 pads with
  filler NAL units so the bitstream rate is constant (phase 2 should strip NAL type 12 before RTP
  packetisation; set `strict_cbr: false` to get capped VBR without padding).
* **Aligned IDRs**: the decoder thread decides which frames are keyframes (every `gop_seconds` of
  media time) and forces the same frames to IDR in all renditions, so an SFU can switch a subscriber
  between layers at any keyframe. SPS/PPS are repeated in-band before every IDR.

Outputs per rendition can be any mix of `*.ts` (MPEG-TS, video + passthrough audio), `*.h264` (raw
Annex-B video) and `udp://host:port` (MPEG-TS).

## Embedding (for phase 2)

```python
from transcoder import Transcoder, load_config

def on_packet(p):            # EncodedPacket(rendition, data: Annex-B bytes, pts, dts (90 kHz), keyframe)
    ...                      # e.g. hand to an RTP packetiser for simulcast layer p.rendition

Transcoder(load_config("config/renditions.yaml"), on_packet=on_packet).run()
```

`on_packet` runs on that rendition's encoder thread.

## Layout

```
config/renditions.yaml     rendition ladder + encoder settings
source/                    mock MPEG-TS feed: make_source.sh, serve_source.sh
transcoder/
  pipeline.py              decode once -> per-rendition worker thread (scale, x264, sinks)
  ladder.py                bitrate -> resolution rule
  sinks.py                 file / UDP muxing
  config.py, __main__.py   YAML loading, CLI
tools/check_renditions.py  ffprobe-based verification (bitrate, profile, IDR alignment)
```
