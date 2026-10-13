"""Render an EDL + photos + audio to MP4/MOV by piping raw frames into ffmpeg."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from . import effects as fx
from .ffmpeg_bin import get_ffmpeg


class ClipStream:
    """Sequential frame reader for one shot of a video clip, decoded by ffmpeg at base size.
    The clip plays from start_s (its key-moment framing) for the shot's duration and loops if it is shorter."""

    def __init__(self, path: Path, base_w: int, base_h: int, fps: int, duration: float, bw: bool, start_s: float = 0.0):
        self.w, self.h, self.bw = base_w, base_h, bw
        self.pix = "gray" if bw else "rgb24"
        self.frame_bytes = base_w * base_h * (1 if bw else 3)
        n = max(1, int(math.ceil(duration * fps)) + 2)
        cmd = [get_ffmpeg(), "-hide_banner", "-loglevel", "error", "-stream_loop", "-1"]
        if start_s > 0:
            cmd += ["-ss", f"{start_s:.3f}"]
        cmd += ["-i", str(path),
               "-an", "-frames:v", str(n), "-r", str(fps),
               "-vf", f"scale={base_w}:{base_h}:force_original_aspect_ratio=increase,crop={base_w}:{base_h}",
               "-f", "rawvideo", "-pix_fmt", self.pix, "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.idx = -1
        self.last: Image.Image | None = None

    def frame(self, idx: int) -> Image.Image:
        while self.idx < idx:
            raw = self.proc.stdout.read(self.frame_bytes)
            if len(raw) < self.frame_bytes:
                break  # clip ended early: hold the last frame
            self.last = Image.frombuffer("L" if self.bw else "RGB", (self.w, self.h), raw, "raw", "L" if self.bw else "RGB", 0, 1)
            self.idx += 1
        if self.last is None:
            self.last = Image.new("L" if self.bw else "RGB", (self.w, self.h), 0)
        return self.last

    def close(self) -> None:
        try:
            self.proc.stdout.close()
            self.proc.kill()
        except Exception:
            pass
from .planner import EDL, Shot
from .presets import ASPECT_SIZES, MontageParams

ProgressCb = Callable[[int, int, str], None]


class Renderer:
    def __init__(self, edl: EDL, params: MontageParams, photo_paths: list[Path], width: int = 1920, height: int = 1080,
                 font_path: str | None = None):
        self.edl = edl
        self.params = params
        self.photo_paths = photo_paths
        self.w, self.h = width, height
        self.fps = params.output.fps
        e = params.effects
        base_w, base_h = int(round(width * fx.HEADROOM)) + 2, int(round(height * fx.HEADROOM)) + 2
        self.base_w, self.base_h = base_w, base_h
        needed = sorted({s.photo_idx for s in edl.shots if s.photo_idx is not None})
        self.is_video = {i: fx.is_video(photo_paths[i]) for i in needed}
        self.bases: dict[int, Image.Image] = {i: fx.load_cover(photo_paths[i], base_w, base_h, bw=e.bw)
                                              for i in needed if not self.is_video[i]}
        self.streams: dict[int, ClipStream] = {}          # shot index -> live stream
        self.first_frames: dict[int, Image.Image] = {}    # shot index -> first frame (for dissolves)
        self.cards: dict[str, np.ndarray] = {}
        for s in edl.shots:
            if s.card_text is not None and s.card_text not in self.cards:
                self.cards[s.card_text] = fx.render_card(s.card_text, width, height, params.intertitles.font_style, font_path)
        self.lut = fx.contrast_lut(e.contrast) if e.contrast > 0 else None
        self.vig = fx.vignette_mask(width, height, e.vignette) if e.vignette > 0 else None
        self.grain = fx.grain_bank(width, height, e.grain, seed=params.seed) if e.grain > 0 else None
        self.rng = np.random.default_rng(params.seed)
        self.n_frames = int(math.ceil(edl.duration * self.fps))
        self._shot_starts = np.array([s.start for s in edl.shots])
        self._jitter_cache: dict[int, tuple[float, float]] = {}

    # --- frame synthesis -------------------------------------------------
    def shot_at(self, t: float) -> int:
        i = int(np.searchsorted(self._shot_starts, t, side="right") - 1)
        return max(0, min(i, len(self.edl.shots) - 1))

    def photo_frame(self, shot: Shot, t: float, frame_i: int) -> np.ndarray:
        if shot.card_text is not None:
            return self.cards[shot.card_text]
        dur = max(1e-6, shot.end - shot.start)
        u = (t - shot.start) / dur
        zoom = shot.zoom_from + (shot.zoom_to - shot.zoom_from) * fx.ease_out(u)
        pan = (shot.pan[0] * u, shot.pan[1] * u)
        jitter = (0.0, 0.0)
        if shot.shake > 0:
            decay = math.exp(-u * 6.0)
            amp = shot.shake * decay
            if amp > 0.005:
                jitter = (float(self.rng.uniform(-amp, amp)), float(self.rng.uniform(-amp, amp)))
        base = self._base_for(shot, frame_i)
        img = fx.crop_zoom(base, self.w, self.h, zoom, pan, jitter)
        frame = np.asarray(img, dtype=np.uint8)
        if frame.ndim == 2:
            frame = np.repeat(frame[:, :, None], 3, axis=2)
        if self.lut is not None:
            frame = fx.apply_lut(frame, self.lut)
        if self.vig is not None:
            frame = fx.apply_vignette(frame, self.vig)
        if self.grain is not None:
            frame = fx.apply_grain(frame, self.grain[frame_i % len(self.grain)])
        if shot.invert:
            frame = fx.invert(frame)
        return frame

    def _base_for(self, shot: Shot, frame_i: int) -> Image.Image:
        if not self.is_video.get(shot.photo_idx):
            return self.bases[shot.photo_idx]
        si = self.edl.shots.index(shot)
        rel = frame_i - int(round(shot.start * self.fps))
        if rel < 0:  # dissolve preview of the next shot: its first frame
            if si not in self.first_frames:
                self.first_frames[si] = fx.video_frame(self.photo_paths[shot.photo_idx], self.base_w, self.base_h, shot.src_offset).convert(
                    "L" if self.params.effects.bw else "RGB")
            return self.first_frames[si]
        st = self.streams.get(si)
        if st is None:
            st = ClipStream(self.photo_paths[shot.photo_idx], self.base_w, self.base_h, self.fps,
                            shot.end - shot.start, self.params.effects.bw, shot.src_offset)
            self.streams[si] = st
            # close streams of earlier shots
            for k in [k for k in self.streams if k < si]:
                self.streams.pop(k).close()
        return st.frame(rel)

    def frame(self, frame_i: int) -> np.ndarray:
        # Use the frame's midpoint so each cut lands on the frame nearest the beat (error <= half a frame).
        t = (frame_i + 0.5) / self.fps
        si = self.shot_at(t)
        shot = self.edl.shots[si]
        frames_into = frame_i - int(round(shot.start * self.fps))
        e = self.params.effects
        # flash frames at the head of the shot override everything
        if shot.flash and 0 <= frames_into < e.flash_frames:
            return fx.solid(self.w, self.h, 255 if shot.flash == "white" else 0)
        # strobe: 2 frames black every 4 frames
        if shot.strobe and (frames_into % 4) >= 2:
            return fx.solid(self.w, self.h, 0)
        frame = self.photo_frame(shot, t, frame_i)
        # dissolve into next shot over the tail of this shot
        if shot.dissolve_ms > 0 and si + 1 < len(self.edl.shots):
            d = shot.dissolve_ms / 1000.0
            if t >= shot.end - d:
                nxt = self.edl.shots[si + 1]
                nf = self.photo_frame(nxt, nxt.start, int(round(nxt.start * self.fps)) - 1)
                frame = fx.blend(frame, nf, (t - (shot.end - d)) / d)
        return frame

    # --- encoding --------------------------------------------------------
    def ffmpeg_cmd(self, audio_wav: Path, out_path: Path, preview: bool = False) -> list[str]:
        o = self.params.output
        dur = self.n_frames / self.fps
        cmd = [get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.w}x{self.h}", "-r", str(self.fps), "-i", "pipe:0",
               "-i", str(audio_wav), "-t", f"{dur:.4f}"]
        af = []
        if self.edl.fade_out_ms > 0:
            fd = min(self.edl.fade_out_ms / 1000.0, dur)
            af.append(f"afade=t=out:st={max(0.0, dur - fd):.3f}:d={fd:.3f}")
        if af:
            cmd += ["-af", ",".join(af)]
        container = "mp4" if preview else o.container
        codec = "h264" if preview else o.codec
        if codec == "prores_422" and container == "mov":
            cmd += ["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le"]
        else:
            crf = 28 if preview else o.crf
            maxrate = "4M" if preview else "16M"
            bufsize = "8M" if preview else "32M"
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-maxrate", maxrate, "-bufsize", bufsize,
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
            if container == "mp4":
                cmd += ["-movflags", "+faststart"]
        cmd += ["-map", "0:v:0", "-map", "1:a:0", str(out_path)]
        return cmd

    def render(self, audio_wav: Path, out_path: Path, progress: ProgressCb | None = None, preview: bool = False) -> Path:
        cmd = self.ffmpeg_cmd(audio_wav, out_path, preview)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(self.n_frames):
                proc.stdin.write(self.frame(i).tobytes())
                if progress and (i % 30 == 0 or i == self.n_frames - 1):
                    progress(i + 1, self.n_frames, "rendering")
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        for st in self.streams.values():
            st.close()
        err = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err.decode(errors='replace')[-2000:]}")
        return out_path


def render(edl: EDL, params: MontageParams, photo_paths: list[Path], audio_wav: Path, out_path: Path,
           progress: ProgressCb | None = None, preview: bool = False, font_path: str | None = None) -> Path:
    w, h = ASPECT_SIZES.get(params.output.aspect, (1080, 1920))
    if preview:
        w, h = w // 2, h // 2
    r = Renderer(edl, params, photo_paths, w, h, font_path)
    return r.render(audio_wav, out_path, progress, preview)
