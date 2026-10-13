from nullmontage.audio import analyze
from nullmontage.effects import render_card
from nullmontage.planner import plan
from nullmontage.presets import ASPECT_SIZES, MontageParams
from nullmontage.render import Renderer, render


def test_card_fits_vertical():
    import numpy as np
    c = render_card("MADE BY JVH", 270, 480, "plain")
    assert c.shape == (480, 270, 3)
    cols = np.nonzero((c > 200).any(axis=(0, 2)))[0]
    assert cols.min() > 5 and cols.max() < 265  # text not clipped at the edges


def test_render_vertical(tmp_path, click_wav, tiny_photos):
    grid = analyze(click_wav)
    p = MontageParams().with_preset("clean_beat")
    p.output.fps = 24
    assert p.output.aspect == "9:16" and ASPECT_SIZES[p.output.aspect] == (1080, 1920)
    edl = plan(p, grid, len(tiny_photos))
    r = Renderer(edl, p, tiny_photos, 180, 320)
    f = r.frame(int(round(edl.shots[1].start * 24)) + 1)
    assert f.shape == (320, 180, 3)
    out = tmp_path / "v.mp4"
    render(edl, p, tiny_photos, click_wav, out, preview=True)
    assert out.stat().st_size > 5_000
