import numpy as np

from nullmontage.audio import analyze
from nullmontage.effects import is_video, make_thumbnail, mean_brightness
from nullmontage.planner import plan
from nullmontage.presets import MontageParams
from nullmontage.render import Renderer


def test_video_helpers(tmp_path, tiny_clip):
    assert is_video(tiny_clip) and not is_video("x.jpg")
    make_thumbnail(tiny_clip, tmp_path / "t.jpg")
    assert (tmp_path / "t.jpg").stat().st_size > 500
    assert 0.0 <= mean_brightness(tiny_clip) <= 1.0


def test_render_with_clip_source(tmp_path, click_wav, tiny_photos, tiny_clip):
    sources = tiny_photos[:2] + [tiny_clip] + tiny_photos[2:]
    grid = analyze(click_wav)
    p = MontageParams(fit_mode="fit_to_photos", pacing="constant", cut_unit="beat")
    p.output.fps = 24
    p.effects.dissolve_ms = 80
    edl = plan(p, grid, len(sources))
    r = Renderer(edl, p, sources, 320, 180)
    clip_shot = next(s for s in edl.shots if s.photo_idx == 2)
    f0 = int(round(clip_shot.start * 24))
    a = r.frame(f0 + 1)
    b = r.frame(f0 + 10)
    assert a.shape == (180, 320, 3) and b.shape == (180, 320, 3)
    # testsrc animates, so frames within the clip shot differ
    assert np.abs(a.astype(int) - b.astype(int)).mean() > 1.0
    # full render to mp4 works end-to-end with the clip in the middle
    out = tmp_path / "clip_montage.mp4"
    r.render(click_wav, out, preview=True)
    assert out.stat().st_size > 5_000
