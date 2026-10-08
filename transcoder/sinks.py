"""Where encoded packets go: muxed files / UDP streams, or a Python callback."""
from fractions import Fraction
from pathlib import Path

import av

TB_90K = Fraction(1, 90000)


def _format_for(url: str) -> str:
    if url.startswith(("udp://", "rtp://", "srt://", "tcp://")):
        return "mpegts"
    ext = Path(url.split("?")[0]).suffix.lower()
    return {".ts": "mpegts", ".h264": "h264", ".264": "h264", ".m3u8": "hls"}.get(ext, "mpegts")


# Live HLS: one segment per GOP (segments can only be cut on IDRs), short rolling playlist.
HLS_OPTIONS = {
    "hls_time": "1",
    "hls_list_size": "6",
    "hls_flags": "delete_segments+independent_segments+omit_endlist",
    "hls_allow_cache": "0",
}


class MuxSink:
    """Muxes one rendition's video (and the passthrough audio, if the format allows) into a URL."""

    def __init__(self, url: str, width: int, height: int, fps: Fraction, audio_template=None):
        self.url = url
        fmt = _format_for(url)
        if "://" not in url:
            Path(url).parent.mkdir(parents=True, exist_ok=True)
        self.container = av.open(url, "w", format=fmt, options=HLS_OPTIONS if fmt == "hls" else None)
        # An unopened libx264 stream only describes the codec parameters; the packets come
        # from the rendition's single shared encoder.
        self.video = self.container.add_stream("libx264", rate=fps)
        self.video.width, self.video.height, self.video.pix_fmt = width, height, "yuv420p"
        self.video.time_base = TB_90K
        self.audio = None
        if audio_template is not None and fmt in ("mpegts", "hls"):
            self.audio = self.container.add_stream_from_template(audio_template)
            self.audio.time_base = TB_90K

    def write_video(self, data: bytes, pts: int, dts: int, keyframe: bool):
        self._mux(self.video, data, pts, dts, keyframe)

    def write_audio(self, data: bytes, pts: int, dts: int):
        if self.audio is not None:
            self._mux(self.audio, data, pts, dts, True)

    def _mux(self, stream, data, pts, dts, keyframe):
        pkt = av.Packet(data)
        pkt.pts, pkt.dts, pkt.time_base, pkt.is_keyframe = pts, dts, TB_90K, keyframe
        pkt.stream = stream
        self.container.mux(pkt)

    def close(self):
        self.container.close()
