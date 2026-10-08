#!/usr/bin/env python3
"""Browser viewer for the transcoder's renditions.

Serves the HLS playlists the transcoder writes (out/hls/<name>/index.m3u8) plus a page that plays
every rendition side by side. The H.264 is the encoder's output, only re-packaged as HLS segments.

    .venv/bin/python tools/web_view.py                 # http://127.0.0.1:8080
    ssh -L 8080:localhost:8080 <server>                # on your machine, then open http://localhost:8080
"""
import argparse
import functools
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from transcoder.config import load_config  # noqa: E402


def renditions_from(config_path, out_dir: Path):
    cfg = load_config(config_path)
    found = []
    for r in cfg.renditions:
        for o in r.outputs:
            if o.endswith(".m3u8") and "://" not in o:
                path = (ROOT / o).resolve()
                try:
                    rel = path.relative_to(out_dir)
                except ValueError:
                    sys.exit(f"{o}: HLS output must live under {out_dir}")
                found.append({"name": r.name, "bitrate": r.bitrate,
                              "resolution": "x".join(map(str, r.resolution)) if r.resolution else "auto",
                              "url": rel.as_posix()})
    if not found:
        sys.exit("no *.m3u8 outputs in the config; add e.g. out/hls/high/index.m3u8 to each rendition")
    return found


class Handler(SimpleHTTPRequestHandler):
    page: bytes = b""

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self.page)))
            self.end_headers()
            self.wfile.write(self.page)
            return
        super().do_GET()

    def end_headers(self):
        if self.path.endswith(".m3u8"):
            self.send_header("Cache-Control", "no-cache, no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quiet: playlists are polled every second
        if args and str(args[1]) not in ("200", "304"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=str(ROOT / "config/renditions.yaml"))
    ap.add_argument("--out", default=str(ROOT / "out"), help="directory the transcoder writes into")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only, use ssh -L)")
    ap.add_argument("-p", "--port", type=int, default=8080)
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    rends = renditions_from(args.config, out_dir)
    template = (Path(__file__).parent / "web_view.html").read_text()
    Handler.page = template.replace("/*RENDITIONS*/[]", json.dumps(rends)).encode()

    server = ThreadingHTTPServer((args.host, args.port), functools.partial(Handler, directory=str(out_dir)))
    print(f"serving {', '.join(r['name'] for r in rends)} on http://{args.host}:{args.port}/")
    print(f"  from your machine: ssh -L {args.port}:localhost:{args.port} <this-server>  then open http://localhost:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
