import json
import subprocess
from pathlib import Path

import pytest

from nullmontage.audio import analyze
from nullmontage.ffmpeg_bin import get_ffmpeg
from nullmontage.planner import plan
from nullmontage.presets import MontageParams
from nullmontage.render import render


def _duration(path: Path) -> float:
    # no ffprobe bundled: use ffmpeg -i and parse Duration
    out = subprocess.run([get_ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "Duration:" in line:
            hms = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise AssertionError(out)


@pytest.mark.parametrize("container,codec", [("mp4", "h264"), ("mov", "h264"), ("mov", "prores_422")])
def test_render_small(tmp_path, click_wav, tiny_photos, container, codec):
    grid = analyze(click_wav)
    p = MontageParams(fit_mode="fit_to_photos", pacing="constant", cut_unit="beat")
    p.output.container = container
    p.output.codec = codec
    p.output.fps = 24
    p.effects.flash_white = "downbeats"
    p.effects.grain = 0.3
    p.effects.vignette = 0.3
    p.effects.contrast = 0.3
    p.effects.shake = 0.3
    p.effects.dissolve_ms = 100
    p.intertitles.enabled = True
    p.intertitles.placement = "opening"
    p.intertitles.cards = ["TEST"]
    p.intertitles.card_hold_beats = 2
    edl = plan(p, grid, len(tiny_photos))
    out = tmp_path / f"out.{container}"
    calls = []
    render(edl, p, tiny_photos, click_wav, out, progress=lambda i, n, m: calls.append((i, n)), preview=(codec == "h264" and container == "mp4"))
    assert out.exists() and out.stat().st_size > 10_000
    assert calls and calls[-1][0] == calls[-1][1]
    assert abs(_duration(out) - edl.duration) < 0.25
