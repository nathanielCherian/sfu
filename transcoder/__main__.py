"""usage: python -m transcoder [-c config/renditions.yaml] [-i INPUT] [--duration S] [--summary out/summary.json]"""
import argparse
import json
import logging
from pathlib import Path

from .config import load_config
from .pipeline import Transcoder


def main():
    ap = argparse.ArgumentParser(prog="python -m transcoder", description=__doc__)
    ap.add_argument("-c", "--config", default="config/renditions.yaml")
    ap.add_argument("-i", "--input", help="override the input URL/file from the config")
    ap.add_argument("--duration", type=float, help="stop after this many seconds of media")
    ap.add_argument("--no-udp", action="store_true", help="drop udp:// outputs (file outputs only)")
    ap.add_argument("--summary", default="out/summary.json", help="where to write the run summary JSON")
    ap.add_argument("--stats-interval", type=float, default=5.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    if args.input:
        cfg.input = args.input
    if args.no_udp:
        for r in cfg.renditions:
            r.outputs = [o for o in r.outputs if not o.startswith("udp://")]

    summary = Transcoder(cfg, duration=args.duration, stats_interval=args.stats_interval).run()
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, indent=2))
    logging.info("summary written to %s", args.summary)


if __name__ == "__main__":
    main()
