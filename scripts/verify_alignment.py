"""Render a clean beat-cut montage (no flashes/strobe/grain) and check that every detected scene change
in the encoded file lands within one frame of a planned cut and of the beat grid.

usage: uv run python scripts/verify_alignment.py <audio> <photo_dir> [out.mp4]
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from nullmontage.audio import analyze, decode_for_mux, decode_to_wav
from nullmontage.effects import check_source
from nullmontage.ffmpeg_bin import get_ffmpeg
from nullmontage.planner import plan
from nullmontage.presets import MontageParams
from nullmontage.render import render


def detect_cuts(video: Path, fps: int, w: int = 160, h: int = 90) -> np.ndarray:
    """Decode to tiny grayscale frames and flag frames whose difference from the previous frame is a clear outlier."""
    raw = subprocess.run([get_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    thresh = max(6.0, np.median(diff) * 6)
    idx = np.nonzero(diff > thresh)[0] + 1  # index of the first frame of the new shot
    return idx / fps


def main(audio: Path, photo_dir: Path, out: Path | None = None) -> int:
    photos = sorted(p for p in photo_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".webp"} and check_source(p)[0])
    tmp = Path(tempfile.mkdtemp())
    out = out or tmp / "clean.mp4"
    grid = analyze(decode_to_wav(audio, tmp / "a.wav"))
    p = MontageParams(fit_mode="fit_to_photos", pacing="accelerate", cut_unit="beat")
    p.effects.punch_zoom = 0.0
    p.effects.grain = 0.0
    p.effects.dissolve_ms = 0
    edl = plan(p, grid, len(photos))
    mux = decode_for_mux(audio, tmp / "mux.wav")
    render(edl, p, photos, mux, out, preview=True)
    cuts = detect_cuts(out, p.output.fps)
    starts = np.array([s.start for s in edl.shots[1:]])  # first shot has no preceding cut
    half = np.array(grid.half_beats)
    frame = 1.0 / p.output.fps
    # a cut rendered on the nearest frame sits at round(start*fps)/fps
    starts = np.round(starts * p.output.fps) / p.output.fps
    d_plan = np.abs(cuts[:, None] - starts[None, :]).min(axis=1)
    d_grid = np.abs(cuts[:, None] - half[None, :]).min(axis=1)
    missed = np.abs(starts[:, None] - cuts[None, :]).min(axis=1) > frame * 1.5
    print(f"planned cuts {len(starts)}, detected {len(cuts)}, planned cuts not detected {int(missed.sum())}"
          + (" (cuts between near-identical images are invisible to the detector; not an alignment error)" if missed.sum() else ""))
    print(f"detected cut -> planned start: max {d_plan.max()*1000:.1f} ms (frame = {frame*1000:.1f} ms)")
    print(f"detected cut -> beat/half-beat grid: max {d_grid.max()*1000:.1f} ms, mean {d_grid.mean()*1000:.1f} ms")
    off = np.abs(cuts / 0.25 - np.round(cuts / 0.25)) * 0.25
    print(f"(if the audio is a 120 BPM click track) cut -> true grid: max {off.max()*1000:.1f} ms, mean {off.mean()*1000:.1f} ms")
    spurious = int((d_plan > frame * 0.51).sum())
    print(f"spurious cuts (detected but not planned): {spurious}")
    ok = len(cuts) > 0 and d_plan.max() <= frame * 0.51 and d_grid.max() <= frame * 0.51 and spurious == 0
    print("ALIGNMENT OK" if ok else "ALIGNMENT PROBLEM")
    return 0 if ok else 1


if __name__ == "__main__":
    a = Path(sys.argv[1]); d = Path(sys.argv[2]); o = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    sys.exit(main(a, d, o))
