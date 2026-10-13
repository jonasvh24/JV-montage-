import numpy as np

from nullmontage.audio import analyze


def test_click_track_beats_detected(click_wav):
    grid = analyze(click_wav)
    assert 110 < grid.bpm < 130, grid.bpm
    assert abs(grid.duration - 12.0) < 0.05
    # every detected beat should sit near a true beat (0.5 s period)
    beats = np.array(grid.beats)
    offsets = np.abs(beats / 0.5 - np.round(beats / 0.5)) * 0.5
    assert np.percentile(offsets, 90) < 0.03, offsets
    assert len(grid.beats) >= 20
    assert len(grid.downbeats) >= 5
    assert len(grid.half_beats) == 2 * len(grid.beats) - 1
    assert set(grid.sections) >= {"intro", "build", "climax", "outro", "peak"}
    s = grid.summary()
    assert s["n_beats"] == len(grid.beats)


def test_double_time_click_is_normalised(tmp_path):
    from tests.conftest import make_click_track

    wav = make_click_track(tmp_path / "fast.wav", bpm=180.0, seconds=12.0, accent_every=2)
    grid = analyze(wav)
    assert 85 < grid.bpm < 95, grid.bpm            # 180 BPM clicks read as a 90 BPM pulse
    beats = np.array(grid.beats)
    period = 60.0 / 90.0
    off = np.abs(beats / period - np.round(beats / period)) * period
    assert np.percentile(off, 90) < 0.03
    # the dropped clicks survive as half-beats, so half-beat cuts still hit every click
    assert len(grid.half_beats) == 2 * len(grid.beats) - 1
    # accented clicks (every 2nd) are the ones kept as beats
    assert np.mean(grid.beat_strength) > 0.5
