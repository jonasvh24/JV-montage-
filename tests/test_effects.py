import numpy as np
from PIL import Image

from nullmontage import effects as fx


def test_crop_zoom_shapes():
    base = Image.new("RGB", (int(320 * fx.HEADROOM) + 2, int(180 * fx.HEADROOM) + 2), (100, 50, 20))
    for z in (1.0, 1.05, 1.1):
        out = fx.crop_zoom(base, 320, 180, z, (0.5, -0.5), (0.1, 0.1))
        assert out.size == (320, 180)


def test_filters_preserve_shape_and_dtype():
    f = np.random.default_rng(0).integers(0, 255, (180, 320, 3), dtype=np.uint8)
    assert fx.apply_lut(f, fx.contrast_lut(0.5)).shape == f.shape
    assert fx.apply_vignette(f, fx.vignette_mask(320, 180, 0.5)).dtype == np.uint8
    g = fx.apply_grain(f, fx.grain_bank(320, 180, 0.5, n=2)[0])
    assert g.shape == f.shape and g.dtype == np.uint8
    assert fx.invert(f).max() <= 255
    assert fx.blend(f, fx.solid(320, 180, 0), 0.5).mean() < f.mean()


def test_render_card():
    c = fx.render_card("The struggle continues", 640, 360)
    assert c.shape == (360, 640, 3)
    # cream text and red bar should both be present
    assert (c[:, :, 0] > 180).any() and ((c[:, :, 0] > 150) & (c[:, :, 1] < 60)).any()
