"""Reference clips for voice cloning: length detection, conversion and cropping.

Uploaded WAV, MP3, M4A or OGG recordings become a mono 16-bit WAV of at most
``MAX_SECONDS``. Leading silence is dropped and the clip ends in a natural
pause (between sentences or words) instead of mid-word, found with a short-time
loudness analysis. FFmpeg does the decoding when it is installed; otherwise
soundfile handles WAV/OGG/MP3 (M4A needs FFmpeg). Transcripts are made offline
by ``core.local_stt``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

MAX_SECONDS = 10.0
MIN_SECONDS = 3.0
SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg"}

# Only the start of a recording matters; long leading silence still fits.
DECODE_SECONDS = 30.0
FRAME_SEC = 0.025
HOP_SEC = 0.010
LEAD_KEEP_SEC = 0.10          # silence kept before the first word
TAIL_KEEP_SEC = 0.15          # silence kept after the last word
SENTENCE_PAUSE_SEC = 0.25     # preferred cut: a pause this long
WORD_PAUSE_SEC = 0.06         # acceptable cut: a gap between words
SENTENCE_SEARCH_SEC = 3.0     # prefer sentence pauses in the last seconds
MIN_CLIP_SEC = 5.0            # never cut earlier than this for a pause
DIP_SEARCH_SEC = 2.5          # fallback: quietest moment in the last seconds
DIP_MIN_DB = 12.0             # ... only if clearly quieter than speech
FADE_IN_SEC = 0.01
FADE_OUT_SEC = 0.02


class VoiceSampleError(ValueError):
    """A problem the user can fix; the message is shown as-is."""


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def probe_duration(path: Path) -> float | None:
    """Length of the original recording in seconds, or None when unknown."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = _run([
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ], timeout=30)
            if result.returncode == 0 and result.stdout.strip() not in ("", "N/A"):
                return float(result.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    try:
        return float(sf.info(str(path)).duration)
    except Exception:
        return None


# ── Decoding ─────────────────────────────────────────────────────────────────

def _decode_with_ffmpeg(ffmpeg: str, source: Path) -> tuple[np.ndarray, int]:
    with tempfile.TemporaryDirectory(prefix="auris-ref-") as tmp:
        wav = Path(tmp) / "head.wav"
        result = _run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-t", str(DECODE_SECONDS), "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
        ])
        if result.returncode != 0 or not wav.is_file():
            raise VoiceSampleError(
                "A felvételt nem sikerült beolvasni. Ellenőrizd, hogy valódi hangfájl-e "
                "(WAV, MP3, M4A vagy OGG)."
            )
        samples, rate = sf.read(str(wav), dtype="float32")
    return samples, rate


def _decode_with_soundfile(source: Path) -> tuple[np.ndarray, int]:
    try:
        info = sf.info(str(source))
        frames = min(info.frames, int(DECODE_SECONDS * info.samplerate))
        data, rate = sf.read(str(source), frames=frames, dtype="float32", always_2d=True)
    except Exception as error:
        raise VoiceSampleError(
            "A felvételt nem sikerült beolvasni. Telepítsd az FFmpeg programot, "
            "vagy használj WAV fájlt."
        ) from error
    return data.mean(axis=1), rate


# ── Pause detection ──────────────────────────────────────────────────────────

@dataclass
class Cut:
    start: float        # seconds into the recording
    end: float
    at_pause: bool      # ends in a detected pause rather than a hard cut


def _frame_levels(samples: np.ndarray, rate: int) -> tuple[np.ndarray, float]:
    """Loudness in dBFS per hop; returns (levels, hop seconds)."""
    win, hop = max(1, int(rate * FRAME_SEC)), max(1, int(rate * HOP_SEC))
    if len(samples) < win:
        return np.array([-120.0]), hop / rate
    squares = np.concatenate(([0.0], np.cumsum(samples.astype(np.float64) ** 2)))
    starts = np.arange(0, len(samples) - win + 1, hop)
    energy = (squares[starts + win] - squares[starts]) / win
    return 10 * np.log10(np.maximum(energy, 1e-12)), hop / rate


def _silence_runs(silent: np.ndarray, hop: float) -> list[tuple[float, float]]:
    """(start, end) seconds of silent stretches; blips under 30 ms are bridged."""
    runs: list[list[float]] = []
    index = 0
    while index < len(silent):
        if not silent[index]:
            index += 1
            continue
        begin = index
        while index < len(silent) and silent[index]:
            index += 1
        start, end = begin * hop, index * hop + FRAME_SEC
        if runs and start - runs[-1][1] <= 0.03:
            runs[-1][1] = end
        else:
            runs.append([start, end])
    return [(a, b) for a, b in runs]


def choose_cut(samples: np.ndarray, rate: int, max_seconds: float = MAX_SECONDS) -> Cut:
    """Pick where the reference clip should start and end."""
    total = len(samples) / rate
    levels, hop = _frame_levels(samples, rate)
    floor, speech = np.percentile(levels, 10), np.percentile(levels, 90)
    if speech - floor < 10:  # no real dynamics: steady tone, noise or silence
        end = min(total, max_seconds)
        return Cut(0.0, end, at_pause=total <= max_seconds)
    threshold = floor + 0.25 * (speech - floor)
    silent = levels < threshold
    runs = _silence_runs(silent, hop)

    voiced = np.flatnonzero(~silent)
    first_voice = voiced[0] * hop if len(voiced) else 0.0
    start = max(0.0, first_voice - LEAD_KEEP_SEC)
    window_end = start + max_seconds

    if total <= window_end:
        last_voice = voiced[-1] * hop + FRAME_SEC if len(voiced) else total
        return Cut(start, min(total, last_voice + TAIL_KEEP_SEC), at_pause=True)

    earliest = start + MIN_CLIP_SEC
    usable = [(a, b) for a, b in runs if earliest <= a < window_end - 0.02]

    def cut_after(pause_start: float) -> float:
        return min(pause_start + TAIL_KEEP_SEC, window_end)

    sentence = [
        (a, b) for a, b in usable
        if b - a >= SENTENCE_PAUSE_SEC and a >= window_end - SENTENCE_SEARCH_SEC
    ]
    if sentence:
        return Cut(start, cut_after(sentence[-1][0]), at_pause=True)
    words = [(a, b) for a, b in usable if b - a >= WORD_PAUSE_SEC]
    if words:
        return Cut(start, cut_after(words[-1][0]), at_pause=True)

    first, last = int((window_end - DIP_SEARCH_SEC) / hop), int(window_end / hop) - 1
    if last > first:
        quietest = first + int(np.argmin(levels[first:last]))
        if speech - levels[quietest] >= DIP_MIN_DB:
            return Cut(start, quietest * hop + FRAME_SEC / 2, at_pause=True)
    return Cut(start, window_end, at_pause=False)


def _render(samples: np.ndarray, rate: int, cut: Cut) -> np.ndarray:
    clip = samples[int(cut.start * rate):int(cut.end * rate)].astype(np.float32, copy=True)
    fade_in, fade_out = min(len(clip), int(FADE_IN_SEC * rate)), min(len(clip), int(FADE_OUT_SEC * rate))
    if fade_in:
        clip[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
    if fade_out:
        clip[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
    return clip


def prepare_reference(source: Path, target: Path) -> dict:
    """Write a trimmed mono WAV to ``target`` and describe what happened."""
    source, target = Path(source), Path(target)
    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise VoiceSampleError("WAV, MP3, M4A vagy OGG felvételt válassz.")

    original = probe_duration(source)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        samples, rate = _decode_with_ffmpeg(ffmpeg, source)
    elif extension == ".m4a":
        raise VoiceSampleError(
            "Az M4A felvételhez az ingyenes FFmpeg program kell. Telepítsd, "
            "vagy mentsd a felvételt WAV, MP3 vagy OGG formátumba."
        )
    else:
        samples, rate = _decode_with_soundfile(source)

    decoded = len(samples) / rate
    if original is None:
        original = decoded
    if decoded < 0.5:
        raise VoiceSampleError("A felvétel üres vagy túl rövid.")

    cut = choose_cut(samples, rate)
    clip = _render(samples, rate, cut)
    duration = len(clip) / rate
    if duration < 0.5:
        raise VoiceSampleError("A felvételen nem található beszéd.")
    sf.write(str(target), clip, rate, subtype="PCM_16")
    return {
        "duration": round(float(duration), 2),
        "original_duration": round(float(original), 2),
        "start": round(float(cut.start), 2),
        "end": round(float(cut.end), 2),
        "cropped": bool(original > MAX_SECONDS + 0.05),
        "cut_at_pause": bool(cut.at_pause),
        "too_short": bool(duration < MIN_SECONDS),
    }
