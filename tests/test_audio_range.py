"""The chosen part of the song (audio_start_s / audio_end_s) is what gets decoded for analysis and mux."""
import subprocess
import wave

import numpy as np

from nullmontage import audio as audio_mod
from nullmontage.audio import _seek_args, decode_for_mux, decode_to_wav
from nullmontage.ffmpeg_bin import get_ffmpeg


def _wav_seconds(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def test_seek_args():
    assert _seek_args(0, 0) == []
    assert _seek_args(12.5, 0) == ["-ss", "12.500"]
    assert _seek_args(10, 25) == ["-ss", "10.000", "-t", "15.000"]
    assert _seek_args(30, 20) == ["-ss", "30.000"]  # end before start = to the end


def test_decode_uses_only_the_chosen_part(tmp_path):
    src = tmp_path / "tone.wav"
    subprocess.run([get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                    "-ac", "1", "-ar", "22050", str(src)], check=True)
    assert abs(_wav_seconds(decode_to_wav(src, tmp_path / "a.wav", 5, end_s=12)) - 7) < 0.05
    assert abs(_wav_seconds(decode_to_wav(src, tmp_path / "b.wav", 5)) - 15) < 0.05
    assert abs(_wav_seconds(decode_for_mux(src, tmp_path / "c.wav", 2, end_s=6)) - 4) < 0.05
