import soundfile as sf

from nullmontage.audio import analyze, decode_for_mux, decode_to_wav, speed_filter


def test_speed_filter_chain():
    assert speed_filter(1.0, True, 48000) == []
    assert speed_filter(1.5, True, 48000) == ["-af", "atempo=1.5000"]
    assert speed_filter(3.0, True, 48000) == ["-af", "atempo=2.0,atempo=1.5000"]
    assert "asetrate=48000*1.5000" in speed_filter(1.5, False, 48000)[1]


def test_sped_up_track_is_shorter_and_faster(tmp_path, click_wav):
    fast = decode_to_wav(click_wav, tmp_path / "fast.wav", speed=1.5)
    info = sf.info(str(fast))
    assert abs(info.duration - 12.0 / 1.5) < 0.1
    grid = analyze(click_wav).scaled(1.5)
    assert 170 < grid.bpm < 190, grid.bpm
    assert abs(grid.duration - 8.0) < 0.05
    # scaled beats coincide with the true beats of the sped-up audio (period 0.5/1.5 s)
    import numpy as np
    b = np.array(grid.beats)
    period = 0.5 / 1.5
    assert np.percentile(np.abs(b / period - np.round(b / period)) * period, 90) < 0.02
    mux = decode_for_mux(click_wav, tmp_path / "mux.wav", speed=1.5, keep_pitch=False)
    assert abs(sf.info(str(mux)).duration - 8.0) < 0.1
