"""Audio decoding and beat analysis: MP3 -> BeatGrid."""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .ffmpeg_bin import get_ffmpeg

SR = 22050


class TimeMap:
    """Piecewise-constant playback speed: segments of ORIGINAL time [a, b) played at speed v.
    Maps original times to output times (and gives the output duration)."""

    def __init__(self, segments: list[tuple[float, float, float]], out_lengths: list[float] | None = None):
        self.segments = sorted(segments)
        # measured output length per segment (seconds); defaults to the nominal (b - a) / v
        self.out_lengths = list(out_lengths) if out_lengths else [(b - a) / v for a, b, v in self.segments]
        self.out_starts: list[float] = []
        t = 0.0
        for L in self.out_lengths:
            self.out_starts.append(t)
            t += L
        self.out_end = t

    def to_output(self, t: float) -> float:
        for (a, b, v), o, L in zip(self.segments, self.out_starts, self.out_lengths):
            if t < b:
                return o + max(0.0, t - a) * (L / max(1e-9, b - a))
        a, b, v = self.segments[-1]
        return self.out_end + (t - b) / v

    def duration(self, total: float) -> float:
        return self.to_output(total)


def ramp_segments(t0: float, t1: float, total: float, base: float, end_mult: float, gamma: float,
                  reverse: bool = False, n: int = 24) -> list[tuple[float, float, float]]:
    """Speed segments: `base` before t0, ramping to base*end_mult over [t0, t1] (eased by p**gamma), then held.
    reverse=True starts fast and settles to `base` (decelerate)."""
    segs: list[tuple[float, float, float]] = []
    t0, t1 = max(0.0, t0), min(max(t0 + 1e-3, t1), max(total, t0 + 1e-3))
    if t0 > 0:
        segs.append((0.0, t0, base * (end_mult if reverse else 1.0)))
    for k in range(n):
        a = t0 + (t1 - t0) * k / n
        b = t0 + (t1 - t0) * (k + 1) / n
        p = (k + 0.5) / n
        e = (1.0 - p) ** gamma if reverse else p ** gamma
        segs.append((a, b, base * (1.0 + (end_mult - 1.0) * e)))
    if total > t1:
        segs.append((t1, total, base * (1.0 if reverse else end_mult)))
    return segs


def _seek_args(start_s: float, end_s: float = 0.0) -> list[str]:
    """ffmpeg input options selecting the part of the song to use: [start_s, end_s) in original time (end 0 = to the end)."""
    args: list[str] = []
    if start_s > 0:
        args += ["-ss", f"{start_s:.3f}"]
    if end_s > start_s:
        args += ["-t", f"{end_s - start_s:.3f}"]
    return args


def render_ramped_audio(src: Path, dst: Path, segments: list[tuple[float, float, float]], keep_pitch: bool,
                        start_s: float = 0.0, rate: int = 48000, end_s: float = 0.0) -> TimeMap:
    """Write the tempo-ramped song to dst (stereo 48 kHz WAV), stretching each segment on its own and
    measuring the real output length, so the returned TimeMap is exact at every seam."""
    ff = get_ffmpeg()
    cmd = [ff, "-hide_banner", "-loglevel", "error", *_seek_args(start_s, end_s)]
    cmd += ["-i", str(src), "-vn", "-ac", "2", "-ar", str(rate), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, np.float32).reshape(-1, 2)
    pieces, lengths = [], []
    for a, b, v in sorted(segments):
        i0, i1 = int(round(a * rate)), min(len(audio), int(round(b * rate)))
        chunk = audio[i0:i1]
        if len(chunk) == 0:
            lengths.append(0.0)
            continue
        if abs(v - 1.0) < 1e-4:
            out = chunk
        else:
            if keep_pitch:
                sp, chain = v, []
                while sp > 2.0:
                    chain.append("atempo=2.0")
                    sp /= 2.0
                while sp < 0.5:
                    chain.append("atempo=0.5")
                    sp /= 0.5
                chain.append(f"atempo={sp:.5f}")
                af = ",".join(chain)
            else:
                af = f"asetrate={rate}*{v:.5f},aresample={rate}"
            seg_cmd = [ff, "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", "pipe:0",
                       "-af", af, "-f", "f32le", "-ar", str(rate), "-ac", "2", "-"]
            res = subprocess.run(seg_cmd, input=chunk.tobytes(), capture_output=True, check=True).stdout
            out = np.frombuffer(res, np.float32).reshape(-1, 2)
        pieces.append(out)
        lengths.append(len(out) / rate)
    full = np.concatenate(pieces) if pieces else np.zeros((0, 2), np.float32)
    import soundfile as sf

    sf.write(str(dst), full, rate, subtype="PCM_16")
    return TimeMap(segments, lengths)


def _segment_filter(segments: list[tuple[float, float, float]], keep_pitch: bool, rate: int) -> str:
    n = len(segments)
    parts = [f"[0:a]aresample={rate},asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    for i, (a, b, v) in enumerate(segments):
        chain = f"[s{i}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS"
        if keep_pitch:
            sp = v
            while sp > 2.0:
                chain += ",atempo=2.0"
                sp /= 2.0
            while sp < 0.5:
                chain += ",atempo=0.5"
                sp /= 0.5
            chain += f",atempo={sp:.5f}"
        else:
            chain += f",asetrate={rate}*{v:.5f},aresample={rate}"
        parts.append(chain + f"[t{i}]")
    parts.append("".join(f"[t{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]")
    return ";".join(parts)


def speed_filter(speed: float, keep_pitch: bool, rate: int) -> list[str]:
    """ffmpeg -af arguments that play the song `speed` times faster."""
    if abs(speed - 1.0) < 1e-3:
        return []
    if keep_pitch:
        parts, s = [], speed
        while s > 2.0:      # atempo accepts 0.5..2.0 per stage
            parts.append("atempo=2.0")
            s /= 2.0
        while s < 0.5:
            parts.append("atempo=0.5")
            s /= 0.5
        parts.append(f"atempo={s:.4f}")
        return ["-af", ",".join(parts)]
    return ["-af", f"aresample={rate},asetrate={rate}*{speed:.4f},aresample={rate}"]


def decode_to_wav(src: Path, dst: Path, start_s: float = 0.0, speed: float = 1.0, keep_pitch: bool = True,
                  end_s: float = 0.0) -> Path:
    """Decode any audio file to mono 22.05 kHz WAV using the bundled ffmpeg (optionally sped up)."""
    cmd = [get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *_seek_args(start_s, end_s)]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", str(SR), *speed_filter(speed, keep_pitch, SR), "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def decode_for_mux(src: Path, dst: Path, start_s: float = 0.0, speed: float = 1.0, keep_pitch: bool = True,
                   segments: list[tuple[float, float, float]] | None = None, end_s: float = 0.0) -> Path:
    """Decode audio at full quality (stereo, 48 kHz) for muxing into the final video.
    With `segments` (original-time ranges + speeds) the song's tempo ramps over the video."""
    cmd = [get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *_seek_args(start_s, end_s)]
    cmd += ["-i", str(src), "-vn"]
    if segments:
        cmd += ["-filter_complex", _segment_filter(segments, keep_pitch, 48000), "-map", "[out]"]
    else:
        cmd += ["-ac", "2", "-ar", "48000", *speed_filter(speed, keep_pitch, 48000)]
    cmd += ["-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


@dataclass
class BeatGrid:
    duration: float
    bpm: float
    beats: list[float]              # seconds
    downbeats: list[float]          # subset of beats (assumed 4/4)
    half_beats: list[float]         # beats + midpoints, sorted
    beat_strength: list[float]      # 0..1 accent weight per beat
    energy: list[float]             # 0..1 smoothed energy per beat
    sections: dict[str, list[float]] = field(default_factory=dict)  # name -> [start, end]
    downbeat_phase: int = 0

    def summary(self) -> dict:
        return {
            "duration_s": round(self.duration, 2),
            "bpm": round(self.bpm, 1),
            "n_beats": len(self.beats),
            "n_bars": len(self.downbeats),
            "sections": {k: [round(v[0], 1), round(v[1], 1)] for k, v in self.sections.items()},
            "mean_energy": round(float(np.mean(self.energy)) if self.energy else 0.0, 3),
        }

    def scaled(self, speed: float) -> "BeatGrid":
        """Grid for the same track played `speed` times faster (all times divided, bpm multiplied)."""
        if abs(speed - 1.0) < 1e-6:
            return self
        f = 1.0 / speed
        return BeatGrid(
            duration=self.duration * f, bpm=self.bpm * speed,
            beats=[b * f for b in self.beats], downbeats=[b * f for b in self.downbeats],
            half_beats=[b * f for b in self.half_beats], beat_strength=list(self.beat_strength), energy=list(self.energy),
            sections={k: [v[0] * f, v[1] * f] for k, v in self.sections.items()}, downbeat_phase=self.downbeat_phase,
        )

    def remapped(self, tmap: "TimeMap") -> "BeatGrid":
        """Grid for the same track played through a TimeMap (tempo ramp)."""
        f = tmap.to_output
        return BeatGrid(
            duration=f(self.duration), bpm=self.bpm,
            beats=[f(b) for b in self.beats], downbeats=[f(b) for b in self.downbeats],
            half_beats=[f(b) for b in self.half_beats], beat_strength=list(self.beat_strength), energy=list(self.energy),
            sections={k: [f(v[0]), f(v[1])] for k, v in self.sections.items()}, downbeat_phase=self.downbeat_phase,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BeatGrid":
        return cls(**d)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: Path) -> "BeatGrid":
        return cls.from_dict(json.loads(path.read_text()))


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) < k:
        return x
    kernel = np.ones(k) / k
    return np.convolve(x, kernel, mode="same")


def _norm(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    lo, hi = float(np.min(x)), float(np.max(x))
    if hi - lo < 1e-9:
        return np.ones_like(x) * 0.5
    return (x - lo) / (hi - lo)


def _refine_beats(beats: np.ndarray, onset_env: np.ndarray, sr: int, hop: int, window_s: float) -> np.ndarray:
    """Snap each beat to the strongest onset within +-window_s, keeping the tracker's global phase."""
    frames_per_s = sr / hop
    w = max(1, int(round(window_s * frames_per_s)))
    out = []
    for b in beats:
        f = int(round(b * frames_per_s))
        lo, hi = max(0, f - w), min(len(onset_env), f + w + 1)
        if hi <= lo:
            out.append(float(b))
            continue
        seg = onset_env[lo:hi]
        peak = lo + int(np.argmax(seg))
        # Only move if the peak is clearly a local maximum (avoid drifting on flat envelopes).
        if seg.max() > 1.5 * (seg.mean() + 1e-9):
            out.append(peak / frames_per_s)
        else:
            out.append(float(b))
    out = np.array(out)
    # Preserve monotonicity.
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 0.01
    return out


def _normalise_tempo(beats: np.ndarray, tempo: float, onset_env: np.ndarray, sr: int, hop: int) -> tuple[np.ndarray, float]:
    import librosa

    if len(beats) < 8:
        return beats, tempo
    period = float(np.median(np.diff(beats)))
    bpm = 60.0 / period if period > 0 else tempo
    if bpm > 150:
        frames = np.clip(librosa.time_to_frames(beats, sr=sr, hop_length=hop), 0, len(onset_env) - 1)
        st = onset_env[frames]
        ph = 0 if st[0::2].mean() >= st[1::2].mean() else 1
        return beats[ph::2], bpm / 2
    if bpm < 70:
        mids = (beats[:-1] + beats[1:]) / 2.0
        return np.sort(np.concatenate([beats, mids])), bpm * 2
    return beats, bpm


def analyze(wav: Path, bpm_hint: float | None = None) -> BeatGrid:
    """Run beat tracking, accent weighting, energy and section detection."""
    import librosa
    import soundfile as sf

    y, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    duration = len(y) / sr
    hop = 128  # ~5.8 ms resolution so cuts land precisely on the beat

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    kwargs = {"start_bpm": bpm_hint} if bpm_hint else {}
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=hop, trim=False, units="frames", **kwargs
    )
    tempo = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop).astype(float)

    if len(beats) < 4:
        # Fallback: synthetic grid from tempo (or 120 BPM) so the planner still works.
        tempo = tempo if tempo > 0 else 120.0
        period = 60.0 / tempo
        beats = np.arange(0.0, duration, period)

    beats = _refine_beats(beats, onset_env, sr, hop, window_s=0.04)

    # Normalise into a musical tempo range so "one cut per beat" matches the felt pulse:
    # double-time detections (> 150 BPM) are halved, keeping the phase with the stronger accents;
    # very slow detections (< 70 BPM) are doubled by inserting midpoints.
    beats, tempo = _normalise_tempo(beats, tempo, onset_env, sr, hop)

    # Accent strength per beat: onset envelope sampled at beat frames.
    beat_frames = librosa.time_to_frames(beats, sr=sr, hop_length=hop)
    beat_frames = np.clip(beat_frames, 0, len(onset_env) - 1)
    strength = _norm(onset_env[beat_frames])

    # Energy per beat: RMS envelope smoothed, sampled at beats.
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_s = _smooth(rms, 172)  # ~1 s at hop 128 / 22050
    energy = _norm(rms_s[np.clip(beat_frames, 0, len(rms_s) - 1)])

    # Downbeat phase: which of the 4 offsets carries the strongest accents.
    best_phase, best_score = 0, -1.0
    for ph in range(4):
        s = float(np.mean(strength[ph::4])) if len(strength[ph::4]) else 0.0
        if s > best_score:
            best_phase, best_score = ph, s
    downbeats = beats[best_phase::4]

    # Half beats.
    mids = (beats[:-1] + beats[1:]) / 2.0
    half = np.sort(np.concatenate([beats, mids]))

    # Sections by energy over time (quarters of the track, labelled by relative energy).
    n = len(beats)
    q = max(1, n // 4)
    labels = ["intro", "build", "climax", "outro"]
    bounds = [0, q, 2 * q, 3 * q, n]
    seg_energy = [float(np.mean(energy[bounds[i]:bounds[i + 1]])) if bounds[i] < bounds[i + 1] else 0.0 for i in range(4)]
    # Climax = highest-energy quarter among the middle/late ones; keep chronological names but record energy.
    sections: dict[str, list[float]] = {}
    for i, name in enumerate(labels):
        s = float(beats[bounds[i]]) if bounds[i] < n else duration
        e = float(beats[bounds[i + 1]]) if bounds[i + 1] < n else duration
        sections[name] = [s, e]
    peak_i = int(np.argmax(seg_energy))
    sections["peak"] = sections[labels[peak_i]]

    return BeatGrid(
        duration=float(duration),
        bpm=tempo,
        beats=[float(b) for b in beats],
        downbeats=[float(b) for b in downbeats],
        half_beats=[float(b) for b in half],
        beat_strength=[float(s) for s in strength],
        energy=[float(e) for e in energy],
        sections=sections,
        downbeat_phase=best_phase,
    )
