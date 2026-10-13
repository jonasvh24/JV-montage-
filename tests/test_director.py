from nullmontage.director import DirectorPatch, _patch_to_params
from nullmontage.presets import MontageParams, apply_keywords


def test_keyword_fallback():
    p, ch = apply_keywords(MontageParams(), "Make it accelerate, black and white, grainy, with white flashes and the whole song, cards every 8 bars")
    assert p.pacing == "accelerate" and p.effects.bw and p.effects.grain > 0
    assert p.effects.flash_white == "downbeats" and p.fit_mode == "fit_to_song"
    assert p.intertitles.placement == "every_n_bars" and p.intertitles.every_n_bars == 8
    assert ch


def test_patch_application_and_clamping():
    patch = DirectorPatch(style_preset="odessa_steps", shake=3.0, intertitle_cards=["ARISE"], codec="prores_422", rationale="x")
    p, ch = _patch_to_params(MontageParams(), patch)
    assert p.style_preset == "odessa_steps" and p.effects.strobe
    assert p.effects.shake == 1.0
    assert p.intertitles.enabled and p.intertitles.cards == ["ARISE"]
    assert p.output.container == "mov" and p.output.codec == "prores_422"
    assert any("shake" in c for c in ch)


def test_keyword_length_phrases_set_fixed_duration():
    from nullmontage.presets import MontageParams, apply_keywords

    for notes, secs in [("make it 30 seconds long", 30), ("about 1 minute 15 seconds total", 75), ("length 1:30, every beat", 90),
                        ("45 secs long please", 45), ("2 minutes long", 120), ("one minute long", 60), ("a minute and a half", 90),
                        ("half a minute", 30), ("ninety seconds long", 90), ("30 secs long", 30)]:
        p, ch = apply_keywords(MontageParams(), notes)
        assert p.fit_mode == "fit_to_duration" and p.target_duration_s == secs, (notes, ch)
    p, _ = apply_keywords(MontageParams(), "skip 10 s of the intro, black and white")
    assert p.fit_mode == "fit_to_photos"


def test_keyword_song_range():
    p, ch = apply_keywords(MontageParams(), "use the song from 0:30 to 1:15, black and white")
    assert (p.output.audio_start_s, p.output.audio_end_s) == (30, 75), ch
    p, _ = apply_keywords(MontageParams(), "start the song at 45 s, one picture per beat")
    assert p.output.audio_start_s == 45 and p.output.audio_end_s == 0
    p, _ = apply_keywords(MontageParams(), "skip the first 10 seconds")
    assert p.output.audio_start_s == 10
    p, _ = apply_keywords(MontageParams(), "make it 30 seconds long")
    assert p.output.audio_start_s == 0 and p.fit_mode == "fit_to_duration" and p.target_duration_s == 30


def test_patch_song_range_clamped():
    p, _ = _patch_to_params(MontageParams(), DirectorPatch(audio_start_s=40, audio_end_s=20, rationale="x"))
    assert p.output.audio_start_s == 40 and p.output.audio_end_s == 0
    p, _ = _patch_to_params(MontageParams(), DirectorPatch(audio_start_s=20, audio_end_s=50, rationale="x"))
    assert (p.output.audio_start_s, p.output.audio_end_s) == (20, 50)
