"""FastAPI server: upload photos + audio, analyze, direct, plan, render, download."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audio as audio_mod
from .auth import PasswordGate
from .director import claude_available, interpret
from .effects import IMAGE_EXT, VIDEO_EXT, check_source, make_thumbnail, mean_brightness, placeholder_thumbnail, sniff_kind
from .jobs import JobStore, run_in_thread
from .ordering import decide_order
from .planner import _jsonable, plan
from .presets import PRESET_DESCRIPTIONS, STYLE_PRESETS, MontageParams
from .render import render
from . import shots as shots_mod

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="nullmontage")
store = JobStore()
PASSWORD = os.environ.get("NULLMONTAGE_PASSWORD", "")
if PASSWORD:
    app.add_middleware(PasswordGate, password=PASSWORD)
SOURCE_EXT = IMAGE_EXT | VIDEO_EXT
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif"}


def _job(jid: str):
    job = store.get(jid)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "file")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "file"


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/api/presets")
def presets():
    return {"presets": STYLE_PRESETS, "descriptions": PRESET_DESCRIPTIONS,
            "defaults": MontageParams().with_preset("clean_beat").model_dump(),
            "schema": MontageParams.model_json_schema(), "claude": claude_available()}


@app.get("/api/jobs")
def list_jobs():
    return store.list()


@app.post("/api/jobs")
def create_job():
    return store.create().to_dict()


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return _job(jid).to_dict()


@app.post("/api/jobs/{jid}/photos")
async def upload_photos(jid: str, files: list[UploadFile] = File(...)):
    job = _job(jid)
    names, rejected = [], []
    for i, f in enumerate(files):
        ext = Path(f.filename or "").suffix.lower()
        name = f"{len(job.photos) + i:03d}_{_safe_name(f.filename)}"
        dest = job.photo_dir / name
        with open(dest, "wb") as fh:
            while chunk := await f.read(1 << 20):
                fh.write(chunk)
        if dest.stat().st_size == 0:
            dest.unlink()
            rejected.append({"file": f.filename, "reason": "empty file"})
            continue
        kind = sniff_kind(dest)
        if kind is None and ext not in SOURCE_EXT:
            dest.unlink()
            rejected.append({"file": f.filename, "reason": f"unsupported type {ext or '(none)'}"})
            continue
        ok, reason = check_source(dest)
        if not ok:
            dest.unlink()
            rejected.append({"file": f.filename, "reason": reason})
            continue
        names.append(name)
    job.set(photos=job.photos + names, status="new" if not job.audio else job.status)
    d = job.to_dict()
    d["rejected"] = rejected
    d["added"] = len(names)
    return d


def _validate_sources(job) -> list[dict]:
    """Drop sources that can no longer be decoded from the job. Returns the skipped list."""
    cache_path = job.dir / "sources.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    keep, skipped = [], []
    for name in job.photos:
        path = job.photo_dir / name
        if not path.exists():
            skipped.append({"file": name, "reason": "file missing"})
            continue
        key = f"{name}:{path.stat().st_size}"
        if key not in cache:
            ok, reason = check_source(path)
            cache[key] = [ok, reason]
        ok, reason = cache[key]
        (keep if ok else skipped).append(name if ok else {"file": name, "reason": reason})
    cache_path.write_text(json.dumps(cache))
    if skipped:
        job.set(photos=keep)
    return skipped


class Reorder(BaseModel):
    photos: list[str]


class OrderBody(BaseModel):
    notes: str = ""
    style_preset: str = "custom"


@app.post("/api/jobs/{jid}/photos/ai-order")
def ai_order(jid: str, body: OrderBody):
    """Let the AI decide the playback order of the photos/clips and apply it (the previous order is returned for undo)."""
    job = _job(jid)
    _validate_sources(job)
    if len(job.photos) < 2:
        raise HTTPException(400, "upload at least two photos or clips first")
    summary = None
    latest = max(job.dir.glob("analysis*.json"), key=lambda f: f.stat().st_mtime, default=None)
    if job.audio and latest:
        summary = audio_mod.BeatGrid.load(latest).summary()
    res = decide_order(job, body.notes, body.style_preset, summary)
    job.set(photos=res["photos"])
    (job.dir / "order.json").write_text(json.dumps(res))
    d = job.to_dict()
    d.update({k: v for k, v in res.items() if k != "photos"})
    return d


class AnalyzeBody(BaseModel):
    notes: str = ""
    style_preset: str = "custom"


@app.post("/api/jobs/{jid}/photos/analyze")
def analyze_shots(jid: str, body: AnalyzeBody):
    """AI looks at every photo and clip: description, importance, must-include, and each clip's key moment."""
    job = _job(jid)
    _validate_sources(job)
    if not job.photos:
        raise HTTPException(400, "upload photos or clips first")
    job.set(message="analysing shots")
    try:
        res = shots_mod.analyze_shots(job, body.notes, body.style_preset)
    finally:
        job.set(message="")
    return res


@app.get("/api/jobs/{jid}/shots")
def get_shots(jid: str):
    job = _job(jid)
    return shots_mod.load_notes(job) or {"shots": {}, "summary": "", "source": None}


class ShotPatch(BaseModel):
    must_include: bool | None = None
    once: bool | None = None
    key_at: float | None = None
    clear_key: bool = False


@app.patch("/api/jobs/{jid}/shots/{name}")
def patch_shot(jid: str, name: str, body: ShotPatch):
    """Editor overrides for one shot: star it (must_include), flag it once (never repeat), move its key moment."""
    job = _job(jid)
    if name not in job.photos:
        raise HTTPException(404, "no such shot")
    fields = {k: v for k, v in body.model_dump().items() if k in shots_mod.USER_FIELDS and v is not None}
    if body.clear_key:
        fields["key_at"] = None
    return shots_mod.set_user_fields(job, name, fields)


@app.post("/api/jobs/{jid}/photos/order")
def reorder_photos(jid: str, body: Reorder):
    job = _job(jid)
    if sorted(body.photos) != sorted(job.photos):
        raise HTTPException(400, "photo list mismatch")
    job.set(photos=body.photos)
    return job.to_dict()


@app.delete("/api/jobs/{jid}/photos/{name}")
def delete_photo(jid: str, name: str):
    job = _job(jid)
    if name in job.photos:
        for path in (job.photo_dir / name, job.dir / "thumbs" / (name + ".jpg")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        job.set(photos=[p for p in job.photos if p != name])
    return job.to_dict()


@app.get("/api/jobs/{jid}/thumbs/{name}")
def get_thumb(jid: str, name: str):
    job = _job(jid)
    if name not in job.photos:
        raise HTTPException(404)
    tdir = job.dir / "thumbs"
    tdir.mkdir(exist_ok=True)
    t = tdir / (name + ".jpg")
    if not t.exists():
        try:
            make_thumbnail(job.photo_dir / name, t)
        except Exception:
            placeholder_thumbnail(t)
    return FileResponse(t, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/jobs/{jid}/photos/{name}")
def get_photo(jid: str, name: str):
    job = _job(jid)
    if name not in job.photos:
        raise HTTPException(404)
    return FileResponse(job.photo_dir / name)


@app.get("/api/jobs/{jid}/check")
def check_sources(jid: str):
    job = _job(jid)
    skipped = _validate_sources(job)
    d = job.to_dict()
    d["skipped"] = skipped
    return d


@app.post("/api/jobs/{jid}/audio")
async def upload_audio(jid: str, file: UploadFile = File(...)):
    job = _job(jid)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, f"unsupported audio type {ext}")
    name = "track" + ext
    (job.dir / name).write_bytes(await file.read())
    for stale in [*job.dir.glob("analysis*"), *job.dir.glob("mux*")]:
        stale.unlink(missing_ok=True)
    job.set(audio=name, status="new")
    return job.to_dict()


@app.get("/api/jobs/{jid}/audio")
def get_audio(jid: str):
    """The uploaded track itself, for the in-page player used to pick which part of the song to use."""
    job = _job(jid)
    if not job.audio:
        raise HTTPException(404, "no audio")
    return FileResponse(job.dir / job.audio)


def _song_range(start_s: float, end_s: float) -> tuple[float, float]:
    """Normalise the chosen part of the song: end 0 or not after start = play to the end."""
    start_s = max(0.0, float(start_s or 0.0))
    end_s = float(end_s or 0.0)
    return start_s, (end_s if end_s > start_s else 0.0)


def _analysis(job, audio_start_s: float = 0.0, speed: float = 1.0, keep_pitch: bool = True, audio_end_s: float = 0.0):
    """Beat grid for the part of the track that will be heard. Beats are detected once per chosen range on the
    original-speed audio (cached per range) and scaled: time-stretching moves every beat by exactly the same
    factor, and detecting on the sped-up audio risks half/double-tempo errors."""
    audio_start_s, audio_end_s = _song_range(audio_start_s, audio_end_s)
    tag = f"{audio_start_s:.3f}-{audio_end_s:.3f}"
    key = job.dir / f"analysis-{tag}.json"
    if key.exists():
        return audio_mod.BeatGrid.load(key).scaled(speed)
    if not job.audio:
        raise HTTPException(400, "upload audio first")
    job.set(status="analyzing", message="decoding + beat tracking")
    wav = audio_mod.decode_to_wav(job.dir / job.audio, job.dir / f"analysis-{tag}.wav", audio_start_s, end_s=audio_end_s)
    grid = audio_mod.analyze(wav)
    grid.save(key)
    job.set(status="analyzed", message="")
    return grid.scaled(speed)


@app.get("/api/jobs/{jid}/analysis")
def analysis(jid: str, audio_start_s: float = 0.0, speed: float = 1.0, keep_pitch: bool = True, audio_end_s: float = 0.0):
    job = _job(jid)
    grid = _analysis(job, audio_start_s, speed, keep_pitch, audio_end_s)
    d = _jsonable(grid.to_dict())
    d["summary"] = grid.summary()
    return d


class DirectBody(BaseModel):
    params: MontageParams
    notes: str = ""


@app.post("/api/jobs/{jid}/direct")
def direct(jid: str, body: DirectBody):
    job = _job(jid)
    summary = None
    if job.audio and (job.dir / "analysis.json").exists():
        summary = audio_mod.BeatGrid.load(job.dir / "analysis.json").summary()
    res = interpret(body.params, body.notes, summary, len(job.photos))
    return {"params": res["params"].model_dump(), "changes": res["changes"], "source": res["source"],
            "rationale": res.get("rationale", ""), "model": res.get("model")}


class PlanBody(BaseModel):
    params: MontageParams


def _ramp(job, params: MontageParams, base_grid, n_photos: int, brightness):
    """For accelerate/decelerate pacing with a song speed ramp: return (grid, mux_path). The ramped audio is
    rendered segment by segment (cached by its parameters) and the grid's beat times follow the MEASURED
    output, so cuts land on the beats exactly as they will be heard. Otherwise (grid, None)."""
    o = params.output
    grid = base_grid.scaled(o.speed)
    if params.pacing not in ("accelerate", "decelerate") or params.song_speed_ramp <= 1.0 + 1e-6:
        return grid, None
    # plan once at uniform speed to learn which stretch of the song the video uses (tick structure is speed-invariant)
    edl0 = plan(params, grid, n_photos, brightness, shots_mod.shot_info_for_plan(job))
    t0 = edl0.shots[0].start * o.speed
    t1 = edl0.shots[-1].end * o.speed
    gamma = 0.5 + params.accel_curve * 2.0
    segs = audio_mod.ramp_segments(t0, t1, base_grid.duration, o.speed, params.song_speed_ramp, gamma,
                                   reverse=(params.pacing == "decelerate"))
    key = hashlib.sha1(json.dumps([segs, o.keep_pitch, o.audio_start_s, o.audio_end_s, job.audio]).encode()).hexdigest()[:12]
    mux = job.dir / f"mux-ramp-{key}.wav"
    mapf = job.dir / f"mux-ramp-{key}.json"
    if mux.exists() and mapf.exists():
        m = json.loads(mapf.read_text())
        tmap = audio_mod.TimeMap([tuple(x) for x in m["segments"]], m["out_lengths"])
    else:
        for old in job.dir.glob("mux-ramp-*"):
            old.unlink()
        job.set(message="rendering tempo ramp")
        tmap = audio_mod.render_ramped_audio(job.dir / job.audio, mux, segs, o.keep_pitch, o.audio_start_s, end_s=o.audio_end_s)
        mapf.write_text(json.dumps({"segments": segs, "out_lengths": tmap.out_lengths}))
    return base_grid.remapped(tmap), mux


def _brightness(job) -> list[float] | None:
    cache = job.dir / "brightness.json"
    if cache.exists():
        d = json.loads(cache.read_text())
        if d.get("photos") == job.photos:
            return d["values"]
    vals = [mean_brightness(p) for p in job.photo_paths()]
    cache.write_text(json.dumps({"photos": job.photos, "values": vals}))
    return vals


@app.post("/api/jobs/{jid}/plan")
def make_plan(jid: str, body: PlanBody):
    job = _job(jid)
    skipped = _validate_sources(job)
    if not job.photos:
        raise HTTPException(400, "no usable photos or clips" + (f" ({len(skipped)} unreadable)" if skipped else ""))
    o = body.params.output
    base = _analysis(job, o.audio_start_s, 1.0, o.keep_pitch, o.audio_end_s)
    bright = _brightness(job) if body.params.order == "brightness_sort" else None
    grid, ramp_mux = _ramp(job, body.params, base, len(job.photos), bright)
    edl = plan(body.params, grid, len(job.photos), bright, shots_mod.shot_info_for_plan(job))
    d = edl.to_dict()
    d["skipped"] = skipped
    o = body.params.output
    d["song_ramp"] = [round(o.speed, 2), round(o.speed * body.params.song_speed_ramp, 2)] if ramp_mux else None
    (job.dir / "edl.json").write_text(json.dumps(d))
    (job.dir / "params.json").write_text(body.params.model_dump_json())
    job.set(status="planned", message="")
    d["summary"] = grid.summary()
    return d


class RenderBody(BaseModel):
    params: MontageParams
    preview: bool = False


def _render_thread(job, params: MontageParams, preview: bool):
    try:
        skipped = _validate_sources(job)
        if not job.photos:
            raise RuntimeError("no usable photos or clips")
        o = params.output
        base = _analysis(job, o.audio_start_s, 1.0, o.keep_pitch, o.audio_end_s)
        bright = _brightness(job) if params.order == "brightness_sort" else None
        grid, ramp_mux = _ramp(job, params, base, len(job.photos), bright)
        edl = plan(params, grid, len(job.photos), bright, shots_mod.shot_info_for_plan(job))
        (job.dir / "edl.json").write_text(json.dumps(edl.to_dict()))
        job.set(status="rendering", progress=0.0, message="decoding audio for mux", error=None)
        mux = ramp_mux or audio_mod.decode_for_mux(job.dir / job.audio, job.dir / "mux.wav", o.audio_start_s, o.speed, o.keep_pitch, end_s=o.audio_end_s)
        ext = "mp4" if preview else params.output.container
        name = f"preview-{job.id}.mp4" if preview else f"montage-{job.id}.{ext}"
        out = job.dir / name
        t0 = time.time()

        def cb(i, n, msg):
            el = time.time() - t0
            eta = (n - i) * el / max(1, i)
            job.set(progress=i / n, message=f"{msg} {i}/{n} frames, ~{int(eta)}s left")

        job.set(message="loading photos")
        render(edl, params, job.photo_paths(), mux, out, progress=cb, preview=preview,
               font_path=os.environ.get("NULLMONTAGE_FONT"))
        note = f" (skipped {len(skipped)} unreadable file(s))" if skipped else ""
        job.set(status="done", progress=1.0, output=name, message=f"done in {int(time.time() - t0)}s{note}")
    except Exception as exc:
        job.set(status="error", error=f"{type(exc).__name__}: {exc}", message="")


@app.post("/api/jobs/{jid}/render")
def start_render(jid: str, body: RenderBody):
    job = _job(jid)
    if not job.photos or not job.audio:
        raise HTTPException(400, "upload photos and audio first")
    if job.status == "rendering":
        raise HTTPException(409, "already rendering")
    job.set(status="rendering", progress=0.0, message="starting", output=None, error=None)
    run_in_thread(_render_thread, job, body.params, body.preview)
    return job.to_dict()


@app.get("/api/jobs/{jid}/events")
async def events(jid: str):
    job = _job(jid)

    async def gen():
        last = None
        while True:
            d = job.to_dict()
            s = json.dumps(d)
            if s != last:
                yield f"data: {s}\n\n"
                last = s
            if d["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{jid}/output")
def output(jid: str, download: bool = False):
    job = _job(jid)
    if not job.output or not (job.dir / job.output).exists():
        raise HTTPException(404, "no output yet")
    path = job.dir / job.output
    media = "video/quicktime" if path.suffix == ".mov" else "video/mp4"
    kw = {"filename": job.output} if download else {}
    return FileResponse(path, media_type=media, **kw)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main():
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("NULLMONTAGE_HOST", "127.0.0.1")  # set NULLMONTAGE_HOST=0.0.0.0 to reach it from other devices on your LAN
    # The Docker image sets this: a hosted copy without a password would let anyone upload and render.
    if os.environ.get("NULLMONTAGE_REQUIRE_PASSWORD") == "1" and not PASSWORD:
        raise SystemExit("NULLMONTAGE_PASSWORD is not set; refusing to start an open server")
    print(f"nullmontage → http://localhost:{port}", flush=True)
    uvicorn.run("nullmontage.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
