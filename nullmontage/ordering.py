"""AI shot ordering: Claude looks at every photo and clip (as numbered contact sheets) together with the
director's notes and decides the playback order. Without Anthropic credentials a visual-flow heuristic
(greedy nearest-neighbour on tiny thumbnails, so adjacent shots look continuous) is used instead."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from .director import DEFAULT_MODEL, claude_available
from .effects import _font, is_video, make_thumbnail, placeholder_thumbnail

TILE_W, TILE_H = 256, 144
COLS, ROWS = 6, 5  # 30 shots per contact sheet


class OrderPlan(BaseModel):
    """Claude's answer: every shot number exactly once, in playback order."""
    order: list[int] = Field(description="Shot numbers (1-based, as printed on the tiles) in the order they should play; every number exactly once")
    rationale: str = Field(description="Two to four sentences on the narrative/visual logic of the sequence")
    opening: str = Field("", description="Why the first shot opens")
    climax_shots: list[int] = Field(default_factory=list, description="Shot numbers that should land on the musical peak")
    closing: str = Field("", description="Why the last shot closes")


SYSTEM = """You are the editor's assistant for a beat-cut photo/video montage. You are shown every source shot as numbered tiles
on contact sheets (CLIP tiles are video clips, the rest are photos), plus the director's notes and the music analysis.
Decide the order in which the shots should play. Think like a film editor:
- Open with an establishing or striking image that sets place and mood; close on the strongest or most emotional image.
- Build: calm/wide -> busier/closer, or dark -> bright, so the sequence escalates toward the musical peak. Put the most dynamic
  shots (action, faces up close, clips with motion) where the climax lands; quieter images in the intro and outro.
- Keep adjacent shots visually continuous (similar palette, brightness, direction of motion) unless the notes ask for collision/contrast.
- Group shots of the same subject, place or moment so they read as beats of a story; avoid near-duplicates back to back.
- Respect explicit instructions in the notes (chronological, keep my order, a person who must open or close, alternate A/B, ...).
Use every shot number exactly once. Be decisive."""


def ensure_thumbs(job) -> list[Path]:
    tdir = job.dir / "thumbs"
    tdir.mkdir(exist_ok=True)
    out = []
    for name in job.photos:
        t = tdir / (name + ".jpg")
        if not t.exists():
            try:
                make_thumbnail(job.photo_dir / name, t)
            except Exception:
                placeholder_thumbnail(t)
        out.append(t)
    return out


def contact_sheets(thumbs: list[Path], names: list[str]) -> list[bytes]:
    """Numbered contact sheets (JPEG bytes), COLS x ROWS tiles each."""
    sheets = []
    per = COLS * ROWS
    font = _font(34)
    small = _font(18)
    for s in range(0, len(thumbs), per):
        chunk = thumbs[s:s + per]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * TILE_W, rows * TILE_H), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for i, t in enumerate(chunk):
            n = s + i + 1
            x, y = (i % COLS) * TILE_W, (i // COLS) * TILE_H
            try:
                tile = Image.open(t).convert("RGB").resize((TILE_W - 4, TILE_H - 4))
            except Exception:
                tile = Image.new("RGB", (TILE_W - 4, TILE_H - 4), (60, 60, 60))
            sheet.paste(tile, (x + 2, y + 2))
            label = str(n)
            d.rectangle([x + 2, y + 2, x + 24 + 18 * len(label), y + 44], fill=(0, 0, 0))
            d.text((x + 10, y + 4), label, fill=(255, 255, 255), font=font)
            if is_video(names[s + i]):
                d.rectangle([x + TILE_W - 62, y + 2, x + TILE_W - 2, y + 26], fill=(200, 30, 30))
                d.text((x + TILE_W - 56, y + 4), "CLIP", fill=(255, 255, 255), font=small)
        buf = io.BytesIO()
        sheet.save(buf, "JPEG", quality=85)
        sheets.append(buf.getvalue())
    return sheets


def _features(path: Path) -> np.ndarray:
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return np.zeros(64 + 3, dtype=float)
    g = np.asarray(img.convert("L").resize((8, 8)), dtype=float).ravel() / 255.0
    rgb = np.asarray(img.resize((1, 1)), dtype=float).ravel() / 255.0
    return np.concatenate([g, rgb * 2.0])


def heuristic_order(thumbs: list[Path]) -> tuple[list[int], str]:
    """Greedy visual-flow path: start on the first uploaded shot, always continue with the most similar unused shot."""
    n = len(thumbs)
    if n <= 2:
        return list(range(n)), "Fewer than three shots: order kept."
    f = np.stack([_features(t) for t in thumbs])
    d = np.linalg.norm(f[:, None, :] - f[None, :, :], axis=2)
    order = [0]
    left = set(range(1, n))
    while left:
        cur = order[-1]
        nxt = min(left, key=lambda j: d[cur, j])
        order.append(nxt)
        left.remove(nxt)
    return order, ("Visual-flow heuristic (no Anthropic credentials, so no AI judgement): kept your first shot, then always "
                   "continued with the most similar-looking unused shot so adjacent cuts feel continuous.")


def repair(order: list[int], n: int) -> list[int]:
    """Turn Claude's 1-based list into a valid 0-based permutation: drop junk/dupes, append anything missing in upload order."""
    seen, out = set(), []
    for v in order:
        i = int(v) - 1
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    out.extend(i for i in range(n) if i not in seen)
    return out


def claude_order(thumbs: list[Path], names: list[str], notes: str, style_preset: str, grid_summary: dict | None) -> dict:
    import anthropic

    sheets = contact_sheets(thumbs, names)
    content: list[dict] = []
    for k, jpg in enumerate(sheets):
        content.append({"type": "text", "text": f"Contact sheet {k + 1} of {len(sheets)} (shots {k * COLS * ROWS + 1}-{min(len(thumbs), (k + 1) * COLS * ROWS)}):"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.standard_b64encode(jpg).decode()}})
    kinds = [f"{i + 1}: {'clip' if is_video(nm) else 'photo'} {nm.split('_', 1)[-1]}" for i, nm in enumerate(names)]
    content.append({"type": "text", "text": (
        f"{len(thumbs)} shots in upload order (number: kind filename):\n" + "\n".join(kinds) +
        f"\n\nStyle preset: {style_preset}\nDirector's notes: {notes.strip() or '(none)'}\n"
        f"Music analysis: {json.dumps(grid_summary or {})}\n\nReturn the playback order of all {len(thumbs)} shot numbers.")})
    client = anthropic.Anthropic()
    model = os.environ.get("NULLMONTAGE_MODEL", DEFAULT_MODEL)
    response = client.messages.parse(
        model=model, max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=OrderPlan,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request")
    plan: OrderPlan = response.parsed_output
    order = repair(plan.order, len(thumbs))
    return {"order": order, "rationale": plan.rationale, "opening": plan.opening, "closing": plan.closing,
            "climax_shots": [i for i in repair(plan.climax_shots, len(thumbs))[: len(plan.climax_shots)]],
            "source": "claude", "model": model}


def decide_order(job, notes: str, style_preset: str, grid_summary: dict | None) -> dict:
    """Returns {"photos": [...new order of filenames...], "previous": [...], "source", "rationale", ...}."""
    names = list(job.photos)
    thumbs = ensure_thumbs(job)
    if claude_available():
        try:
            res = claude_order(thumbs, names, notes, style_preset, grid_summary)
        except Exception as exc:
            order, why = heuristic_order(thumbs)
            res = {"order": order, "source": "heuristic", "rationale": f"Claude unavailable ({type(exc).__name__}: {exc}); {why}"}
    else:
        order, why = heuristic_order(thumbs)
        res = {"order": order, "source": "heuristic", "rationale": why}
    res["previous"] = names
    res["photos"] = [names[i] for i in res.pop("order")]
    return res
