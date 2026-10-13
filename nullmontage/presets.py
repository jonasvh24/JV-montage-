"""MontageParams schema, style presets, and keyword fallback rules."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

StylePreset = Literal[
    "clean_beat", "soviet_montage", "rhythmic_montage", "eisenstein_collision", "vertov_kino_eye", "kuleshov_build", "odessa_steps", "intertitle_heavy", "custom"
]
FitMode = Literal["fit_to_photos", "fit_to_song", "fit_to_duration"]
CutUnit = Literal["bar", "two_beats", "beat", "half_beat", "quarter_beat"]  # pictures per beat: 1/4, 1/2, 1, 2, 4
Pacing = Literal["constant", "accelerate", "decelerate", "follow_energy", "pulse"]
Order = Literal["as_uploaded", "shuffle", "reverse", "alternate_halves", "brightness_sort"]
FlashMode = Literal["off", "hard_beats", "downbeats", "every_cut", "climax_only"]
Placement = Literal["opening", "between_sections", "every_n_bars", "closing", "opening_and_closing"]
Container = Literal["mp4", "mov"]
Codec = Literal["h264", "prores_422"]


class Effects(BaseModel):
    punch_zoom: float = Field(0.0, ge=0, le=1, description="Zoom-in punch on each cut; 0 disables")
    ken_burns: float = Field(0.0, ge=0, le=1, description="Slow drift/zoom over the shot")
    flash_white: FlashMode = "off"
    flash_black: FlashMode = "off"
    flash_frames: int = Field(2, ge=1, le=4)
    hard_beat_threshold: float = Field(0.8, ge=0, le=1, description="For hard_beats flashes: 0.8 = only the hardest 20% of beats (or biggest 20% energy jumps); 1 = only the single hardest")
    strobe: bool = Field(False, description="Rapid black-frame strobe during the climax section")
    strobe_section_pct: float = Field(0.15, ge=0, le=1, description="Fraction of the end of the timeline to strobe")
    shake: float = Field(0.0, ge=0, le=1, description="Camera jitter scaled by beat accent")
    bw: bool = False
    contrast: float = Field(0.0, ge=0, le=1, description="High-contrast crush")
    grain: float = Field(0.0, ge=0, le=1, description="Film grain amount")
    vignette: float = Field(0.0, ge=0, le=1)
    invert_on_accent: bool = Field(False, description="Invert colors on strongest downbeats")
    dissolve_ms: int = Field(0, ge=0, le=1000, description="Cross-dissolve length; 0 = hard cuts only")


class Intertitles(BaseModel):
    enabled: bool = False
    cards: list[str] = Field(default_factory=list)
    placement: Placement = "between_sections"
    every_n_bars: int = Field(8, ge=1, le=64)
    card_hold_beats: int = Field(4, ge=1, le=32)
    font_style: Literal["soviet_constructivist", "plain"] = "soviet_constructivist"


Aspect = Literal["9:16", "16:9", "4:5", "1:1"]
ASPECT_SIZES: dict[str, tuple[int, int]] = {"9:16": (1080, 1920), "16:9": (1920, 1080), "4:5": (1080, 1350), "1:1": (1080, 1080)}


class Output(BaseModel):
    aspect: Aspect = Field("9:16", description="Frame shape: 9:16 vertical (default), 16:9 landscape, 4:5, 1:1")
    container: Container = "mp4"
    codec: Codec = "h264"
    fps: Literal[24, 25, 30] = 30
    crf: int = Field(21, ge=10, le=35)
    audio_fade_out_ms: int = Field(1500, ge=0, le=10000)
    audio_start_s: float = Field(0.0, ge=0, description="Use the song from this many seconds in")
    audio_end_s: float = Field(0.0, ge=0, description="Stop using the song at this many seconds (0 = to the end)")
    speed: float = Field(1.0, ge=0.5, le=3.0, description="Song playback speed multiplier (1.5 = 50% faster)")
    keep_pitch: bool = Field(True, description="Time-stretch (keep pitch) instead of pitching the song up")


class MontageParams(BaseModel):
    style_preset: StylePreset = "custom"
    intro_card: str = Field("MADE BY JVH", max_length=80, description="Always shown as the first shot; empty disables")
    intro_hold_beats: int = Field(4, ge=1, le=32)
    # Rhythm
    fit_mode: FitMode = Field("fit_to_photos", description="fit_to_photos = each photo once, song trimmed; fit_to_song = fill the track; fit_to_duration = exactly target_duration_s long")
    target_duration_s: float = Field(30.0, ge=3, le=1800, description="Video length in seconds when fit_mode is fit_to_duration (cuts stay on beats; photos repeat or hold as needed)")
    cut_unit: CutUnit = "beat"
    pacing: Pacing = "constant"
    accel_curve: float = Field(0.5, ge=0, le=1, description="How hard acceleration ramps (0 gentle, 1 brutal)")
    song_speed_ramp: float = Field(1.25, ge=1.0, le=2.0, description="With accelerate/decelerate pacing the song's tempo also ramps to this multiple by the end (1 = off)")
    min_shot_ms: int = Field(120, ge=40, le=5000)
    start_offset_beats: int = Field(0, ge=0, le=64)
    end_on_downbeat: bool = True
    # Structure
    order: Order = "as_uploaded"
    allow_repeats: bool = True
    use_every_shot: bool = Field(False, description="With a set length / whole song: show every shot exactly once, spread across the time; AI-starred shots and key moments get the longer holds; shots flagged 'once' are never repeated in any mode")
    motif_repeat: int = Field(0, ge=0, le=3, description="Return to the first photo every N bars (0 = off)")
    hold_first_beats: int = Field(0, ge=0, le=32)
    hold_last_beats: int = Field(0, ge=0, le=32)
    seed: int = 7
    effects: Effects = Field(default_factory=Effects)
    intertitles: Intertitles = Field(default_factory=Intertitles)
    output: Output = Field(default_factory=Output)

    def with_preset(self, name: str) -> "MontageParams":
        data = self.model_dump()
        _deep_update(data, STYLE_PRESETS.get(name, {}))
        data["style_preset"] = name
        return MontageParams.model_validate(data)


def _deep_update(base: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


STYLE_PRESETS: dict[str, dict] = {
    "clean_beat": {
        "fit_mode": "fit_to_photos", "cut_unit": "beat", "pacing": "constant", "order": "as_uploaded",
        "hold_first_beats": 0, "end_on_downbeat": True,
        "effects": {"punch_zoom": 0.0, "ken_burns": 0.0, "flash_white": "off", "flash_black": "off", "strobe": False,
                     "shake": 0.0, "bw": False, "contrast": 0.0, "grain": 0.0, "vignette": 0.0, "invert_on_accent": False,
                     "dissolve_ms": 0},
        "intertitles": {"enabled": False, "font_style": "plain"},
        "output": {"speed": 1.0},
    },
    "soviet_montage": {
        "fit_mode": "fit_to_photos", "cut_unit": "beat", "pacing": "constant", "order": "as_uploaded",
        "hold_first_beats": 0, "end_on_downbeat": True,
        "effects": {"punch_zoom": 0.0, "ken_burns": 0.0, "flash_white": "off", "flash_black": "off", "strobe": False,
                     "shake": 0.0, "bw": False, "contrast": 0.0, "grain": 0.0, "vignette": 0.0, "invert_on_accent": False,
                     "dissolve_ms": 0},
        "intertitles": {"enabled": False, "font_style": "plain"},
        "output": {"speed": 1.5, "keep_pitch": True},
    },
    "rhythmic_montage": {
        "fit_mode": "fit_to_photos", "cut_unit": "beat", "pacing": "follow_energy", "order": "as_uploaded",
        "hold_first_beats": 0, "end_on_downbeat": True,
        "effects": {"punch_zoom": 0.25, "ken_burns": 0.0, "flash_white": "hard_beats", "flash_black": "off",
                     "hard_beat_threshold": 0.85, "strobe": False, "shake": 0.0, "bw": False, "contrast": 0.0,
                     "grain": 0.0, "vignette": 0.0, "invert_on_accent": False, "dissolve_ms": 0},
        "intertitles": {"enabled": False, "font_style": "plain"},
        "output": {"speed": 1.0},
    },
    "eisenstein_collision": {
        "cut_unit": "beat", "pacing": "accelerate", "accel_curve": 0.6, "order": "alternate_halves",
        "effects": {"punch_zoom": 0.4, "flash_white": "downbeats", "shake": 0.3, "contrast": 0.4, "grain": 0.3, "bw": True},
        "intertitles": {"enabled": True, "placement": "opening_and_closing"},
    },
    "vertov_kino_eye": {
        "cut_unit": "beat", "pacing": "constant", "order": "as_uploaded",
        "effects": {"punch_zoom": 0.5, "ken_burns": 0.3, "bw": True, "grain": 0.5, "vignette": 0.4, "shake": 0.15},
    },
    "kuleshov_build": {
        "cut_unit": "bar", "pacing": "accelerate", "accel_curve": 0.35, "order": "as_uploaded",
        "hold_first_beats": 8,
        "effects": {"punch_zoom": 0.2, "ken_burns": 0.5, "dissolve_ms": 0, "vignette": 0.3},
    },
    "odessa_steps": {
        "cut_unit": "half_beat", "pacing": "accelerate", "accel_curve": 0.85, "order": "as_uploaded",
        "effects": {"punch_zoom": 0.6, "flash_black": "downbeats", "strobe": True, "strobe_section_pct": 0.15,
                     "shake": 0.5, "bw": True, "contrast": 0.6, "grain": 0.4},
    },
    "intertitle_heavy": {
        "cut_unit": "beat", "pacing": "pulse", "order": "as_uploaded",
        "effects": {"punch_zoom": 0.3, "bw": True, "grain": 0.3, "vignette": 0.3},
        "intertitles": {"enabled": True, "placement": "every_n_bars", "every_n_bars": 4, "card_hold_beats": 4},
    },
    "custom": {},
}

PRESET_DESCRIPTIONS = {
    "clean_beat": "No effects: one photo per beat, hard cuts, colour, your order, intro card then straight into it.",
    "soviet_montage": "Quicker pace: song sped up 1.5x with one photo on every beat, your order kept, no cards, no effects.",
    "rhythmic_montage": "Cuts follow the music's energy: long holds in quiet passages, half-beat cuts when it hits; white flash on the hardest beats, light punch zoom, colour, your order, no cards.",
    "eisenstein_collision": "A/B collision of two photo halves, accelerating beat cuts, white flashes on downbeats, B&W high contrast.",
    "vertov_kino_eye": "Relentless constant cut on every beat, kino-eye punch zooms, grainy B&W with vignette.",
    "kuleshov_build": "Slow bar-length holds that tighten into beat cuts; gentle drift, mostly clean image.",
    "odessa_steps": "Escalation to half-beat cuts, black flashes, strobe finale, shake and crushed B&W.",
    "intertitle_heavy": "Pulse rhythm with constructivist title cards every four bars.",
    "custom": "Start from defaults and set everything yourself.",
}

# Keyword fallback: (regex, dotted path, value)
KEYWORD_RULES: list[tuple[str, str, object]] = [
    (r"\b(accelerat|faster and faster|speed(s)? up|build(s)? up|escalat|climax)", "pacing", "accelerate"),
    (r"\b(decelerat|slow(s)? down|wind(s)? down)", "pacing", "decelerate"),
    (r"\b(constant|steady|same speed|relentless)", "pacing", "constant"),
    (r"\b(follow the (energy|music)|dynamic|with the energy)", "pacing", "follow_energy"),
    (r"\b(pulse|long short|alternate long)", "pacing", "pulse"),
    (r"\b(every beat|on the beat|each beat|per beat)", "cut_unit", "beat"),
    (r"\b(half[- ]beat|double[- ]time|twice per beat|eighth)", "cut_unit", "half_beat"),
    (r"\b(quarter[- ]beat|4 per beat|four per beat|as fast as possible|rapid[- ]fire)", "cut_unit", "quarter_beat"),
    (r"\b(quicker|faster cuts|quick cuts|faster pictures|pictures faster|more cuts)", "cut_unit", "half_beat"),
    (r"\b(every (two|2) beats|every other beat|half as many)", "cut_unit", "two_beats"),
    (r"\b(every bar|per bar|each bar|slow cuts|on the bar)", "cut_unit", "bar"),
    (r"\b(one|1) (picture|photo|image) per beat\b", "cut_unit", "beat"),
    (r"\b(two|2) (pictures|photos|images) per beat\b", "cut_unit", "half_beat"),
    (r"\b(four|4) (pictures|photos|images) per beat\b", "cut_unit", "quarter_beat"),
    (r"\b(whole song|entire song|full song|full track|whole track|fit the song)", "fit_mode", "fit_to_song"),
    (r"\b(end when photos|no repeats?|each photo once|only once|trim the (song|music))", "fit_mode", "fit_to_photos"),
    (r"\b(no repeats?|each photo once|only once)", "allow_repeats", False),
    (r"\b(repeat photos|reuse photos|loop photos)", "allow_repeats", True),
    (r"\b(every|all|each) (shot|clip|photo|picture|image)s? (exactly )?once\b|\buse (all|every) (of )?(the )?(shot|clip|photo|picture|image)s?\b|\bspread (the )?(shot|clip|photo)s? (over|across)", "use_every_shot", True),
    (r"\b(collision|eisenstein|juxtapos|alternate|intercut|a/b)", "order", "alternate_halves"),
    (r"\b(shuffle|random order|randomi[sz]e)", "order", "shuffle"),
    (r"\b(reverse order|backwards)", "order", "reverse"),
    (r"\b(dark to light|light to dark|by brightness)", "order", "brightness_sort"),
    (r"\b(black and white|black & white|b&w|b/w|monochrome|greyscale|grayscale)", "effects.bw", True),
    (r"\b(colou?r(ful)?|keep colou?r|in colou?r)", "effects.bw", False),
    (r"\b(flash(es)?|flashing)[^.]{0,30}\b(hard(est)? beats?|drops?|big(gest)? (changes?|hits?)|strong(est)? beats?|accents?)", "effects.flash_white", "hard_beats"),
    (r"\b(white flash|flash(es)? of white|flash frames?|flashing)", "effects.flash_white", "downbeats"),
    (r"\b(black flash|flash(es)? to black|blink)", "effects.flash_black", "downbeats"),
    (r"\b(strobe|stroboscop)", "effects.strobe", True),
    (r"\b(no strobe)", "effects.strobe", False),
    (r"\b(shake|shaky|jitter|handheld|tremble)", "effects.shake", 0.5),
    (r"\b(grain|grainy|film look|16 ?mm|35 ?mm|old film)", "effects.grain", 0.5),
    (r"\b(high contrast|crushed|harsh|punchy)", "effects.contrast", 0.6),
    (r"\b(vignette)", "effects.vignette", 0.5),
    (r"\b(zoom|punch)", "effects.punch_zoom", 0.6),
    (r"\b(no zoom|static|still frames)", "effects.punch_zoom", 0.0),
    (r"\b(ken burns|drift|slow pan|slow zoom)", "effects.ken_burns", 0.5),
    (r"\b(invert)", "effects.invert_on_accent", True),
    (r"\b(dissolve|crossfade|cross-fade|soft cuts)", "effects.dissolve_ms", 250),
    (r"\b(hard cuts?|no transitions?|no dissolve)", "effects.dissolve_ms", 0),
    (r"\b(intertitle|title cards?|text cards?|captions?)", "intertitles.enabled", True),
    (r"\b(no (intertitles?|title cards?|text))", "intertitles.enabled", False),
    (r"\b(brutal|aggressive|violent|hard)", "accel_curve", 0.85),
    (r"\b(gentle|soft|subtle|slow build)", "accel_curve", 0.3),
    (r"\b(vertical|portrait|9:16|reels?|tiktok|shorts|stories)", "output.aspect", "9:16"),
    (r"\b(landscape|horizontal|16:9|widescreen|youtube)", "output.aspect", "16:9"),
    (r"\b(square|1:1)", "output.aspect", "1:1"),
    (r"\b(4:5|instagram post)", "output.aspect", "4:5"),
    (r"\b(\.mov|quicktime|prores)", "output.container", "mov"),
    (r"\b(prores)", "output.codec", "prores_422"),
    (r"\b(mp4|h\.?264)", "output.container", "mp4"),
]

PRESET_KEYWORDS = [
    (r"\b(odessa|steps|battleship|potemkin)", "odessa_steps"),
    (r"\b(rhythmic|rhythm montage|follow the (beat|rhythm))", "rhythmic_montage"),
    (r"\b(soviet|russian montage|classic montage)", "soviet_montage"),
    (r"\b(no effects|clean|plain|simple|just (the )?cuts|only cuts)", "clean_beat"),
    (r"\b(vertov|kino[- ]?eye|man with a movie camera)", "vertov_kino_eye"),
    (r"\b(kuleshov)", "kuleshov_build"),
    (r"\b(eisenstein|collision|intellectual montage)", "eisenstein_collision"),
    (r"\b(intertitle heavy|lots of (titles|text))", "intertitle_heavy"),
]


def apply_keywords(params: MontageParams, notes: str) -> tuple[MontageParams, list[str]]:
    """Deterministic fallback: apply keyword rules to params. Returns new params + list of changes."""
    text = notes.lower()
    data = params.model_dump()
    changes: list[str] = []
    for rx, preset in PRESET_KEYWORDS:
        if re.search(rx, text):
            _deep_update(data, STYLE_PRESETS[preset])
            data["style_preset"] = preset
            changes.append(f"style_preset = {preset}")
            break
    for rx, path, value in KEYWORD_RULES:
        if re.search(rx, text):
            node = data
            parts = path.split(".")
            for p in parts[:-1]:
                node = node[p]
            if node.get(parts[-1]) != value:
                node[parts[-1]] = value
                changes.append(f"{path} = {value}")
    # which part of the song: "song from 0:30 to 1:15", "use the track from 45 s to 1:30", "start (the song) at 0:32", "skip 10 seconds"
    _t = r"(?:(\d+):(\d\d)|(\d+(?:[.,]\d+)?)\s*(?:s|sec|secs|seconds?))"
    def _secs(mm, base):
        if mm.group(base):
            return int(mm.group(base)) * 60 + int(mm.group(base + 1))
        return float(mm.group(base + 2).replace(",", "."))
    m = re.search(r"\b(?:song|track|music|audio)\b[^.]{0,20}?\bfrom\s+" + _t + r"\s+(?:to|until|till|-|–)\s+" + _t, text)
    if m:
        a, b = _secs(m, 1), _secs(m, 4)
        if b > a:
            data["output"]["audio_start_s"], data["output"]["audio_end_s"] = a, b
            changes.append(f"output.audio_start_s = {a}"); changes.append(f"output.audio_end_s = {b}")
    else:
        m = re.search(r"\b(?:start(?:s|ing)?(?: the)?(?: song| track| music)? at|from)\s+" + _t + r"\b(?! (?:to|until|till))", text)
        if m and re.search(r"\b(song|track|music|audio|drop|chorus|intro)\b", text):
            a = _secs(m, 1)
            data["output"]["audio_start_s"] = a
            changes.append(f"output.audio_start_s = {a}")
        m = re.search(r"\bskip(?: the)?(?: first)?\s+" + _t, text) or re.search(r"\bskip(?: the)? (?:song |track |music )?intro(?: of)?\s+" + _t, text)
        if m:
            a = _secs(m, 1)
            data["output"]["audio_start_s"] = a
            changes.append(f"output.audio_start_s = {a}")
    # "every N bars" for intertitles
    m = re.search(r"every (\d+) bars?", text)
    if m:
        data["intertitles"]["placement"] = "every_n_bars"
        data["intertitles"]["every_n_bars"] = max(1, min(64, int(m.group(1))))
        changes.append(f"intertitles.every_n_bars = {m.group(1)}")
    # spelled-out counts ("one minute", "a minute and a half", "half a minute", "ninety seconds") -> digits
    _words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
    text = re.sub(r"\bhalf (?:a|an) (?:minute|min)\b", "30 seconds", text)
    text = re.sub(r"\b(a|an|one) (?:minute|min) and a half\b", "90 seconds", text)
    text = re.sub(r"\b(" + "|".join(_words) + r")\s+(min(?:ute)?s?|s(?:ec(?:ond)?s?)?)\b", lambda mm: f"{_words[mm.group(1)]} {mm.group(2)}", text)
    m = re.search(r"(\d+)\s*(?:min(?:ute)?s?)\s*(?:and\s*)?(\d+)?\s*(?:s(?:ec(?:ond)?s?)?)?\b|(\d+):(\d\d)\b(?!\s*aspect)|(\d+(?:[.,]\d+)?)\s*(?:s|sec|secs|seconds?)\s*(?:long|total|video|montage|max|exactly)?\b", text)
    if m and re.search(r"\b(long|length|duration|lasts?|seconds?|secs?|minutes?|mins?|runtime|total)\b", text) and not re.search(r"\b(skip|intro|offset|start at|fade|dissolve|flash)\b[^.]{0,20}" + re.escape(m.group(0)), text):
        if m.group(1):
            secs = int(m.group(1)) * 60 + int(m.group(2) or 0)
        elif m.group(3):
            secs = int(m.group(3)) * 60 + int(m.group(4))
        else:
            secs = float(m.group(5).replace(",", "."))
        if 3 <= secs <= 1800:
            data["fit_mode"] = "fit_to_duration"
            data["target_duration_s"] = float(secs)
            changes.append(f"fit_mode = fit_to_duration, target_duration_s = {secs:g}")
    m = re.search(r"(?:speed|tempo|song|music|track)[^.\d]{0,20}(\d+(?:[.,]\d+)?)\s*[x×]|(\d+(?:[.,]\d+)?)\s*[x×]\s*(?:speed|faster|tempo)", text)
    if m:
        val = float((m.group(1) or m.group(2)).replace(",", "."))
        if 0.5 <= val <= 3.0:
            data["output"]["speed"] = val
            changes.append(f"output.speed = {val}")
    if re.search(r"\b(speed up|faster song|faster music|speed the song)", text) and data["output"]["speed"] == 1.0:
        data["output"]["speed"] = 1.25
        changes.append("output.speed = 1.25")
    if re.search(r"\b(chipmunk|pitch(ed)? up|nightcore)", text):
        data["output"]["keep_pitch"] = False
        changes.append("output.keep_pitch = False")
    m = re.search(r"intro (?:card |text |says? )?[\"“”']([^\"“”']{1,80})[\"“”']", notes, re.I)
    if m:
        data["intro_card"] = m.group(1)
        changes.append(f"intro_card = {m.group(1)}")
    if re.search(r"\bno intro\b", text):
        data["intro_card"] = ""
        changes.append("intro_card = (none)")
    # quoted strings become intertitle cards
    quoted = re.findall(r"[\"“”']([^\"“”']{2,60})[\"“”']", notes)
    if quoted:
        data["intertitles"]["cards"] = quoted
        data["intertitles"]["enabled"] = True
        changes.append(f"intertitles.cards = {quoted}")
    return MontageParams.model_validate(data), changes
