"""Director's notes -> MontageParams, via Claude structured output with a keyword fallback."""
from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel

from .presets import (PRESET_DESCRIPTIONS, STYLE_PRESETS, MontageParams, _deep_update, apply_keywords)

DEFAULT_MODEL = "claude-opus-5-5"


class DirectorPatch(BaseModel):
    """Only the fields Claude wants to change. null = leave as is."""
    style_preset: Literal["clean_beat", "soviet_montage", "rhythmic_montage", "eisenstein_collision", "vertov_kino_eye", "kuleshov_build", "odessa_steps", "intertitle_heavy", "custom"] | None = None
    intro_card: str | None = None
    intro_hold_beats: int | None = None
    fit_mode: Literal["fit_to_photos", "fit_to_song", "fit_to_duration"] | None = None
    target_duration_s: float | None = None
    cut_unit: Literal["bar", "two_beats", "beat", "half_beat", "quarter_beat"] | None = None
    pacing: Literal["constant", "accelerate", "decelerate", "follow_energy", "pulse"] | None = None
    accel_curve: float | None = None
    song_speed_ramp: float | None = None
    min_shot_ms: int | None = None
    start_offset_beats: int | None = None
    end_on_downbeat: bool | None = None
    order: Literal["as_uploaded", "shuffle", "reverse", "alternate_halves", "brightness_sort"] | None = None
    allow_repeats: bool | None = None
    motif_repeat: int | None = None
    hold_first_beats: int | None = None
    hold_last_beats: int | None = None
    punch_zoom: float | None = None
    ken_burns: float | None = None
    flash_white: Literal["off", "hard_beats", "downbeats", "every_cut", "climax_only"] | None = None
    flash_black: Literal["off", "hard_beats", "downbeats", "every_cut", "climax_only"] | None = None
    hard_beat_threshold: float | None = None
    flash_frames: int | None = None
    strobe: bool | None = None
    strobe_section_pct: float | None = None
    shake: float | None = None
    bw: bool | None = None
    contrast: float | None = None
    grain: float | None = None
    vignette: float | None = None
    invert_on_accent: bool | None = None
    dissolve_ms: int | None = None
    intertitles_enabled: bool | None = None
    intertitle_cards: list[str] | None = None
    intertitle_placement: Literal["opening", "between_sections", "every_n_bars", "closing", "opening_and_closing"] | None = None
    intertitle_every_n_bars: int | None = None
    card_hold_beats: int | None = None
    aspect: Literal["9:16", "16:9", "4:5", "1:1"] | None = None
    speed: float | None = None
    keep_pitch: bool | None = None
    audio_start_s: float | None = None
    audio_end_s: float | None = None
    use_every_shot: bool | None = None
    container: Literal["mp4", "mov"] | None = None
    codec: Literal["h264", "prores_422"] | None = None
    fps: Literal[24, 25, 30] | None = None
    rationale: str


_EFFECT_KEYS = {"punch_zoom", "ken_burns", "flash_white", "flash_black", "flash_frames", "hard_beat_threshold", "strobe", "strobe_section_pct",
                "shake", "bw", "contrast", "grain", "vignette", "invert_on_accent", "dissolve_ms"}
_OUTPUT_KEYS = {"aspect", "container", "codec", "fps", "speed", "keep_pitch", "audio_start_s", "audio_end_s"}

SYSTEM = f"""You are the director's assistant for a Soviet-montage photo video editor. The editor cuts a sequence of still photos to
music so that every cut lands on a beat. You translate free-text director's notes into concrete edit parameters.

Montage vocabulary:
- The user's "soviet montage" preset means: quicker pace via sped-up song (1.5x) with a photo on every beat, uploaded order kept, no cards, no effects.
- Rhythmic montage: shot length follows the music's energy (pacing=follow_energy): long holds in quiet passages, half-beat cuts when it hits; white flash on the hardest beats, light punch zoom, colour, uploaded order, no cards.
- Eisenstein "montage of attractions"/collision: juxtapose two groups of images (order=alternate_halves), accelerate, hard cuts, flashes on downbeats.
- Vertov kino-eye: relentless constant cutting on every beat, punch zooms, grainy black and white.
- Kuleshov: slow holds (bar-length) that build meaning between images, then tighten.
- Odessa steps: escalation to half-beat cuts, black flashes, strobe finale, shake, crushed contrast.
- Intertitles: constructivist title cards (uppercase, cream on black with red bar) placed between sections or every N bars.

Parameter meanings:
- fit_mode: fit_to_photos = each photo once, music trimmed with a fade when photos run out; fit_to_song = fill the whole track (repeat photos if allow_repeats, else hold each photo across several beats); fit_to_duration = the video is exactly target_duration_s seconds (3..1800) long, cuts still on beats, photos repeat or hold as needed. Whenever the notes give a length ("30 seconds", "about a minute", "1:30"), set fit_mode=fit_to_duration and target_duration_s.
- cut_unit = pictures per beat: bar (1/4 per beat), two_beats (1/2), beat (1), half_beat (2), quarter_beat (4).
- use_every_shot: with fit_to_song / fit_to_duration, show every uploaded shot exactly once spread across the time (no repeats; important shots and clip key moments hold longer). Set when the notes say "use all the clips", "every shot once", "no repeats but fill the time".
- audio_start_s / audio_end_s: which part of the song to use, in seconds of the original track ("use the song from 0:45 to 1:30", "start at the drop at 32 s", "skip the first 10 seconds"); audio_end_s 0 = to the end. Don't confuse with the montage length.
- speed: song playback speed multiplier 0.5..3 (keep_pitch true = time-stretch, false = pitched up like nightcore). pacing: constant, accelerate (long to short), decelerate, follow_energy (louder = faster), pulse (long-short alternation).
- accel_curve 0..1: 0 gentle, 1 brutal (stays slow then crashes into rapid cuts at the end).
- song_speed_ramp 1..2: with accelerate pacing the music itself speeds up to this multiple by the end (1.25 default, 1 = off).
- punch_zoom/ken_burns/shake/contrast/grain/vignette are 0..1 strengths. flash_white/flash_black: off, hard_beats (only the hardest-hitting beats and biggest energy jumps/drops; hard_beat_threshold 0..1 sets how selective), downbeats, every_cut, climax_only. strobe applies to the last strobe_section_pct of the timeline. dissolve_ms 0 = hard cuts.
- aspect: 9:16 vertical (phones, Reels/TikTok/Shorts), 16:9 landscape (YouTube/TV), 4:5, 1:1.
- intro_card: text of the card that always opens the video (default "MADE BY JVH"); only change it if the notes ask.
- intertitle_cards: short uppercase slogans (2-5 words each). Write cards yourself if the notes imply titles but give none.

Presets: {json.dumps(PRESET_DESCRIPTIONS)}

Return only the fields that should change; leave everything else null. Keep numbers in range 0..1 for strengths, and be bold: Soviet montage is aggressive by nature. Explain in one or two sentences in `rationale`."""


def _patch_to_params(params: MontageParams, patch: DirectorPatch) -> tuple[MontageParams, list[str]]:
    data = params.model_dump()
    changes: list[str] = []
    d = patch.model_dump(exclude_none=True)
    d.pop("rationale", None)
    if "style_preset" in d:
        _deep_update(data, STYLE_PRESETS.get(d["style_preset"], {}))
        data["style_preset"] = d.pop("style_preset")
        changes.append(f"style_preset = {data['style_preset']}")
    for k, v in d.items():
        if k in _EFFECT_KEYS:
            if data["effects"].get(k) != v:
                data["effects"][k] = v
                changes.append(f"effects.{k} = {v}")
        elif k in _OUTPUT_KEYS:
            if data["output"].get(k) != v:
                data["output"][k] = v
                changes.append(f"output.{k} = {v}")
        elif k.startswith("intertitle") or k == "card_hold_beats":
            key = {"intertitles_enabled": "enabled", "intertitle_cards": "cards", "intertitle_placement": "placement",
                   "intertitle_every_n_bars": "every_n_bars", "card_hold_beats": "card_hold_beats"}[k]
            if data["intertitles"].get(key) != v:
                data["intertitles"][key] = v
                changes.append(f"intertitles.{key} = {v}")
            if key == "cards" and v:
                data["intertitles"]["enabled"] = True
        else:
            if data.get(k) != v:
                data[k] = v
                changes.append(f"{k} = {v}")
    # clamp floats/ints into schema bounds by validating field-by-field
    for k in ("accel_curve",):
        data[k] = min(1.0, max(0.0, float(data[k])))
    data["song_speed_ramp"] = min(2.0, max(1.0, float(data["song_speed_ramp"])))
    for k in ("punch_zoom", "ken_burns", "strobe_section_pct", "shake", "contrast", "grain", "vignette", "hard_beat_threshold"):
        data["effects"][k] = min(1.0, max(0.0, float(data["effects"][k])))
    data["effects"]["dissolve_ms"] = min(1000, max(0, int(data["effects"]["dissolve_ms"])))
    data["effects"]["flash_frames"] = min(4, max(1, int(data["effects"]["flash_frames"])))
    data["min_shot_ms"] = min(5000, max(40, int(data["min_shot_ms"])))
    data["target_duration_s"] = min(1800.0, max(3.0, float(data["target_duration_s"])))
    data["intro_hold_beats"] = min(32, max(1, int(data["intro_hold_beats"])))
    data["output"]["speed"] = min(3.0, max(0.5, float(data["output"]["speed"])))
    data["output"]["audio_start_s"] = max(0.0, float(data["output"]["audio_start_s"]))
    data["output"]["audio_end_s"] = max(0.0, float(data["output"]["audio_end_s"]))
    if data["output"]["audio_end_s"] <= data["output"]["audio_start_s"]:
        data["output"]["audio_end_s"] = 0.0
    for k, hi in (("motif_repeat", 3), ("hold_first_beats", 32), ("hold_last_beats", 32), ("start_offset_beats", 64)):
        data[k] = min(hi, max(0, int(data[k])))
    data["intertitles"]["every_n_bars"] = min(64, max(1, int(data["intertitles"]["every_n_bars"])))
    data["intertitles"]["card_hold_beats"] = min(32, max(1, int(data["intertitles"]["card_hold_beats"])))
    if data["output"]["codec"] == "prores_422":
        data["output"]["container"] = "mov"
    return MontageParams.model_validate(data), changes


def claude_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    # ant auth login profile on disk
    cfg = os.path.expanduser("~/.config/anthropic")
    return os.path.isdir(cfg) and any(os.scandir(cfg))


def interpret(params: MontageParams, notes: str, grid_summary: dict | None, n_photos: int) -> dict:
    """Returns {"params": MontageParams, "changes": [...], "source": "claude"|"keywords", "rationale": str}."""
    notes = (notes or "").strip()
    if not notes:
        return {"params": params, "changes": [], "source": "none", "rationale": ""}
    if claude_available():
        try:
            return _interpret_claude(params, notes, grid_summary, n_photos)
        except Exception as exc:  # fall back, but say so
            p, ch = apply_keywords(params, notes)
            return {"params": p, "changes": ch, "source": "keywords", "rationale": f"Claude unavailable ({type(exc).__name__}: {exc}); used keyword rules."}
    p, ch = apply_keywords(params, notes)
    return {"params": p, "changes": ch, "source": "keywords", "rationale": "No Anthropic credentials found; used keyword rules."}


def _interpret_claude(params: MontageParams, notes: str, grid_summary: dict | None, n_photos: int) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("NULLMONTAGE_MODEL", DEFAULT_MODEL)
    user = (
        f"Director's notes:\n{notes}\n\n"
        f"Current parameters (JSON):\n{json.dumps(params.model_dump(), indent=1)}\n\n"
        f"Track analysis: {json.dumps(grid_summary or {})}\n"
        f"Number of photos: {n_photos}\n"
    )
    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=DirectorPatch,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request")
    patch: DirectorPatch = response.parsed_output
    p, ch = _patch_to_params(params, patch)
    return {"params": p, "changes": ch, "source": "claude", "rationale": patch.rationale, "model": model}
