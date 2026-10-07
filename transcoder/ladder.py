"""Pick an output resolution for a target bitrate (x264 itself never changes resolution)."""


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def pick_resolution(bitrate: int, fps: float, src_w: int, src_h: int,
                    heights, min_bpp: float) -> tuple[int, int]:
    """Largest candidate height <= source height whose bits-per-pixel
    bitrate / (w * h * fps) is >= min_bpp. Keeps the source aspect ratio.
    Falls back to the smallest candidate when even that is starved."""
    aspect = src_w / src_h
    candidates = sorted({h for h in heights if h <= src_h} | {src_h}, reverse=True)
    sizes = [(_even(h * aspect), _even(h)) for h in candidates]
    for w, h in sizes:
        if bitrate / (w * h * fps) >= min_bpp:
            return w, h
    return sizes[-1]
