"""Montage planner: MontageParams + BeatGrid + photos -> EDL (edit decision list).

All cuts land on the tick grid (quarter-beats). Bars = 16 ticks, beats = 4, half-beats = 2, quarter-beats = 1.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

import numpy as np

from .audio import BeatGrid
from .presets import MontageParams

LADDER = [1, 2, 4, 8, 16, 32]  # allowed shot lengths in ticks (quarter-beats)
SUB = 4  # ticks per beat
DEFAULT_CARDS = ["PROLOGUE", "THE STRUGGLE", "THE CLIMAX", "THE END"]


@dataclass
class Shot:
    start: float
    end: float
    photo_idx: int | None = None      # None for intertitle cards
    card_text: str | None = None
    zoom_from: float = 1.0
    zoom_to: float = 1.0
    pan: tuple[float, float] = (0.0, 0.0)   # -1..1 drift direction for ken burns
    flash: str | None = None          # "white" | "black" | None
    shake: float = 0.0
    strobe: bool = False
    invert: bool = False
    dissolve_ms: int = 0
    on_downbeat: bool = False
    hard: bool = False
    strength: float = 0.5
    tick: int = 0
    src_offset: float = 0.0           # clips: start playing this many seconds into the clip (key moment framing)


@dataclass
class EDL:
    shots: list[Shot]
    audio_end: float
    fade_out_ms: int
    fps: int
    stats: dict = field(default_factory=dict)
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))

    @property
    def duration(self) -> float:
        return self.shots[-1].end if self.shots else 0.0


def _jsonable(o):
    """Recursively convert numpy scalars/arrays so json.dumps never sees them."""
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_jsonable(v) for v in o.tolist()]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    return o


class _Grid:
    """Tick grid helpers."""

    def __init__(self, grid: BeatGrid):
        self.grid = grid
        beats_arr = np.array(grid.beats, dtype=float)
        if len(beats_arr) > 1:
            steps = np.diff(beats_arr)
            ticks = [beats_arr[:-1] + steps * (k / SUB) for k in range(SUB)]
            self.ticks = np.concatenate([np.stack(ticks, axis=1).ravel(), beats_arr[-1:]])
        else:
            self.ticks = beats_arr
        self.n = len(self.ticks)
        beat_set = {round(b, 6) for b in grid.beats}
        down_set = {round(b, 6) for b in grid.downbeats}
        self.is_beat = np.array([round(t, 6) in beat_set for t in self.ticks])
        self.is_down = np.array([round(t, 6) in down_set for t in self.ticks])
        # beat index per tick (for strength/energy lookup)
        beats = np.array(grid.beats)
        self.beat_idx = np.clip(np.searchsorted(beats, self.ticks, side="right") - 1, 0, len(beats) - 1)
        self.strength = np.array(grid.beat_strength)[self.beat_idx]
        self.energy = np.array(grid.energy)[self.beat_idx]
        # bar index per tick
        self.bar_idx = np.cumsum(self.is_down) - 1

    def time(self, tick: int) -> float:
        if tick < self.n:
            return float(self.ticks[tick])
        # extrapolate past the last tick using the mean tick interval
        step = float(np.mean(np.diff(self.ticks))) if self.n > 1 else 0.25
        return float(self.ticks[-1] + (tick - self.n + 1) * step)

    def next_downbeat_tick(self, tick: int) -> int:
        idx = np.nonzero(self.is_down[tick:])[0]
        return int(tick + idx[0]) if len(idx) else tick

    def tick_at(self, t: float) -> int:
        """Tick index whose time is nearest to t (extrapolated past the end of the song)."""
        if self.n == 0:
            return 0
        if t <= self.ticks[-1]:
            return int(np.argmin(np.abs(self.ticks - t)))
        step = float(np.mean(np.diff(self.ticks))) if self.n > 1 else 0.25
        return self.n - 1 + int(round((t - self.ticks[-1]) / step))

    def nearest_downbeat_tick(self, tick: int) -> int:
        idx = np.nonzero(self.is_down)[0]
        if not len(idx):
            return tick
        if tick >= self.n:
            # beyond the song: downbeats continue every bar from the last real one
            last = int(idx[-1])
            return last + int(round((tick - last) / (4 * SUB))) * 4 * SUB
        return int(idx[np.argmin(np.abs(idx - tick))])

    def last_downbeat_tick(self) -> int:
        idx = np.nonzero(self.is_down)[0]
        return int(idx[-1]) if len(idx) else self.n - 1


def _unit_ticks(cut_unit: str) -> int:
    return {"bar": 4 * SUB, "two_beats": 2 * SUB, "beat": SUB, "half_beat": SUB // 2, "quarter_beat": 1}[cut_unit]


def _quantize(length: float) -> int:
    return min(LADDER, key=lambda l: abs(math.log2(l) - math.log2(max(length, 1e-6))))


def _shot_length_ticks(params: MontageParams, p: float, energy: float, shot_i: int) -> int:
    """Shot length in ticks for timeline position p in [0,1]."""
    L0 = _unit_ticks(params.cut_unit)
    if params.pacing == "constant":
        return L0
    if params.pacing == "pulse":
        return L0 * 2 if shot_i % 2 == 0 else L0
    start = min(8 * SUB, L0 * 4)
    end = max(1, L0 // 4) if params.cut_unit in ("bar", "two_beats") else max(1, L0 // 2)
    if params.pacing == "follow_energy":
        return _quantize(start * (end / start) ** float(energy))
    gamma = 0.5 + params.accel_curve * 2.0
    if params.pacing == "decelerate":
        p = 1.0 - p
    eased = min(1.0, max(0.0, p)) ** gamma
    return _quantize(start * (end / start) ** eased)


def _photo_sequence(params: MontageParams, n_photos: int, brightness: list[float] | None) -> list[int]:
    idx = list(range(n_photos))
    if params.order == "reverse":
        idx.reverse()
    elif params.order == "shuffle":
        random.Random(params.seed).shuffle(idx)
    elif params.order == "alternate_halves":
        half = (n_photos + 1) // 2
        a, b = idx[:half], idx[half:]
        out = []
        for i in range(half):
            out.append(a[i])
            if i < len(b):
                out.append(b[i])
        idx = out
    elif params.order == "brightness_sort" and brightness:
        idx.sort(key=lambda i: brightness[i])
    return idx


def _cards(params: MontageParams) -> list[str]:
    return params.intertitles.cards or DEFAULT_CARDS


def plan(params: MontageParams, grid: BeatGrid, n_photos: int, brightness: list[float] | None = None,
         shots_info: list[dict] | None = None) -> EDL:
    """shots_info (optional, aligned with the photos): per-shot hints from the AI shot analysis or the editor:
    weight 1-5, must (has to be in), once (never repeat), key_at (clip key moment, s), duration (clip length, s)."""
    if n_photos <= 0:
        raise ValueError("no photos")
    g = _Grid(grid)
    seq = _photo_sequence(params, n_photos, brightness)
    start_tick = min(params.start_offset_beats * SUB, max(0, g.n - 2))
    info = _norm_info(shots_info, n_photos)

    if params.fit_mode == "fit_to_song":
        end_tick = g.last_downbeat_tick() if params.end_on_downbeat else g.n - 1
        end_tick = max(end_tick, start_tick + 1)
        if params.use_every_shot:
            shots = _distribute_all(params, g, seq, start_tick, end_tick, info)
        else:
            shots = _generate(params, g, seq, start_tick, end_tick, consume_all=False, info=info)
    elif params.fit_mode == "fit_to_duration":
        end_tick = g.tick_at(g.time(start_tick) + params.target_duration_s)
        # the song is the hard ceiling: a longer request just fills the track
        end_tick = min(end_tick, g.last_downbeat_tick() if params.end_on_downbeat else g.n - 1)
        if params.end_on_downbeat:
            # snap to the nearest downbeat, but never drift more than a bar from the requested length
            db = g.nearest_downbeat_tick(end_tick)
            if db > start_tick and abs(db - end_tick) <= 2 * SUB:
                end_tick = db
        end_tick = max(end_tick, start_tick + 1)
        if params.use_every_shot:
            shots = _distribute_all(params, g, seq, start_tick, end_tick, info)
        else:
            shots = _generate(params, g, seq, start_tick, end_tick, consume_all=False, info=info)
    else:
        # Binary-search the timeline extent T (in ticks) such that consuming every photo ends near T.
        lo, hi = start_tick + n_photos, start_tick + n_photos * 8 * SUB + 64
        best = None
        for _ in range(40):
            mid = (lo + hi) // 2
            shots = _generate(params, g, seq, start_tick, mid, consume_all=True, info=info)
            last = shots[-1].tick + _shot_ticks(shots[-1], g)
            if last > mid:
                lo = mid + 1
            else:
                hi = mid
                best = shots
            if lo >= hi:
                break
        shots = best or _generate(params, g, seq, start_tick, hi, consume_all=True, info=info)
        if params.end_on_downbeat and shots:
            last_end_tick = shots[-1].tick + _shot_ticks(shots[-1], g)
            db = g.next_downbeat_tick(min(last_end_tick, g.n - 1))
            if db > last_end_tick and (db - last_end_tick) <= 4 * SUB:
                shots[-1].end = g.time(db)

    _apply_effects(params, g, shots)
    _frame_key_moments(shots, info)
    audio_end = min(shots[-1].end, grid.duration) if shots else grid.duration
    if params.fit_mode == "fit_to_song":
        audio_end = max(audio_end, shots[-1].end)
    for s in shots:
        s.end = min(s.end, max(audio_end, s.start + 0.04))
    stats = _stats(shots, n_photos, grid)
    stats["key_moments"] = sum(1 for s in shots if s.src_offset > 0 or (s.photo_idx is not None and info and info[s.photo_idx]["key_at"] is not None))
    stats["must_missing"] = sorted({i for i, n in enumerate(info) if n["must"]} - {s.photo_idx for s in shots}) if info else []
    return EDL(
        shots=shots,
        audio_end=float(audio_end),
        fade_out_ms=params.output.audio_fade_out_ms if params.fit_mode != "fit_to_song" else 0,
        fps=params.output.fps,
        stats=stats,
        beats=grid.beats,
        downbeats=grid.downbeats,
    )


def _shot_ticks(shot: Shot, g: _Grid) -> int:
    # recover tick length from times (used only during search)
    return max(1, int(round((shot.end - shot.start) / max(1e-6, float(np.mean(np.diff(g.ticks)))))))


def _norm_info(shots_info: list[dict] | None, n: int) -> list[dict] | None:
    if not shots_info:
        return None
    out = []
    for i in range(n):
        d = shots_info[i] if i < len(shots_info) and shots_info[i] else {}
        out.append({"weight": int(d.get("weight", 3) or 3), "must": bool(d.get("must")), "once": bool(d.get("once")),
                    "key_at": (float(d["key_at"]) if d.get("key_at") is not None else None),
                    "duration": (float(d["duration"]) if d.get("duration") else None)})
    return out


def _priority(info: list[dict] | None, i: int) -> float:
    if not info:
        return 3.0
    n = info[i]
    return n["weight"] + (3.0 if n["must"] else 0.0) + (1.0 if n["key_at"] is not None else 0.0)


def _frame_key_moments(shots: list[Shot], info: list[dict] | None) -> None:
    """Clips with a key moment start playing so the moment lands inside the shot (about 40% in)."""
    if not info:
        return
    for s in shots:
        if s.photo_idx is None:
            continue
        n = info[s.photo_idx]
        if n["key_at"] is None:
            continue
        length = max(0.04, s.end - s.start)
        off = max(0.0, n["key_at"] - 0.4 * length)
        if n["duration"]:
            off = max(0.0, min(off, n["duration"] - length))
        s.src_offset = round(off, 3)


def _distribute_all(params: MontageParams, g: _Grid, seq: list[int], start_tick: int, end_tick: int,
                    info: list[dict] | None) -> list[Shot]:
    """use_every_shot: every shot exactly once, in order, spread over [start_tick, end_tick). Shot lengths come from
    the tick ladder (so cuts stay on the beat grid, on whole beats when there is room); the spare time goes to the
    highest-priority shots (must-include, AI importance, clip key moments) as longer holds. If even one tick per shot
    does not fit, the lowest-priority non-essential shots are left out."""
    shots: list[Shot] = []
    tick = start_tick
    intro = (params.intro_card or "").strip()
    if intro:
        shots.append(Shot(start=g.time(tick), end=g.time(tick + params.intro_hold_beats * SUB), card_text=intro, tick=tick))
        tick += params.intro_hold_beats * SUB
    it = params.intertitles
    cards = _cards(params)
    card_ticks = it.card_hold_beats * SUB
    closing = it.enabled and it.placement in ("closing", "opening_and_closing")
    if it.enabled and it.placement in ("opening", "opening_and_closing"):
        shots.append(Shot(start=g.time(tick), end=g.time(tick + card_ticks), card_text=cards[0], tick=tick))
        tick += card_ticks
    end_tick = max(end_tick - (card_ticks if closing else 0), tick + 1)
    total = end_tick - tick
    order = list(seq)
    tick_s = float(np.mean(np.diff(g.ticks))) if g.n > 1 else 0.25
    min_ticks = max(1, int(math.ceil(params.min_shot_ms / 1000.0 / tick_s)))
    # drop lowest-priority non-must shots until one minimum-length shot each fits
    while len(order) > 1 and len(order) * min_ticks > total:
        droppable = [i for i in order if not (info and info[i]["must"])] or order
        order.remove(min(droppable, key=lambda i: (_priority(info, i), -order.index(i))))
    n = len(order)
    L = max([l for l in LADDER if l * n <= total] or [1])
    extra = total - L * n
    lengths = {i: L for i in order}
    by_pri = sorted(order, key=lambda i: (-_priority(info, i), order.index(i)))
    k = 0
    while extra >= L and by_pri:
        lengths[by_pri[k % len(by_pri)]] += L
        extra -= L
        k += 1
    if order:
        lengths[order[-1]] += extra  # the last shot absorbs the remainder so the end lands exactly
    for i in order:
        shots.append(Shot(start=g.time(tick), end=g.time(tick + lengths[i]), photo_idx=i, tick=tick))
        tick += lengths[i]
    if closing:
        shots.append(Shot(start=g.time(tick), end=g.time(tick + card_ticks), card_text=cards[-1], tick=tick))
    return shots


def _generate(params: MontageParams, g: _Grid, seq: list[int], start_tick: int, end_tick: int, consume_all: bool,
              info: list[dict] | None = None) -> list[Shot]:
    """Walk the tick grid producing shots. consume_all: stop when photos run out (fit_to_photos).
    Shots flagged once are shown a single time even when the others cycle."""
    shots: list[Shot] = []
    queue = list(seq)
    pos = 0  # index into queue when repeating
    once = {i for i in seq if info and info[i]["once"]}
    shown: set[int] = set()
    tick = start_tick
    shot_i = 0
    total = max(1, end_tick - start_tick)
    cards = _cards(params)
    card_i = 0
    it = params.intertitles
    card_ticks = it.card_hold_beats * SUB
    min_ticks = 1
    tick_s = float(np.mean(np.diff(g.ticks))) if g.n > 1 else 0.25
    min_ticks = max(1, int(math.ceil(params.min_shot_ms / 1000.0 / tick_s)))
    sections = params_sections(g)
    section_starts = {int(np.searchsorted(g.ticks, t)) for t in sections}
    hold_first = params.hold_first_beats * SUB
    hold_last = params.hold_last_beats * SUB
    used_bars: set[int] = set()

    def add_card(text: str, at_tick: int) -> int:
        shots.append(Shot(start=g.time(at_tick), end=g.time(at_tick + card_ticks), card_text=text, tick=at_tick))
        return at_tick + card_ticks

    intro = (params.intro_card or "").strip()
    if intro:
        shots.append(Shot(start=g.time(tick), end=g.time(tick + params.intro_hold_beats * SUB), card_text=intro, tick=tick))
        tick += params.intro_hold_beats * SUB
    if it.enabled and it.placement in ("opening", "opening_and_closing"):
        tick = add_card(cards[card_i % len(cards)], tick)
        card_i += 1

    while True:
        if consume_all:
            if not queue:
                break
        elif tick >= end_tick:
            break
        # intertitles in the middle
        if it.enabled and shots and shots[-1].card_text is None:
            bar = int(g.bar_idx[min(tick, g.n - 1)])
            on_down = bool(g.is_down[min(tick, g.n - 1)])
            if it.placement == "every_n_bars" and on_down and bar > 0 and bar % it.every_n_bars == 0 and bar not in used_bars:
                used_bars.add(bar)
                tick = add_card(cards[card_i % len(cards)], tick)
                card_i += 1
                continue
            if it.placement == "between_sections" and tick in section_starts and tick > start_tick:
                section_starts.discard(tick)
                tick = add_card(cards[card_i % len(cards)], tick)
                card_i += 1
                continue
        # motif
        p = (tick - start_tick) / total
        energy = float(g.energy[min(tick, g.n - 1)])
        length = _shot_length_ticks(params, p, energy, shot_i)
        length = max(length, min_ticks)
        if shot_i == 0:
            length += hold_first
        is_last = consume_all and len(queue) == 1
        if is_last:
            length += hold_last
        if not consume_all and tick + length > end_tick:
            length = max(1, end_tick - tick)
        # photo choice
        bar = int(g.bar_idx[min(tick, g.n - 1)])
        motif = params.motif_repeat > 0 and bool(g.is_down[min(tick, g.n - 1)]) and bar > 0 and bar % params.motif_repeat == 0 and bar not in used_bars
        if motif:
            used_bars.add(bar)
            photo = seq[0]
        elif consume_all:
            photo = queue.pop(0)
        else:
            if pos >= len(queue):
                pos = 0  # cycle; no-repeat mode is remapped to holds below
                # second time round: shots flagged once leave the cycle (unless that would empty it)
                keep = [i for i in queue if i not in once]
                if keep:
                    queue = keep
            photo = queue[pos]
            pos += 1
        shown.add(photo)
        shots.append(Shot(start=g.time(tick), end=g.time(tick + length), photo_idx=photo, tick=tick))
        tick += length
        shot_i += 1

    if it.enabled and it.placement in ("closing", "opening_and_closing"):
        tick = add_card(cards[card_i % len(cards)], tick)
    if not consume_all and not params.allow_repeats and shots:
        # spread photos across the whole song: each photo holds across consecutive cuts (zoom re-triggers per cut)
        photo_shots = [s for s in shots if s.photo_idx is not None]
        m, n = len(photo_shots), len(seq)
        if m > n:
            for i, s in enumerate(photo_shots):
                s.photo_idx = seq[min(n - 1, i * n // m)]
    if not consume_all and info and shots:
        # must-include shots that never got a slot replace the lowest-priority shots that did (keeping order feel)
        photo_shots = [s for s in shots if s.photo_idx is not None]
        present = {s.photo_idx for s in photo_shots}
        missing = [i for i in seq if info[i]["must"] and i not in present]
        if missing:
            counts: dict[int, int] = {}
            for s in photo_shots:
                counts[s.photo_idx] = counts.get(s.photo_idx, 0) + 1
            for s in sorted(photo_shots, key=lambda s: (info[s.photo_idx]["must"], _priority(info, s.photo_idx), -counts[s.photo_idx])):
                if not missing:
                    break
                if info[s.photo_idx]["must"] and counts[s.photo_idx] <= 1:
                    continue
                counts[s.photo_idx] -= 1
                s.photo_idx = missing.pop(0)
    return shots


def params_sections(g: _Grid) -> list[float]:
    secs = g.grid.sections
    return [secs[k][0] for k in ("build", "climax", "outro") if k in secs]


def _apply_effects(params: MontageParams, g: _Grid, shots: list[Shot]) -> None:
    fx = params.effects
    rng = random.Random(params.seed)
    total_end = shots[-1].end if shots else 1.0
    total_start = shots[0].start if shots else 0.0
    span = max(1e-6, total_end - total_start)
    peak = g.grid.sections.get("peak", [0, 0])
    n_photo_shots = 0
    # "hard beats": strongest onsets, or the biggest jumps in energy (drops), per beat
    # Rank-based so the threshold means "top (1-t) share of beats" whatever the track's dynamics:
    # threshold 0.8 = the 20% hardest onsets, or the 20% biggest energy jumps (that are real jumps, >= 0.1).
    strength_all = np.array(g.grid.beat_strength, dtype=float)
    energy = np.array(g.grid.energy, dtype=float)
    delta = np.diff(energy, prepend=energy[0] if len(energy) else 0.0)

    def rank(arr: np.ndarray, v: float) -> float:
        # average rank: ties share their rank, so a flat track never counts as "all hard"
        return (float((arr < v).sum()) + 0.5 * float((arr == v).sum())) / max(1, len(arr))
    for i, s in enumerate(shots):
        t = min(s.tick, g.n - 1)
        s.on_downbeat = bool(g.is_down[t])
        s.strength = float(g.strength[t])
        bi = int(g.beat_idx[t])
        on_beat = bool(g.is_beat[t])
        thr = fx.hard_beat_threshold
        hard_onset = rank(strength_all, s.strength) >= thr
        hard_jump = len(delta) > bi and delta[bi] >= 0.1 and rank(delta, float(delta[bi])) >= thr
        s.hard = bool(on_beat and (hard_onset or hard_jump))
        s.dissolve_ms = fx.dissolve_ms
        if s.card_text is not None:
            continue
        n_photo_shots += 1
        # zoom
        punch = fx.punch_zoom * 0.10 * (0.6 + 0.4 * s.strength)
        kb = fx.ken_burns * 0.06
        s.zoom_from = 1.0 + punch
        if kb > 0:
            direction = 1 if n_photo_shots % 2 == 0 else -1
            s.zoom_to = 1.0 + max(0.0, kb * (1 if direction > 0 else 0.3))
            s.pan = (rng.uniform(-1, 1), rng.uniform(-0.5, 0.5))
            if direction < 0:
                s.zoom_from = max(s.zoom_from, 1.0 + kb)
        else:
            s.zoom_to = 1.0
        # flashes
        in_peak = peak[0] <= s.start < peak[1]
        def want(mode: str) -> bool:
            if mode == "off":
                return False
            if mode == "every_cut":
                return True
            if mode == "downbeats":
                return s.on_downbeat
            if mode == "hard_beats":
                return s.hard
            if mode == "climax_only":
                return in_peak and s.on_downbeat
            return False
        w, b = want(fx.flash_white), want(fx.flash_black)
        if w and b:
            s.flash = "white" if (i % 2 == 0) else "black"
        elif w:
            s.flash = "white"
        elif b:
            s.flash = "black"
        # strobe in the final section of the timeline
        if fx.strobe and (s.start - total_start) / span >= 1.0 - fx.strobe_section_pct:
            s.strobe = True
        s.shake = fx.shake * (0.4 + 0.6 * s.strength)
        if fx.invert_on_accent and s.on_downbeat and s.strength > 0.8:
            s.invert = True


def _stats(shots: list[Shot], n_photos: int, grid: BeatGrid) -> dict:
    photo_shots = [s for s in shots if s.photo_idx is not None]
    lengths = [s.end - s.start for s in photo_shots]
    used = {s.photo_idx for s in photo_shots}
    beats = _Grid(grid).ticks
    starts = np.array([s.start for s in shots])
    off = np.abs(starts[:, None] - beats[None, :]).min(axis=1) if len(beats) and len(starts) else np.array([0.0])
    return {
        "n_shots": len(shots),
        "n_cards": len(shots) - len(photo_shots),
        "photos_used": len(used),
        "photos_total": n_photos,
        "duration_s": round(shots[-1].end, 2) if shots else 0.0,
        "shortest_shot_ms": int(min(lengths) * 1000) if lengths else 0,
        "longest_shot_ms": int(max(lengths) * 1000) if lengths else 0,
        "mean_shot_ms": int(float(np.mean(lengths)) * 1000) if lengths else 0,
        "max_cut_offset_ms": int(float(off.max()) * 1000),
        "flashes": sum(1 for s in shots if s.flash),
        "hard_beats": sum(1 for s in shots if s.hard),
        "strobe_shots": sum(1 for s in shots if s.strobe),
    }
