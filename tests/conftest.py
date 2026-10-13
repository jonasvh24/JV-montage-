import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


def make_click_track(path: Path, bpm: float = 120.0, seconds: float = 12.0, sr: int = 22050, accent_every: int = 4) -> Path:
    """Synthesize a click track: short decaying sine bursts on each beat, louder on downbeats."""
    n = int(seconds * sr)
    y = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t = np.arange(int(0.06 * sr)) / sr
    burst = (np.sin(2 * np.pi * 880 * t) * np.exp(-t * 60)).astype(np.float32)
    i = 0
    while True:
        start = int(i * period * sr)
        if start >= n:
            break
        amp = 1.0 if i % accent_every == 0 else 0.5
        end = min(n, start + len(burst))
        y[start:end] += amp * burst[: end - start]
        i += 1
    # low noise floor so RMS isn't degenerate
    y += (np.random.default_rng(0).standard_normal(n) * 0.003).astype(np.float32)
    sf.write(str(path), y, sr)
    return path


@pytest.fixture
def click_wav(tmp_path: Path) -> Path:
    return make_click_track(tmp_path / "click.wav")


@pytest.fixture
def tiny_photos(tmp_path: Path) -> list[Path]:
    from PIL import Image

    paths = []
    for i in range(5):
        img = Image.new("RGB", (400, 300), (40 * i, 200 - 30 * i, 90))
        p = tmp_path / f"photo_{i:02d}.jpg"
        img.save(p, quality=85)
        paths.append(p)
    return paths


@pytest.fixture
def tiny_clip(tmp_path: Path) -> Path:
    """A 2 s 320x240 test-pattern MOV made with ffmpeg."""
    from nullmontage.ffmpeg_bin import get_ffmpeg

    out = tmp_path / "clip.mov"
    subprocess.run([get_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out
