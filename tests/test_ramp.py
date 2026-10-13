import json

import librosa
import numpy as np
import soundfile as sf

from nullmontage.audio import TimeMap, analyze, ramp_segments, render_ramped_audio
from nullmontage.planner import plan
from nullmontage.presets import MontageParams


def test_timemap_and_segments():
    segs = ramp_segments(2.0, 10.0, 12.0, 1.0, 1.5, 1.0, n=4)
    tm = TimeMap(segs)
    assert tm.to_output(0.0) == 0.0 and abs(tm.to_output(2.0) - 2.0) < 1e-9
    speeds = [v for _, _, v in segs]
    assert speeds[0] == 1.0 and speeds[-1] == 1.5 and speeds == sorted(speeds)
    assert tm.duration(12.0) < 12.0
    rev = ramp_segments(2.0, 10.0, 12.0, 1.0, 1.5, 1.0, reverse=True, n=4)
    assert [v for _, _, v in rev][0] == 1.5 and [v for _, _, v in rev][-1] == 1.0


def test_ramped_audio_keeps_beats_where_the_grid_says(tmp_path):
    from tests.conftest import make_click_track

    wav = make_click_track(tmp_path / "c.wav", bpm=120.0, seconds=20.0)
    base = analyze(wav)
    segs = ramp_segments(0.0, 20.0, base.duration, 1.0, 1.6, 1.5)
    mux = tmp_path / "m.wav"
    tmap = render_ramped_audio(wav, mux, segs, keep_pitch=True)
    grid = base.remapped(tmap)
    y, sr = sf.read(str(mux), dtype="float32")
    y = y.mean(axis=1)
    assert abs(len(y) / sr - grid.duration) < 0.05
    onsets = librosa.onset.onset_detect(y=y, sr=sr, hop_length=64, units="time")
    beats = np.array(grid.beats)
    d = np.abs(onsets[:, None] - beats[None, :]).min(axis=1)
    assert np.percentile(d, 90) < 0.025, d
    assert d[-10:].max() < 0.03  # no drift at the end of the ramp
    # the plan on the ramped grid accelerates: later shots shorter, all on the grid
    p = MontageParams(pacing="accelerate", cut_unit="beat", fit_mode="fit_to_song")
    edl = plan(p, grid, 8)
    lengths = [s.end - s.start for s in edl.shots if s.photo_idx is not None]
    assert np.mean(lengths[:4]) > np.mean(lengths[-4:])
    json.dumps(edl.to_dict())  # JSON-safe
