"""AI shot analysis: every photo and clip is described, rated, and (for clips) given a key moment that must be
seen in the final montage. Claude vision looks at numbered contact sheets (photos) and time-stamped filmstrips
(clips); without Anthropic credentials a motion heuristic finds each clip's busiest moment instead.

Results live in <job>/shots.json: {"shots": {filename: note}, "summary", "source", "model"}. A note has
  description, importance (1-5), must_include, key_at (seconds into the clip, or null), key_why, duration_s,
  once (never repeat this shot), user (fields the editor set by hand, kept across re-analysis).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from .director import DEFAULT_MODEL, claude_available
from .effects import _font, is_video, video_frame
from .ffmpeg_bin import get_ffmpeg
from .ordering import COLS, ROWS, contact_sheets, ensure_thumbs

STRIP_FRAMES = 6
STRIP_W, STRIP_H = 192, 108
USER_FIELDS = ("must_include", "once", "key_at")


class ShotNote(BaseModel):
    shot: int = Field(description="Shot number as printed on the tile / filmstrip (1-based)")
    description: str = Field(description="What the shot shows, at most twelve words")
    importance: int = Field(ge=1, le=5, description="1 = filler, 3 = good, 5 = essential: the montage would be poorer without it")
    must_include: bool = Field(description="True when this shot has to appear in the final montage")
    key_at: float | None = Field(None, description="Clips only: the second (from the filmstrip timestamps) where the key moment happens; null for photos")
    key_why: str = Field("", description="Clips only: what the key moment is, a few words")


class ShotNotes(BaseModel):
    shots: list[ShotNote]
    summary: str = Field(description="Two or three sentences: what the material is about and which moments carry it")


SYSTEM = """You are the editor's assistant for a beat-cut photo/video montage. You are shown the source material: photos as numbered
tiles on contact sheets, video clips as numbered filmstrips (frames left to right with their timestamps in seconds).
For every shot number write a short description, rate how important it is for the final montage (1-5), and say whether it
MUST be in the final cut (must_include): faces and reactions, the decisive action, the establishing view, the emotional peak,
anything the director's notes ask for. Be selective: must_include should be true for the shots that carry the story, not for all.
For each CLIP pick the key moment (key_at, seconds): the instant that has to be seen - the jump, the kiss, the goal, the reveal,
the look to camera. Use the timestamps printed on the filmstrip; pick a frame time, or a time between two frames when the
action clearly happens in between. Photos get key_at null. Answer for every shot number you were shown, each exactly once."""


# ---- probing ---------------------------------------------------------------------------------------------------------
def video_duration(path: Path) -> float | None:
    """Clip length in seconds from ffmpeg's stream info (no ffprobe in the bundled build)."""
    try:
        out = subprocess.run([get_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    except Exception:
        return None
    m = re.search(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", out)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def sample_times(duration: float | None, n: int = STRIP_FRAMES) -> list[float]:
    d = duration or 2.0
    if d <= 0.5:
        return [0.0]
    step = d / n
    return [round(min(d - 0.05, step * (i + 0.5)), 2) for i in range(n)]


def filmstrip(path: Path, number: int, duration: float | None) -> tuple[bytes, list[float]]:
    """One JPEG strip: STRIP_FRAMES cover-cropped frames with their timestamps, numbered like the contact-sheet tiles."""
    times = sample_times(duration)
    strip = Image.new("RGB", (STRIP_W * len(times), STRIP_H + 22), (20, 20, 20))
    d = ImageDraw.Draw(strip)
    font, small = _font(28), _font(16)
    for i, t in enumerate(times):
        try:
            fr = video_frame(path, STRIP_W, STRIP_H, t)
        except Exception:
            fr = Image.new("RGB", (STRIP_W, STRIP_H), (60, 60, 60))
        strip.paste(fr, (i * STRIP_W, 22))
        d.text((i * STRIP_W + 4, 2), f"{t:.1f}s", fill=(255, 255, 255), font=small)
    label = f"CLIP {number}"
    d.rectangle([0, 0, 24 + 16 * len(label), 22], fill=(200, 30, 30))
    d.text((6, -2), label, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    strip.save(buf, "JPEG", quality=85)
    return buf.getvalue(), times


# ---- storage -----------------------------------------------------------------------------------------------------------
def notes_path(job) -> Path:
    return job.dir / "shots.json"


def load_notes(job) -> dict | None:
    p = notes_path(job)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save_notes(job, notes: dict) -> None:
    notes_path(job).write_text(json.dumps(notes))


def _blank(name: str, duration: float | None) -> dict:
    return {"description": "", "importance": 3, "must_include": False, "key_at": None, "key_why": "",
            "duration_s": duration, "once": False, "user": {}, "kind": "clip" if is_video(name) else "photo"}


def shot_info_for_plan(job) -> list[dict] | None:
    """Per-shot planning hints aligned with job.photos, or None when nothing has been analysed yet."""
    notes = load_notes(job)
    if not notes:
        return None
    out = []
    for name in job.photos:
        n = notes.get("shots", {}).get(name) or _blank(name, None)
        out.append({"weight": int(n.get("importance", 3)), "must": bool(n.get("must_include")), "once": bool(n.get("once")),
                    "key_at": n.get("key_at"), "duration": n.get("duration_s")})
    return out


def set_user_fields(job, name: str, fields: dict) -> dict:
    """Editor overrides (must_include / once / key_at) for one shot; kept across re-analysis."""
    notes = load_notes(job) or {"shots": {}, "summary": "", "source": "manual"}
    shots = notes.setdefault("shots", {})
    n = shots.get(name)
    if n is None:
        n = _blank(name, video_duration(job.photo_dir / name) if is_video(name) else None)
        shots[name] = n
    for k, v in fields.items():
        if k not in USER_FIELDS:
            continue
        if k == "key_at":
            v = None if v is None else max(0.0, float(v))
            if v is not None and n.get("duration_s"):
                v = min(v, float(n["duration_s"]))
        else:
            v = bool(v)
        n[k] = v
        n.setdefault("user", {})[k] = v
    save_notes(job, notes)
    return n


# ---- analysis ----------------------------------------------------------------------------------------------------------
def _motion_key_moment(path: Path, duration: float | None) -> tuple[float | None, float]:
    """Busiest instant of a clip by frame differencing (small grey frames every ~0.4 s). Returns (key_at, motion 0..1)."""
    d = duration or 2.0
    n = int(min(24, max(4, d / 0.4)))
    times = [d * (i + 0.5) / n for i in range(n)]
    frames = []
    for t in times:
        try:
            frames.append(np.asarray(video_frame(path, 64, 36, t).convert("L"), dtype=float))
        except Exception:
            frames.append(np.zeros((36, 64)))
    if len(frames) < 2:
        return (times[0] if times else None), 0.0
    diffs = np.array([np.abs(frames[i + 1] - frames[i]).mean() for i in range(len(frames) - 1)])
    k = int(diffs.argmax())
    motion = float(min(1.0, diffs[k] / 40.0))
    return round((times[k] + times[k + 1]) / 2, 2), motion


def heuristic_notes(job) -> dict:
    shots = {}
    for name in job.photos:
        path = job.photo_dir / name
        if is_video(name):
            dur = video_duration(path)
            key_at, motion = _motion_key_moment(path, dur)
            n = _blank(name, dur)
            n.update({"description": f"clip, most movement around {key_at:.1f} s" if key_at is not None else "clip",
                      "importance": 3 + (1 if motion > 0.25 else 0), "must_include": True, "key_at": key_at,
                      "key_why": "busiest moment (motion heuristic)"})
        else:
            n = _blank(name, None)
            n["description"] = "photo"
        shots[name] = n
    return {"shots": shots, "source": "heuristic",
            "summary": ("No Anthropic credentials, so no AI judgement: every clip is marked must-include with its busiest "
                        "moment as the key moment; photos are rated 3 and left for you to star.")}


def claude_notes(job, notes_text: str, style_preset: str) -> dict:
    import anthropic

    names = list(job.photos)
    thumbs = ensure_thumbs(job)
    durations = {nm: (video_duration(job.photo_dir / nm) if is_video(nm) else None) for nm in names}
    client = anthropic.Anthropic()
    model = os.environ.get("NULLMONTAGE_MODEL", DEFAULT_MODEL)
    per = COLS * ROWS
    result: dict[str, dict] = {}
    summaries: list[str] = []
    for s in range(0, len(names), per):
        chunk = list(range(s, min(len(names), s + per)))
        content: list[dict] = []
        photo_idx = [i for i in chunk if not is_video(names[i])]
        if photo_idx:
            # one sheet for this chunk's photos; tiles keep their global shot numbers via the offset trick below
            sheet_thumbs = [thumbs[i] for i in chunk]
            sheet = contact_sheets(sheet_thumbs, [names[i] for i in chunk])[0]
            content.append({"type": "text", "text": f"Contact sheet: tiles are numbered 1-{len(chunk)}; add {s} to get the shot number (shots {s + 1}-{s + len(chunk)}). CLIP tiles are video clips, shown again below as filmstrips."})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(sheet).decode()}})
        for i in chunk:
            if not is_video(names[i]):
                continue
            jpg, times = filmstrip(job.photo_dir / names[i], i + 1, durations[names[i]])
            content.append({"type": "text", "text": f"Filmstrip for shot {i + 1} (clip, {durations[names[i]] or 0:.1f} s long; frames at {', '.join(f'{t:.1f}' for t in times)} s):"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(jpg).decode()}})
        kinds = [f"{i + 1}: {'clip' if is_video(names[i]) else 'photo'} {names[i].split('_', 1)[-1]}" for i in chunk]
        content.append({"type": "text", "text": (
            f"Shots {chunk[0] + 1}-{chunk[-1] + 1} (number: kind filename):\n" + "\n".join(kinds) +
            f"\n\nStyle preset: {style_preset}\nDirector's notes: {notes_text.strip() or '(none)'}\n\nAnswer for shots {chunk[0] + 1}-{chunk[-1] + 1}.")})
        response = client.messages.parse(model=model, max_tokens=8000, system=SYSTEM,
                                         messages=[{"role": "user", "content": content}], output_format=ShotNotes)
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request")
        parsed: ShotNotes = response.parsed_output
        summaries.append(parsed.summary)
        for sn in parsed.shots:
            i = sn.shot - 1
            if not (0 <= i < len(names)):
                continue
            nm = names[i]
            n = _blank(nm, durations[nm])
            key_at = None
            if is_video(nm) and sn.key_at is not None:
                key_at = max(0.0, float(sn.key_at))
                if durations[nm]:
                    key_at = min(key_at, max(0.0, durations[nm] - 0.1))
            n.update({"description": sn.description.strip(), "importance": int(sn.importance), "must_include": bool(sn.must_include),
                      "key_at": key_at, "key_why": sn.key_why.strip() if is_video(nm) else ""})
            result[nm] = n
    for nm in names:  # anything Claude skipped
        if nm not in result:
            n = _blank(nm, durations[nm])
            if is_video(nm):
                n["key_at"], _ = _motion_key_moment(job.photo_dir / nm, durations[nm])
                n["must_include"] = True
            result[nm] = n
    return {"shots": result, "summary": " ".join(summaries), "source": "claude", "model": model}


def analyze_shots(job, notes_text: str = "", style_preset: str = "custom") -> dict:
    """Analyse every shot; editor overrides from a previous pass are re-applied on top."""
    previous = load_notes(job) or {}
    if claude_available():
        try:
            res = claude_notes(job, notes_text, style_preset)
        except Exception as exc:
            res = heuristic_notes(job)
            res["summary"] = f"Claude unavailable ({type(exc).__name__}: {exc}). " + res["summary"]
    else:
        res = heuristic_notes(job)
    for nm, n in res["shots"].items():
        user = (previous.get("shots", {}).get(nm) or {}).get("user") or {}
        n["user"] = dict(user)
        n.update(user)
    save_notes(job, res)
    return res
