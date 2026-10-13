"""Shot analysis (key moments, must-include, once) and how the planner honours it."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from nullmontage import shots as shots_mod
from nullmontage.audio import analyze
from nullmontage.planner import plan
from nullmontage.presets import MontageParams, apply_keywords
from nullmontage.render import Renderer
from tests.test_planner import cuts_on_grid, synth_grid


def _job(tmp_path, paths):
    pdir = tmp_path / "photos"; pdir.mkdir(exist_ok=True)
    names = []
    for p in paths:
        (pdir / p.name).write_bytes(p.read_bytes()); names.append(p.name)
    return SimpleNamespace(id="t", dir=tmp_path, photo_dir=pdir, photos=names)


def test_video_duration_and_heuristic_key_moment(tmp_path, tiny_clip, tiny_photos):
    d = shots_mod.video_duration(tiny_clip)
    assert d is not None and 1.8 <= d <= 2.2
    job = _job(tmp_path, [tiny_photos[0], tiny_clip])
    res = shots_mod.analyze_shots(job)  # no credentials in tests -> heuristic
    assert res["source"] == "heuristic"
    clip = res["shots"][tiny_clip.name]
    assert clip["must_include"] and clip["key_at"] is not None and 0 <= clip["key_at"] <= d
    assert res["shots"][tiny_photos[0].name]["key_at"] is None
    # editor overrides survive a re-analysis
    shots_mod.set_user_fields(job, tiny_photos[0].name, {"must_include": True, "once": True})
    shots_mod.set_user_fields(job, tiny_clip.name, {"key_at": 1.5})
    res2 = shots_mod.analyze_shots(job)
    assert res2["shots"][tiny_photos[0].name]["must_include"] and res2["shots"][tiny_photos[0].name]["once"]
    assert res2["shots"][tiny_clip.name]["key_at"] == 1.5
    info = shots_mod.shot_info_for_plan(job)
    assert info[0]["must"] and info[0]["once"] and info[1]["key_at"] == 1.5 and info[1]["duration"] == d


def test_every_shot_once_fills_the_set_time():
    grid = synth_grid()
    p = MontageParams().with_preset("clean_beat")
    p.fit_mode, p.target_duration_s, p.use_every_shot = "fit_to_duration", 30.0, True
    n = 12
    info = [{"weight": 3, "must": i in (2, 7), "once": False, "key_at": 0.8 if i == 5 else None, "duration": 2.0 if i == 5 else None} for i in range(n)]
    edl = plan(p, grid, n, None, info)
    seq = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert seq == list(range(n))  # every shot exactly once, in order
    assert abs(edl.shots[-1].end - 30.0) <= 2.0
    cuts_on_grid(edl, grid)
    lens = {s.photo_idx: s.end - s.start for s in edl.shots if s.photo_idx is not None}
    assert lens[2] >= max(lens[i] for i in range(n) if i not in (2, 7, 5))  # must-include shots hold longest
    assert edl.stats["photos_used"] == n and edl.stats["must_missing"] == []
    # the clip's key moment lands inside its shot
    s5 = next(s for s in edl.shots if s.photo_idx == 5)
    assert s5.src_offset >= 0 and s5.src_offset <= 0.8 <= s5.src_offset + (s5.end - s5.start) + 1e-6


def test_every_shot_drops_filler_when_too_many():
    grid = synth_grid()
    p = MontageParams().with_preset("clean_beat")
    p.fit_mode, p.target_duration_s, p.use_every_shot, p.min_shot_ms = "fit_to_duration", 5.0, True, 250
    n = 60
    info = [{"weight": 1, "must": i == 59, "once": False, "key_at": None, "duration": None} for i in range(n)]
    edl = plan(p, grid, n, None, info)
    seq = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert len(seq) == len(set(seq)) < n and 59 in seq
    assert min(s.end - s.start for s in edl.shots if s.photo_idx is not None) >= 0.25 - 1e-6


def test_once_flag_is_not_repeated_in_cycle_mode():
    grid = synth_grid()
    p = MontageParams().with_preset("clean_beat")
    p.fit_mode, p.target_duration_s, p.allow_repeats = "fit_to_duration", 40.0, True
    n = 4
    info = [{"weight": 3, "must": False, "once": i == 1, "key_at": None, "duration": None} for i in range(n)]
    edl = plan(p, grid, n, None, info)
    seq = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert seq.count(1) == 1 and seq.count(0) > 1 and len(seq) > n


def test_must_include_shots_get_a_slot_in_repeat_mode():
    grid = synth_grid()
    p = MontageParams().with_preset("clean_beat")
    p.fit_mode, p.target_duration_s = "fit_to_duration", 4.0  # room for ~6 shots, 20 photos
    n = 20
    info = [{"weight": 2, "must": i == 19, "once": False, "key_at": None, "duration": None} for i in range(n)]
    edl = plan(p, grid, n, None, info)
    seq = [s.photo_idx for s in edl.shots if s.photo_idx is not None]
    assert 19 in seq and edl.stats["must_missing"] == []


def test_render_clip_from_key_moment(tmp_path, click_wav, tiny_photos, tiny_clip):
    sources = tiny_photos[:1] + [tiny_clip]
    grid = analyze(click_wav)
    p = MontageParams(fit_mode="fit_to_photos", pacing="constant", cut_unit="beat", intro_card="")
    p.output.fps = 24
    info = [None, {"weight": 5, "must": True, "once": False, "key_at": 1.6, "duration": 2.0}]
    edl = plan(p, grid, 2, None, info)
    clip_shot = next(s for s in edl.shots if s.photo_idx == 1)
    assert clip_shot.src_offset > 0.5
    r = Renderer(edl, p, sources, 320, 180)
    f0 = int(round(clip_shot.start * 24))
    a = r.frame(f0 + 1)
    # frame from the key-moment offset differs from the clip's first frame (testsrc animates)
    from nullmontage.effects import video_frame
    first = np.asarray(video_frame(tiny_clip, 320, 180, 0.0))
    assert np.abs(a.astype(int) - first.astype(int)).mean() > 1.0


def test_keyword_use_every_shot():
    p, ch = apply_keywords(MontageParams(), "use all the clips once, 30 seconds long")
    assert p.use_every_shot and p.fit_mode == "fit_to_duration" and p.target_duration_s == 30, ch
    p, _ = apply_keywords(MontageParams(), "every shot once, whole song")
    assert p.use_every_shot and p.fit_mode == "fit_to_song"
