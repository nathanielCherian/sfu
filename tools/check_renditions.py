#!/usr/bin/env python3
"""Verify transcoder output with ffprobe: bitrate vs target, CBR stability, WebRTC-safe profile,
and keyframe alignment across renditions.

usage: tools/check_renditions.py [out/summary.json]   (exit status 1 if any check fails)
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe") or str(Path.home() / "encoder-test/tools/ffprobe")


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,profile,width,height,has_b_frames,r_frame_rate",
         "-show_entries", "packet=pts_time,size,flags", "-of", "json", path],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def analyse(path, fps):
    info = probe(path)
    stream = info["streams"][0]
    pkts = [p for p in info["packets"] if p.get("pts_time") is not None]
    times = [float(p["pts_time"]) for p in pkts]
    t0 = min(times)
    duration = max(times) - t0 + 1 / fps
    total_bits = sum(int(p["size"]) for p in pkts) * 8
    per_sec = {}
    for t, p in zip(times, pkts):
        per_sec[int(t - t0)] = per_sec.get(int(t - t0), 0) + int(p["size"]) * 8
    full_secs = [b for s, b in sorted(per_sec.items()) if s + 1 <= duration - 1e-6]
    return {
        "stream": stream,
        "frames": len(pkts),
        "duration": duration,
        "avg_bps": total_bits / duration,
        "per_sec": full_secs,
        "keyframes": sorted(round(t - t0, 4) for t, p in zip(times, pkts) if "K" in p["flags"]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("summary", nargs="?", default="out/summary.json")
    ap.add_argument("--tolerance", type=float, default=0.05, help="allowed avg bitrate error (fraction)")
    args = ap.parse_args()
    summary = json.loads(Path(args.summary).read_text())
    fps = summary["source"]["fps"]

    failures, keyframe_sets = [], {}
    print(f"source: {summary['source']}\n")
    hdr = f"{'rendition':9} {'res':>9} {'target':>8} {'avg':>8} {'err':>6} {'1s min':>8} {'1s max':>8} {'1s sd':>6} {'frames':>6} {'IDRs':>4}  profile"
    print(hdr)
    print("-" * len(hdr))
    for r in summary["renditions"]:
        files = [o for o in r["outputs"] if "://" not in o]
        if not files:
            print(f"{r['name']:9} (no file outputs to check)")
            continue
        a = analyse(files[0], fps)
        st = a["stream"]
        target = r["target_bps"]
        err = a["avg_bps"] / target - 1
        ps = a["per_sec"] or [a["avg_bps"]]
        print(f"{r['name']:9} {st['width']:>4}x{st['height']:<4} {target/1e3:7.0f}k {a['avg_bps']/1e3:7.0f}k "
              f"{err*100:+5.1f}% {min(ps)/1e3:7.0f}k {max(ps)/1e3:7.0f}k {statistics.pstdev(ps)/1e3:5.0f}k "
              f"{a['frames']:>6} {len(a['keyframes']):>4}  {st.get('profile')}")
        keyframe_sets[r["name"]] = a["keyframes"]
        if abs(err) > args.tolerance:
            failures.append(f"{r['name']}: average bitrate off target by {err*100:+.1f}%")
        if st.get("profile") != "Constrained Baseline":
            failures.append(f"{r['name']}: profile is {st.get('profile')}, want Constrained Baseline")
        if st.get("has_b_frames"):
            failures.append(f"{r['name']}: has B-frames")
        if (st["width"], st["height"]) != (r["width"], r["height"]):
            failures.append(f"{r['name']}: resolution {st['width']}x{st['height']} != summary {r['width']}x{r['height']}")

    if keyframe_sets:
        ref_name, ref = next(iter(keyframe_sets.items()))
        print(f"\nIDR times ({ref_name}): {ref[:12]}{' ...' if len(ref) > 12 else ''}")
        for name, ks in keyframe_sets.items():
            if ks != ref:
                failures.append(f"keyframes of {name} not aligned with {ref_name}: {sorted(set(ks) ^ set(ref))[:10]}")
        if len(keyframe_sets) > 1 and not any("aligned" in f for f in failures):
            print("IDR alignment: OK (identical across all renditions)")

    print()
    if failures:
        print("FAIL\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
