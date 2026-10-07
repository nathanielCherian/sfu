"""Decode one MPEG-TS input once, fan the frames out to N x264 encoders running in parallel.

    demux/decode (main thread) --frame--> queue --> [scale -> x264 CBR -> sinks]  x N  (worker threads)
                               --audio--> (copied as-is into every rendition's TS output)

All timestamps are rebased to a shared 90 kHz clock that starts at the first video frame, and
the keyframe decision is made once, in the decoder thread, so every rendition gets an IDR on the
same frames.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

import av
from av.video.frame import PictureType
from av.video.reformatter import Interpolation, VideoReformatter

from .config import Config, RenditionConfig
from .ladder import pick_resolution
from .sinks import TB_90K, MuxSink

log = logging.getLogger("transcoder")


@dataclass(frozen=True)
class EncodedPacket:
    """One encoded H.264 access unit (Annex-B), handed to `on_packet` callbacks."""
    rendition: str
    data: bytes
    pts: int          # 90 kHz, relative to the first input video frame
    dts: int
    keyframe: bool


@dataclass(frozen=True)
class _Audio:
    data: bytes
    pts: int
    dts: int


@dataclass
class RenditionStats:
    frames: int = 0
    keyframes: int = 0
    bytes: int = 0
    dropped: int = 0
    encode_seconds: float = 0.0
    last_pts: int = 0


class RenditionWorker(threading.Thread):
    def __init__(self, spec: RenditionConfig, cfg: Config, width: int, height: int,
                 fps: Fraction, audio_template, live: bool,
                 on_packet: Callable[[EncodedPacket], None] | None):
        super().__init__(name=f"enc-{spec.name}", daemon=True)
        self.spec, self.width, self.height, self.fps = spec, width, height, fps
        self.live = live
        self.on_packet = on_packet
        self.stats = RenditionStats()
        self.error: BaseException | None = None
        # ~1 s of frames: enough to absorb jitter, small enough to keep latency bounded.
        self.queue: queue.Queue = queue.Queue(maxsize=max(8, int(fps)))
        self.encoder = self._open_encoder(cfg)
        # Frames are shared by all workers, and VideoFrame.reformat() caches one scaler *on the
        # frame*; concurrent use of that from several threads crashes, so each worker owns one.
        self.reformatter = VideoReformatter()
        self.sinks = [MuxSink(url, width, height, fps, audio_template) for url in spec.outputs]

    def _open_encoder(self, cfg: Config):
        enc = cfg.encoder
        kbps = self.spec.bitrate // 1000
        gop_frames = max(1, round(cfg.gop_seconds * self.fps))
        x264_params = ":".join([
            f"nal-hrd={'cbr' if enc.strict_cbr else 'vbr'}",
            f"vbv-maxrate={kbps}",
            f"vbv-bufsize={max(1, int(kbps * enc.vbv_buffer_seconds))}",
            # IDRs are forced by the pipeline on shared timestamps; keyint is only a safety net.
            f"keyint={gop_frames * 2}",
            "scenecut=0",
            "bframes=0",
            "repeat-headers=1",   # SPS/PPS in-band before every IDR
            "force-cfr=1",
        ])
        ctx = av.CodecContext.create("libx264", "w")
        ctx.width, ctx.height, ctx.pix_fmt = self.width, self.height, "yuv420p"
        ctx.time_base = TB_90K
        ctx.framerate = self.fps
        ctx.bit_rate = self.spec.bitrate
        ctx.options = {
            "preset": enc.preset,
            "tune": enc.tune,
            "profile": enc.profile,
            "forced-idr": "1",
            "x264-params": x264_params,
        }
        ctx.open()
        return ctx

    # -- called from the decoder thread -------------------------------------------------
    def submit_frame(self, frame: av.VideoFrame):
        if not self.live:
            self.queue.put(frame)        # file input: back-pressure the decoder instead
            return
        try:
            self.queue.put_nowait(frame)
        except queue.Full:               # live input: never stall the UDP reader
            self.stats.dropped += 1

    def submit_audio(self, audio: _Audio):
        self.queue.put(audio)

    def finish(self):
        self.queue.put(None)

    # -- worker thread ------------------------------------------------------------------
    def run(self):
        try:
            while (item := self.queue.get()) is not None:
                if isinstance(item, _Audio):
                    for sink in self.sinks:
                        sink.write_audio(item.data, item.pts, item.dts)
                else:
                    self._encode(item)
            self._encode(None)  # flush
        except BaseException as e:  # surfaced by the main thread
            self.error = e
            log.exception("rendition %s failed", self.spec.name)
        finally:
            for sink in self.sinks:
                try:
                    sink.close()
                except Exception:
                    log.exception("closing %s", sink.url)

    def _encode(self, frame: av.VideoFrame | None):
        if frame is not None and (frame.width, frame.height, frame.format.name) != \
                (self.width, self.height, "yuv420p"):
            scaled = self.reformatter.reformat(frame, self.width, self.height, "yuv420p",
                                               interpolation=Interpolation.BICUBIC)
            scaled.pts, scaled.time_base, scaled.pict_type = frame.pts, frame.time_base, frame.pict_type
            frame = scaled
        t0 = time.perf_counter()
        packets = self.encoder.encode(frame)
        self.stats.encode_seconds += time.perf_counter() - t0
        for pkt in packets:
            data = bytes(pkt)
            st = self.stats
            st.frames += 1
            st.bytes += len(data)
            st.keyframes += pkt.is_keyframe
            st.last_pts = pkt.pts
            for sink in self.sinks:
                sink.write_video(data, pkt.pts, pkt.dts, pkt.is_keyframe)
            if self.on_packet:
                self.on_packet(EncodedPacket(self.spec.name, data, pkt.pts, pkt.dts, pkt.is_keyframe))


class Transcoder:
    def __init__(self, cfg: Config, on_packet: Callable[[EncodedPacket], None] | None = None,
                 duration: float | None = None, stats_interval: float = 5.0):
        self.cfg = cfg
        self.on_packet = on_packet
        self.duration = duration
        self.stats_interval = stats_interval
        self.workers: list[RenditionWorker] = []
        self.source: dict = {}
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        url = self.cfg.input
        live = "://" in url and not url.startswith("file:")
        log.info("opening %s (%s)", url, "live" if live else "file")
        inp = av.open(url, timeout=(15.0, 5.0))
        try:
            self._run(inp, live)
        finally:
            inp.close()
        return self.summary()

    def _run(self, inp, live: bool):
        vin = inp.streams.video[0]
        vin.thread_type = "AUTO"
        ain = inp.streams.audio[0] if inp.streams.audio else None
        fps = vin.average_rate or vin.guessed_rate or Fraction(30)
        src_w, src_h = vin.codec_context.width, vin.codec_context.height
        self.source = {"width": src_w, "height": src_h, "fps": float(fps),
                       "codec": vin.codec_context.name, "audio": ain.codec_context.name if ain else None}
        log.info("source: %dx%d @ %.3f fps, %s, audio=%s", src_w, src_h, fps,
                 self.source["codec"], self.source["audio"])

        for spec in self.cfg.renditions:
            w, h = spec.resolution or pick_resolution(spec.bitrate, float(fps), src_w, src_h,
                                                      self.cfg.heights, self.cfg.min_bpp)
            worker = RenditionWorker(spec, self.cfg, w, h, fps, ain, live, self.on_packet)
            log.info("rendition %-6s %4dx%-4d %5d kbps (%s) -> %s", spec.name, w, h,
                     spec.bitrate // 1000, "pinned" if spec.resolution else "auto",
                     ", ".join(spec.outputs) or "(callback only)")
            self.workers.append(worker)
        for w in self.workers:
            w.start()

        gop = round(self.cfg.gop_seconds * 90000)
        origin = None           # first video pts, in the video stream's time base
        next_idr = 0
        self._wall_start = time.monotonic()
        next_report = self._wall_start + self.stats_interval
        self._media_pos = 0
        try:
            for packet in inp.demux(*(s for s in (vin, ain) if s is not None)):
                if self._stop.is_set() or any(w.error for w in self.workers):
                    break
                if packet.stream is ain:
                    if origin is None or packet.pts is None:
                        continue  # audio before the first video frame has nowhere to go
                    a_origin = int(origin * vin.time_base / ain.time_base)
                    rel = lambda t: int((t - a_origin) * ain.time_base / TB_90K)
                    pts = rel(packet.pts)
                    if pts < 0:
                        continue
                    item = _Audio(bytes(packet), pts, rel(packet.dts) if packet.dts is not None else pts)
                    for w in self.workers:
                        w.submit_audio(item)
                    continue

                for frame in packet.decode():
                    if frame.pts is None:
                        continue
                    if origin is None:
                        origin = frame.pts
                    pts90 = int((frame.pts - origin) * vin.time_base / TB_90K)
                    frame.pts, frame.time_base = pts90, TB_90K
                    # Same IDR decision for every rendition => switchable keyframes.
                    if pts90 >= next_idr:
                        frame.pict_type = PictureType.I
                        next_idr = (pts90 // gop + 1) * gop
                    else:
                        frame.pict_type = PictureType.NONE
                    self._media_pos = pts90
                    for w in self.workers:
                        w.submit_frame(frame)
                if self.duration is not None and self._media_pos >= self.duration * 90000:
                    log.info("reached --duration %.1fs", self.duration)
                    break
                if time.monotonic() >= next_report:
                    self._report()
                    next_report += self.stats_interval
        except KeyboardInterrupt:
            log.info("interrupted, flushing encoders")
        except av.error.ExitError as e:  # e.g. UDP read timeout once the feed stops
            log.warning("input ended: %s", e)
        finally:
            for w in self.workers:
                w.finish()
            for w in self.workers:
                w.join()
        errors = [w.error for w in self.workers if w.error]
        if errors:
            raise errors[0]
        self._report()

    def _report(self):
        wall = time.monotonic() - self._wall_start
        media = self._media_pos / 90000
        parts = [f"t={media:7.1f}s speed={media / wall if wall else 0:4.2f}x"]
        for w in self.workers:
            st = w.stats
            secs = st.last_pts / 90000 + 1 / float(w.fps) if st.frames else 0
            kbps = st.bytes * 8 / secs / 1000 if secs else 0
            ms = st.encode_seconds / st.frames * 1000 if st.frames else 0
            parts.append(f"{w.spec.name}={kbps:6.0f}kbps q={w.queue.qsize():2d} "
                         f"drop={st.dropped} enc={ms:4.1f}ms/f")
        log.info(" | ".join(parts))

    def summary(self) -> dict:
        out = {"input": self.cfg.input, "source": self.source, "gop_seconds": self.cfg.gop_seconds,
               "renditions": []}
        for w in self.workers:
            st = w.stats
            secs = st.last_pts / 90000 + 1 / float(w.fps) if st.frames else 0
            out["renditions"].append({
                "name": w.spec.name, "width": w.width, "height": w.height,
                "target_bps": w.spec.bitrate,
                "measured_bps": round(st.bytes * 8 / secs) if secs else 0,
                "frames": st.frames, "keyframes": st.keyframes, "dropped": st.dropped,
                "encode_ms_per_frame": round(st.encode_seconds / st.frames * 1000, 2) if st.frames else 0,
                "outputs": w.spec.outputs,
            })
        return out
