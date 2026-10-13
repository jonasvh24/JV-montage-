"""Per-frame effect helpers on numpy uint8 RGB arrays (H, W, 3)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:  # iPhone HEIC/HEIF photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover
    pass

HEADROOM = 1.12
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif", ".gif"}


def is_video(path) -> bool:
    return str(path).lower().rsplit(".", 1)[-1].join([".", ""]) in VIDEO_EXT


_VIDEO_BRANDS = (b"qt  ", b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"M4A ", b"3gp", b"dash", b"iso5", b"iso6")
_IMAGE_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"heim", b"heis")


def sniff_kind(path) -> str | None:
    """Return 'image', 'video' or None by looking at the file's first bytes, ignoring its extension."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG") or head.startswith((b"GIF87a", b"GIF89a")):
        return "image"
    if head.startswith((b"II*\x00", b"MM\x00*")) or head.startswith(b"BM"):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _IMAGE_BRANDS:
            return "image"
        if brand.startswith(b"3gp") or brand in _VIDEO_BRANDS or brand.rstrip() in (b"qt", b"isom"):
            return "video"
        return "video"
    if head.startswith(b"\x1aE\xdf\xa3"):  # matroska / webm
        return "video"
    if head.startswith(b"bplist"):
        return None  # macOS drag artefact from Photos.app, not an image
    return None


def check_source(path) -> tuple[bool, str]:
    """Verify a source can actually be decoded. Returns (ok, reason)."""
    kind = sniff_kind(path)
    if kind is None:
        try:
            with open(path, "rb") as fh:
                head = fh.read(8)
        except OSError as exc:
            return False, f"unreadable: {exc}"
        if head.startswith(b"bplist"):
            return False, "not an image (macOS drag artefact from Photos: export the photo as a file first)"
        return False, "unrecognised file type"
    try:
        if kind == "image":
            img = Image.open(path)
            img.draft("RGB", (64, 64)) if img.format == "JPEG" else None
            img = ImageOps.exif_transpose(img)
            img.load()
            if img.size[0] < 8 or img.size[1] < 8:
                return False, "image too small"
        else:
            frame = video_frame(path, 32, 18, 0.0)
            if frame.getbbox() is None:
                return False, "video could not be decoded"
    except Exception as exc:
        return False, f"cannot decode: {type(exc).__name__}: {exc}"
    return True, kind


def video_frame(path, out_w: int, out_h: int, at_s: float = 0.0) -> Image.Image:
    """Grab one cover-cropped frame from a video via ffmpeg (auto-rotates)."""
    import subprocess

    from .ffmpeg_bin import get_ffmpeg

    cmd = [get_ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    if len(raw) < out_w * out_h * 3 and at_s > 0:
        # short clip: fall back to the very first frame
        return video_frame(path, out_w, out_h, 0.0)
    if len(raw) < out_w * out_h * 3:
        return Image.new("RGB", (out_w, out_h), (0, 0, 0))
    return Image.frombuffer("RGB", (out_w, out_h), raw[: out_w * out_h * 3], "raw", "RGB", 0, 1)


def placeholder_thumbnail(dst, w: int = 320, h: int = 180, text: str = "UNREADABLE") -> None:
    img = Image.new("RGB", (w, h), (30, 30, 30))
    d = ImageDraw.Draw(img)
    font = _font(int(h * 0.16))
    bb = d.textbbox((0, 0), text, font=font)
    d.text(((w - (bb[2] - bb[0])) // 2 - bb[0], (h - (bb[3] - bb[1])) // 2 - bb[1]), text, font=font, fill=(200, 200, 200))
    d.rectangle([0, 0, w - 1, h - 1], outline=(120, 120, 120), width=3)
    img.save(dst, "JPEG", quality=70)


def make_thumbnail(src, dst, w: int = 320, h: int = 180) -> None:
    """Small cover-cropped JPEG thumbnail for the UI grid (fast JPEG draft decode)."""
    if sniff_kind(src) == "video" or is_video(src):
        img = video_frame(src, w, h, 0.5)
    else:
        img = Image.open(src)
        try:
            img.draft("RGB", (w * 2, h * 2))
        except Exception:
            pass
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (w, h), method=Image.BILINEAR)
    img.save(dst, "JPEG", quality=80)  # base images are scaled this much larger than the output for zoom/shake/pan room


def load_cover(path, base_w: int, base_h: int, bw: bool = False) -> Image.Image:
    """Load a photo, apply EXIF rotation, cover-crop to base_w x base_h."""
    img = Image.open(path)
    if img.format == "JPEG":
        # decode at reduced scale (>= 2x the target) instead of full 12 MP: much faster, no visible loss
        img.draft("RGB", (base_w * 2, base_h * 2))
    img = ImageOps.exif_transpose(img)
    img = img.convert("L" if bw else "RGB")
    img = ImageOps.fit(img, (base_w, base_h), method=Image.LANCZOS, centering=(0.5, 0.5))
    return img


def mean_brightness(path) -> float:
    if is_video(path):
        img = video_frame(path, 64, 36, 0.5).convert("L")
    else:
        img = Image.open(path)
        try:
            img.draft("L", (128, 128))
        except Exception:
            pass
        img = ImageOps.exif_transpose(img).convert("L")
        img.thumbnail((64, 64))
    return float(np.asarray(img).mean() / 255.0)


def ease_out(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1.0 - (1.0 - u) ** 3


def crop_zoom(base: Image.Image, out_w: int, out_h: int, zoom: float, pan: tuple[float, float] = (0.0, 0.0),
              jitter: tuple[float, float] = (0.0, 0.0)) -> Image.Image:
    """Crop a window from the base image at the given zoom (1.0 = nominal view) and resize to output."""
    bw_, bh_ = base.size
    z = max(1.0, zoom) * HEADROOM
    vw, vh = bw_ / z, bh_ / z
    max_dx, max_dy = (bw_ - vw) / 2, (bh_ - vh) / 2
    cx = bw_ / 2 + pan[0] * max_dx + jitter[0] * max_dx
    cy = bh_ / 2 + pan[1] * max_dy + jitter[1] * max_dy
    x0 = min(max(0.0, cx - vw / 2), bw_ - vw)
    y0 = min(max(0.0, cy - vh / 2), bh_ - vh)
    return base.resize((out_w, out_h), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))


def contrast_lut(amount: float) -> np.ndarray:
    """S-curve LUT; amount 0 = identity, 1 = heavy crush."""
    x = np.linspace(0, 1, 256)
    k = 1.0 + amount * 6.0
    y = 1 / (1 + np.exp(-k * (x - 0.5)))
    y = (y - y.min()) / (y.max() - y.min())
    return np.clip(y * 255, 0, 255).astype(np.uint8)


def vignette_mask(w: int, h: int, amount: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    nx = (xx - w / 2) / (w / 2)
    ny = (yy - h / 2) / (h / 2)
    r = np.sqrt(nx * nx + ny * ny) / np.sqrt(2)
    mask = 1.0 - amount * np.clip((r - 0.35) / 0.65, 0, 1) ** 1.5
    return mask.astype(np.float32)[:, :, None]


def grain_bank(w: int, h: int, amount: float, n: int = 12, seed: int = 0) -> list[np.ndarray]:
    """Film-grain noise frames. Generated at half resolution and upsampled so the grain has body
    (like real film) and stays cheap to encode."""
    rng = np.random.default_rng(seed)
    sigma = amount * 18.0
    hw, hh = (w + 1) // 2, (h + 1) // 2
    out = []
    for _ in range(n):
        small = rng.normal(0, sigma, size=(hh, hw)).astype(np.float32)
        big = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)[:h, :w]
        out.append(big.astype(np.int16)[:, :, None])
    return out


def apply_lut(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    return lut[frame]


def apply_vignette(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return (frame.astype(np.float32) * mask).astype(np.uint8)


def apply_grain(frame: np.ndarray, noise: np.ndarray) -> np.ndarray:
    return np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def invert(frame: np.ndarray) -> np.ndarray:
    return 255 - frame


def solid(w: int, h: int, value: int) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


def blend(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    t = min(1.0, max(0.0, t))
    return (a.astype(np.float32) * (1 - t) + b.astype(np.float32) * t).astype(np.uint8)


_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int, path: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for cand in ([path] if path else []) + _FONT_CANDIDATES:
        try:
            if cand:
                return ImageFont.truetype(cand, size=size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def render_card(text: str, w: int, h: int, style: str = "soviet_constructivist", font_path: str | None = None) -> np.ndarray:
    """Render an intertitle card. Constructivist: black field, cream uppercase text, red bar."""
    black, cream, red = (10, 8, 8), (236, 224, 196), (196, 24, 28)
    img = Image.new("RGB", (w, h), black if style == "soviet_constructivist" else (0, 0, 0))
    d = ImageDraw.Draw(img)
    text = text.upper() if style == "soviet_constructivist" else text
    lines = _wrap(text, 22 if w >= h else 14)
    size = int(min(h, w * 1.2) * (0.16 if len(lines) == 1 else 0.11))
    max_w = int(w * 0.86)
    while True:  # shrink until the widest line fits the frame
        font = _font(size, font_path)
        heights, widths = [], []
        for ln in lines:
            bb = d.textbbox((0, 0), ln, font=font)
            widths.append(bb[2] - bb[0])
            heights.append(bb[3] - bb[1])
        if max(widths) <= max_w or size <= 12:
            break
        size = int(size * 0.9)
    gap = int(size * 0.25)
    total_h = sum(heights) + gap * (len(lines) - 1)
    y = (h - total_h) // 2
    if style == "soviet_constructivist":
        # red diagonal wedge top-left and a thick red rule under the text
        d.polygon([(0, 0), (int(w * 0.22), 0), (0, int(h * 0.38))], fill=red)
        d.rectangle([int(w * 0.18), y + total_h + int(size * 0.35), int(w * 0.82), y + total_h + int(size * 0.35) + int(size * 0.12)], fill=red)
    for ln, lw, lh in zip(lines, widths, heights):
        bb = d.textbbox((0, 0), ln, font=font)
        d.text(((w - lw) // 2 - bb[0], y - bb[1]), ln, font=font, fill=cream if style == "soviet_constructivist" else (255, 255, 255))
        y += lh + gap
    return np.asarray(img, dtype=np.uint8)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 > width and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]
