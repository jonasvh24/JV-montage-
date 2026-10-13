import numpy as np
import pytest

from nullmontage.audio import BeatGrid
from nullmontage.planner import plan
from nullmontage.presets import MontageParams


def synth_grid(bpm=120.0, seconds=180.0) -> BeatGrid:
    period = 60.0 / bpm
    beats = list(np.arange(0.0, seconds, period))
    mids = [(a + b) / 2 for a, b in zip(beats[:-1], beats[1:])]
    n = len(beats)
    rng = np.random.default_rng(1)
    strength = [1.0 if i % 4 == 0 else float(rng.uniform(0.2, 0.7)) for i in range(n)]
    energy = list(np.clip(np.linspace(0.2, 1.0, n), 0, 1))
    q = n // 4
    secs = {"intro": [beats[0], beats[q]], "build": [beats[q], beats[2 * q]], "climax": [beats[2 * q], beats[3 * q]],
            "outro": [beats[3 * q], seconds]}
    secs["peak"] = secs["climax"]
    return BeatGrid(duration=seconds, bpm=bpm, beats=beats, downbeats=beats[::4], half_beats=sorted(beats + mids),
                    beat_strength=strength, energy=energy, sections=secs)


def cuts_on_grid(edl, grid):
    ticks = np.array(grid.half_beats)
    for s in edl.shots:
        assert np.abs(ticks - s.start).min() < 1e-6, s
    for a, b in zip(edl.shots[:-1], edl.shots[1:]):
        assert abs(a.end - b.start) < 1e-6
        assert b.start > a.start


def test_fit_to_photos_uses_each_photo_once():
    grid = synth_grid()
    p = MontageParams(fit_mode="fit_to_photos", pacing="accelerate", cut_unit="beat")
    edl = plan(p, grid, 55)
    photos = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert sorted(photos) == list(range(55))
    cuts_on_grid(edl, grid)
    assert edl.audio_end <= grid.duration
    assert edl.fade_out_ms > 0
    assert edl.stats["photos_used"] == 55


def test_accelerate_shot_lengths_shrink():
    grid = synth_grid()
    p = MontageParams(fit_mode="fit_to_photos", pacing="accelerate", cut_unit="beat", accel_curve=0.5)
    edl = plan(p, grid, 55)
    lengths = [s.end - s.start for s in edl.shots]
    first, last = np.mean(lengths[:5]), np.mean(lengths[-5:])
    assert first > last * 2
    # non-increasing when smoothed
    third = len(lengths) // 3
    assert np.mean(lengths[:third]) >= np.mean(lengths[third:2 * third]) >= np.mean(lengths[2 * third:])


def test_fit_to_song_covers_track():
    grid = synth_grid(seconds=60.0)
    p = MontageParams(fit_mode="fit_to_song", pacing="constant", cut_unit="beat", allow_repeats=True)
    edl = plan(p, grid, 10)
    cuts_on_grid(edl, grid)
    assert edl.shots[-1].end >= grid.downbeats[-1] - 1e-6
    assert edl.fade_out_ms == 0
    photos = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert len(photos) > 10 and set(photos) == set(range(10))


def test_fit_to_song_no_repeats_spreads_photos():
    grid = synth_grid(seconds=60.0)
    p = MontageParams(fit_mode="fit_to_song", pacing="constant", cut_unit="beat", allow_repeats=False)
    edl = plan(p, grid, 10)
    photos = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    # monotone non-decreasing, every photo used, cuts still on every beat
    assert photos == sorted(photos) and set(photos) == set(range(10))
    assert len(photos) > 10


def test_alternate_halves_interleaves():
    grid = synth_grid()
    p = MontageParams(order="alternate_halves", pacing="constant", cut_unit="beat")
    edl = plan(p, grid, 6)
    assert edl.shots[0].card_text == "MADE BY JVH"
    assert [s.photo_idx for s in edl.shots if s.photo_idx is not None] == [0, 3, 1, 4, 2, 5]


def test_intertitles_and_effects():
    grid = synth_grid()
    p = MontageParams(pacing="constant", cut_unit="beat")
    p = p.with_preset("odessa_steps")
    p.intertitles.enabled = True
    p.intertitles.placement = "opening_and_closing"
    p.intertitles.cards = ["ARISE", "END"]
    edl = plan(p, grid, 20)
    assert edl.shots[0].card_text == "MADE BY JVH"
    assert edl.shots[1].card_text == "ARISE" and edl.shots[-1].card_text == "END"
    assert edl.stats["n_cards"] == 3
    assert any(s.flash == "black" for s in edl.shots)
    assert any(s.strobe for s in edl.shots)
    assert all(s.zoom_from >= 1.0 for s in edl.shots)
    cuts_on_grid(edl, grid)


def test_min_shot_respected():
    grid = synth_grid(bpm=180.0)
    p = MontageParams(pacing="constant", cut_unit="half_beat", min_shot_ms=300)
    edl = plan(p, grid, 20)
    assert all((s.end - s.start) >= 0.3 - 1e-6 for s in edl.shots)


def test_every_n_bars_cards():
    grid = synth_grid()
    p = MontageParams(fit_mode="fit_to_song", pacing="constant", cut_unit="beat")
    p.intertitles.enabled = True
    p.intertitles.placement = "every_n_bars"
    p.intertitles.every_n_bars = 4
    edl = plan(p, grid, 10)
    cards = [s for s in edl.shots if s.card_text]
    assert len(cards) >= 5
    assert all(c.on_downbeat for c in cards)


def test_intro_card_always_first():
    grid = synth_grid()
    p = MontageParams().with_preset("clean_beat")
    edl = plan(p, grid, 10)
    assert edl.shots[0].card_text == "MADE BY JVH"
    assert abs((edl.shots[0].end - edl.shots[0].start) - 4 * 0.5) < 1e-6
    photos = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert photos == list(range(10))
    # every photo lasts exactly one beat (default) with constant pacing and no effects
    lengths = {round(s.end - s.start, 3) for s in edl.shots[1:-1]}  # last shot may stretch to the downbeat
    assert lengths == {0.5}
    p.cut_unit = "quarter_beat"
    edl = plan(p, grid, 10)
    assert {round(s.end - s.start, 3) for s in edl.shots[1:-1]} == {0.125}
    assert edl.stats["max_cut_offset_ms"] == 0
    assert all(s.zoom_from == 1.0 and s.flash is None and not s.strobe for s in edl.shots)
    # intro precedes any intertitle opening card, and can be disabled
    p.intertitles.enabled = True
    p.intertitles.placement = "opening"
    p.intertitles.cards = ["X"]
    edl = plan(p, grid, 10)
    assert [s.card_text for s in edl.shots[:2]] == ["MADE BY JVH", "X"]
    p.intro_card = ""
    edl = plan(p, grid, 10)
    assert edl.shots[0].card_text == "X"



def test_soviet_preset_keeps_order_and_is_clean():
    grid = synth_grid()
    p = MontageParams().with_preset("soviet_montage")
    assert p.order == "as_uploaded" and not p.intertitles.enabled and not p.effects.bw
    assert p.effects.flash_white == "off" and p.effects.punch_zoom == 0
    assert p.output.speed == 1.5 and p.cut_unit == "beat"
    edl = plan(p, grid, 12)
    assert [s.photo_idx for s in edl.shots if s.photo_idx is not None] == list(range(12))
    assert [s.card_text for s in edl.shots if s.card_text] == ["MADE BY JVH"]


def test_pictures_per_beat_options():
    grid = synth_grid()
    p = MontageParams().with_preset("clean_beat")
    expect = {"bar": 2.0, "two_beats": 1.0, "beat": 0.5, "half_beat": 0.25, "quarter_beat": 0.125}
    for unit, secs in expect.items():
        p.cut_unit = unit
        edl = plan(p, grid, 10)
        assert {round(s.end - s.start, 3) for s in edl.shots[1:-1]} == {secs}, unit


def test_hard_beat_flashes_are_selective():
    grid = synth_grid()  # strength 1.0 on every 4th beat, 0.2-0.7 elsewhere; energy ramps smoothly
    p = MontageParams().with_preset("clean_beat")
    p.effects.flash_white = "hard_beats"
    p.effects.hard_beat_threshold = 0.85  # accents are the top 25% (tied at 1.0), average rank 0.875
    edl = plan(p, grid, 40)
    flashed = [s for s in edl.shots if s.flash == "white"]
    assert flashed and all(s.strength >= 0.9 for s in flashed)
    assert len(flashed) < len(edl.shots) / 2
    p.effects.hard_beat_threshold = 0.0
    assert sum(1 for s in plan(p, grid, 40).shots if s.flash) > len(flashed)
    # a sudden energy jump counts as a hard beat even with a weak onset
    g2 = synth_grid()
    g2.beat_strength = [0.1] * len(g2.beats)
    g2.energy = [0.1] * 20 + [1.0] * (len(g2.beats) - 20)
    p.effects.hard_beat_threshold = 0.9
    edl = plan(p, g2, 40)
    jumps = [s for s in edl.shots if s.flash]
    assert len(jumps) == 1 and abs(jumps[0].start - g2.beats[20]) < 1e-6


@pytest.mark.parametrize("target", [15.0, 30.0, 47.0])
def test_fit_to_duration_hits_target_length(target):
    grid = synth_grid(seconds=120.0)
    p = MontageParams(fit_mode="fit_to_duration", target_duration_s=target, pacing="constant", cut_unit="beat", allow_repeats=True)
    edl = plan(p, grid, 10)
    cuts_on_grid(edl, grid)
    # within one bar (2 s at 120 BPM) of the requested length, ending on a downbeat
    assert abs(edl.duration - target) <= 2.0 + 1e-6
    assert any(abs(d - edl.duration) < 1e-6 for d in grid.downbeats)
    assert edl.audio_end <= grid.duration and edl.fade_out_ms > 0
    photos = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert len(photos) > 10 and set(photos) == set(range(10))


def test_fit_to_duration_exact_without_downbeat_snap():
    grid = synth_grid(seconds=120.0)
    p = MontageParams(fit_mode="fit_to_duration", target_duration_s=20.25, end_on_downbeat=False, cut_unit="beat")
    edl = plan(p, grid, 55)
    assert abs(edl.duration - 20.25) < 0.26  # nearest quarter-beat tick


def test_fit_to_duration_no_repeats_holds_photos():
    grid = synth_grid(seconds=120.0)
    p = MontageParams(fit_mode="fit_to_duration", target_duration_s=60.0, allow_repeats=False, cut_unit="beat")
    edl = plan(p, grid, 10)
    photos = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert photos == sorted(photos) and set(photos) == set(range(10))


def test_fit_to_duration_longer_than_song_is_capped_by_audio():
    grid = synth_grid(seconds=30.0)
    p = MontageParams(fit_mode="fit_to_duration", target_duration_s=90.0, cut_unit="beat")
    edl = plan(p, grid, 10)
    assert edl.audio_end <= grid.duration + 1e-6
    assert edl.duration <= grid.duration + 1e-6


def test_rhythmic_preset_follows_energy_and_keeps_order():
    grid = synth_grid()
    p = MontageParams().with_preset("rhythmic_montage")
    assert p.pacing == "follow_energy" and p.cut_unit == "beat" and p.order == "as_uploaded"
    assert not p.intertitles.enabled and not p.effects.bw and p.output.speed == 1.0
    assert p.effects.flash_white == "hard_beats" and p.effects.punch_zoom > 0
    p.fit_mode = "fit_to_song"  # synthetic energy ramps 0.2 -> 1.0 across the whole track
    edl = plan(p, grid, 12)
    lengths = [s.end - s.start for s in edl.shots[1:-1]]
    assert len(set(round(l, 3) for l in lengths)) > 1, "energy-following pacing should vary shot length"
    assert np.mean(lengths[:5]) > np.mean(lengths[-5:]), "quiet start holds longer than the loud end"
    cuts_on_grid(edl, grid)
    seq = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert seq[:12] == list(range(12))
