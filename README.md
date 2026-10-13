# nullmontage

Drop photos (and video clips) and a song into a local web page, describe the edit, and get a video where **every cut lands on a beat** in the style of Soviet montage (Eisenstein, Vertov, Kuleshov). Output is vertical 1080x1920 by default (also 16:9, 4:5 or 1:1), as MP4 (H.264) or MOV (H.264 or ProRes 422). Photos and clips are centre-cropped to fill the frame.

## Run

```bash
cd ~/Projects/nullmontage
uv sync --all-groups        # first time only (Python 3.12 + bundled ffmpeg, no brew/Docker)
uv run nullmontage          # → http://localhost:8765
```

Optional: `export ANTHROPIC_API_KEY=...` (or `ant auth login`) so the director's notes box is interpreted by Claude. Without a key the notes are parsed by keyword rules. `NULLMONTAGE_MODEL` overrides the model (default `claude-opus-5`). `NULLMONTAGE_FONT=/path/to/font.ttf` overrides the intertitle font. `PORT` changes the port, `NULLMONTAGE_HOST` the bind address.

## Host it locally (always on)

```bash
scripts/service.sh install    # launchd user agent: starts at login, restarts on crash
scripts/service.sh status     # running? pid? HTTP check
scripts/service.sh restart    # after pulling code changes
scripts/service.sh logs       # tail data/server.log
scripts/service.sh uninstall
```

The agent runs `.venv/bin/nullmontage` from this folder and logs to `data/server.log`. Run `install` with `ANTHROPIC_API_KEY` exported to bake the key into the service for the Claude director. `PORT=9000 scripts/service.sh install` changes the port; `NULLMONTAGE_HOST=0.0.0.0 scripts/service.sh install` exposes it to other devices on your LAN (then open `http://<your-mac>.local:8765`).

## Workflow in the UI

1. **Photos & clips**: drop any number of photos (JPG, PNG, HEIC, WebP…) and video clips (MOV, MP4…). Upload order is the edit order; drag thumbnails to reorder, hover to remove. Uploads go in batches of 10 with a progress readout, and the grid shows small server-made thumbnails, so large phone photos don't slow the page. Files are checked by content, not extension: anything that cannot be decoded (for example the `.jpeg`-named property-list files macOS creates when you drag directly out of the Photos app) is rejected with a reason, and files that go bad later are skipped at plan/render time with a note. From Photos, use *File → Export* (or drag to the Finder first) to get real image files. A clip plays from its start for the length of its shot (looping if shorter) and gets the same effects as the photos; its own audio is ignored.
2. **Music**: drop an MP3/WAV/M4A/FLAC. It is decoded and beat-tracked (BPM, beats, downbeats, energy, sections).
3. **Direction**: the *clean beat* preset is loaded by default (no effects, just cuts on the beat). Every video opens with an intro card, "MADE BY JVH" by default; change or clear it in the Rhythm panel. Pick *soviet montage* or another style preset for effects, then adjust anything in the panels, and/or type director's notes and press *Interpret notes*. Notes are turned into parameter changes (shown as a diff) that you can still edit.
4. **Plan**: draws the timeline: beats (grey), downbeats (red), one block per shot coloured by photo, cream blocks for intertitles, flash markers, hatched strobe shots. Stats show shot count, shot lengths and the maximum cut offset from the grid (always 0 ms).
5. **Render**: *Preview 540p* for a quick check, *Render full* for the final file. Progress streams live; the result plays inline and has a download link.

Jobs live in `data/jobs/<id>/`; the URL carries `?job=<id>` so you can reload the page and keep your uploads.

## Controls (all exposed in the UI)

| Panel | Controls |
|---|---|
| Rhythm | intro card text and hold length; with *accelerate* pacing the song itself speeds up over the video ("song speeds up to ×" , default 1.25, 1 = off) so the acceleration is audible as well as visual; the beat grid follows the measured stretched audio so cuts stay exact; picture speed: cut every bar / beat / half beat / quarter beat; fit to photos (song trimmed with a fade when photos run out) or fit to song (photos repeated or held);  pacing constant / accelerate / decelerate / follow energy / pulse; acceleration curve (gentle → brutal); minimum shot length; start offset; end on a downbeat |
| Structure | order as uploaded / shuffle / reverse / A-B collision (alternate halves) / dark→light; repeat photos; motif (return to photo 1 every N bars); hold first/last shot; seed |
| Effects | punch zoom on cut; Ken Burns drift; white/black flashes (hard beats only: the hardest-hitting onsets and biggest energy jumps, with a threshold slider for how selective; downbeats; every cut; climax only); flash length; strobe finale and its length; shake on accents; black & white; contrast crush; film grain; vignette; invert on strongest downbeats; dissolve length (0 = hard cuts) |
| Intertitles | on/off; cards (one per line); placement opening / closing / both / between sections / every N bars; hold length; constructivist or plain style |
| Output | frame 9:16 vertical / 16:9 / 4:5 / 1:1; song speed × 0.5–3 (time-stretched, or pitched up with *keep pitch* off; the beats are re-detected on the sped-up track so cuts stay exact); MP4 or MOV; H.264 or ProRes 422 (MOV); 24/25/30 fps; H.264 CRF; audio fade-out; skip song intro |

Presets: `clean_beat` (default: no effects, one photo per beat, hard cuts, colour), `soviet_montage` (same, but the song is sped up 1.5× for a quicker pace; your photo order is kept, no cards), `eisenstein_collision`, `vertov_kino_eye`, `kuleshov_build`, `odessa_steps`, `intertitle_heavy`, `custom`.

## How the beat sync works

`audio.py` runs librosa beat tracking at 5.8 ms resolution, snaps each beat to the local onset peak, and normalises double-time or half-time detections into a 70–150 BPM felt pulse (a 170 BPM reading becomes an 85 BPM beat with the dropped pulses kept as half-beats). `planner.py` builds a quarter-beat tick grid and only ever starts a shot on a tick, so every cut is on a beat (or half-beat) by construction. `render.py` synthesises each frame in Python and pipes raw frames into the bundled ffmpeg, choosing the frame nearest each cut time, so the cut in the encoded file is within half a frame (≤ 17 ms at 30 fps) of the beat.

`scripts/verify_alignment.py <audio> <photo_dir>` renders a clean version and measures the real cut positions in the encoded file against the beat grid.

## Tests

```bash
uv run pytest
```

Covers beat detection on a synthetic click track, planner invariants (cuts on grid, photo coverage, acceleration), effects, MP4/MOV/ProRes rendering, and the keyword director fallback.

## Layout

```
nullmontage/audio.py      decode + beat/energy analysis → BeatGrid
nullmontage/presets.py    MontageParams schema, style presets, keyword rules
nullmontage/director.py   notes → params (Claude structured output, keyword fallback)
nullmontage/planner.py    params + grid + photos → EDL (shots on the tick grid, flashes, cards)
nullmontage/effects.py    per-frame image ops, intertitle cards
nullmontage/render.py     EDL → frames → ffmpeg (libx264 / prores_ks)
nullmontage/server.py     FastAPI routes + SSE progress
static/                   the UI (vanilla HTML/JS)
```
