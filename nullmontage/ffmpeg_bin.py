"""Locate the ffmpeg binary (bundled via imageio-ffmpeg, overridable with FFMPEG_BIN)."""
from __future__ import annotations

import os
import shutil
from functools import lru_cache


@lru_cache(maxsize=1)
def get_ffmpeg() -> str:
    override = os.environ.get("FFMPEG_BIN")
    if override:
        return override
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - fallback path
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise RuntimeError("ffmpeg not found: install imageio-ffmpeg or set FFMPEG_BIN")
