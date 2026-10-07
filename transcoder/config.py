import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def parse_bitrate(value) -> int:
    """'2500k' / '2.5M' / 2500000 -> bits per second."""
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*([kKmM]?)\s*", str(value))
    if not m:
        raise ValueError(f"bad bitrate: {value!r}")
    return int(float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()])


def parse_resolution(value):
    """'auto' -> None, '1280x720' -> (1280, 720)."""
    if value is None or str(value).lower() == "auto":
        return None
    m = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", str(value))
    if not m:
        raise ValueError(f"bad resolution: {value!r} (use WxH or auto)")
    return int(m.group(1)), int(m.group(2))


@dataclass
class EncoderConfig:
    preset: str = "veryfast"
    tune: str = "zerolatency"
    profile: str = "baseline"
    strict_cbr: bool = True
    vbv_buffer_seconds: float = 1.0


@dataclass
class RenditionConfig:
    name: str
    bitrate: int
    resolution: tuple | None = None  # None = choose automatically from the bitrate
    outputs: list = field(default_factory=list)


@dataclass
class Config:
    input: str
    renditions: list
    gop_seconds: float = 2.0
    encoder: EncoderConfig = field(default_factory=EncoderConfig)
    min_bpp: float = 0.04
    heights: list = field(default_factory=lambda: [1080, 720, 540, 360, 270, 180, 144])


def load_config(path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    auto = raw.get("auto_resolution") or {}
    renditions = [
        RenditionConfig(
            name=r["name"],
            bitrate=parse_bitrate(r["bitrate"]),
            resolution=parse_resolution(r.get("resolution")),
            outputs=list(r.get("outputs") or []),
        )
        for r in raw["renditions"]
    ]
    names = [r.name for r in renditions]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate rendition names: {names}")
    cfg = Config(
        input=raw["input"],
        renditions=renditions,
        gop_seconds=float(raw.get("gop_seconds", 2.0)),
        encoder=EncoderConfig(**(raw.get("encoder") or {})),
    )
    cfg.min_bpp = float(auto.get("min_bpp", cfg.min_bpp))
    cfg.heights = [int(h) for h in auto.get("heights", cfg.heights)]
    return cfg
