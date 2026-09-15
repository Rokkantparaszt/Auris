"""Offline speech-to-text for voice-cloning transcripts (Whisper via Transformers).

The model is downloaded once into the Hugging Face cache, then loaded lazily
for a transcription and released again after a few idle minutes so it never
competes with the speech engine for video memory during long generations.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from core.voice_sample import VoiceSampleError

log = logging.getLogger(__name__)

MODEL_ID = "openai/whisper-large-v3-turbo"
MODEL_SIZE_GB = 1.6
MODEL_FILES = [
    "config.json", "generation_config.json", "preprocessor_config.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
    "normalizer.json", "added_tokens.json", "special_tokens_map.json",
    "model.safetensors",
]
EXPECTED_BYTES = 1_620_000_000
SAMPLE_RATE = 16000
IDLE_UNLOAD_SEC = 300
GPU_MIN_FREE_BYTES = int(2.2 * 1024**3)

_lock = threading.Lock()
_pipeline = None
_device = None
_unload_timer: threading.Timer | None = None
_download = {"state": "idle", "message": "", "error": ""}


# ── Model files ──────────────────────────────────────────────────────────────

def model_present() -> bool:
    from huggingface_hub import try_to_load_from_cache

    # Every file must be there: a partial snapshot cannot be loaded offline.
    return all(isinstance(try_to_load_from_cache(MODEL_ID, name), str) for name in MODEL_FILES)


def _cache_bytes() -> int:
    from huggingface_hub import constants

    folder = Path(constants.HF_HUB_CACHE) / ("models--" + MODEL_ID.replace("/", "--"))
    if not folder.exists():
        return 0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def status() -> dict:
    present = model_present()
    if present and _download["state"] != "downloading":
        state = "ready"
    elif not present and _download["state"] == "ready":
        state = "idle"  # The cache was removed after an earlier download.
    else:
        state = _download["state"]
    percent = 100 if present else min(99, int(_cache_bytes() * 100 / EXPECTED_BYTES))
    return {
        "model_present": present,
        "state": state,
        "percent": percent,
        "error": "" if present else _download["error"],
        "size_gb": MODEL_SIZE_GB,
        "loaded_on": _device,
    }


DOWNLOAD_ATTEMPTS = 4


def _download_worker() -> None:
    from huggingface_hub import snapshot_download

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            # Finished files are kept, so each retry only fetches what is missing.
            snapshot_download(MODEL_ID, allow_patterns=MODEL_FILES)
            if model_present():
                _download.update(state="ready", error="")
                log.info("Whisper model downloaded: %s", MODEL_ID)
                return
            raise RuntimeError("snapshot incomplete after download")
        except Exception as error:  # network resets, disk space, mirror problems
            log.warning("Whisper download attempt %d/%d failed: %s", attempt, DOWNLOAD_ATTEMPTS, error)
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(3 * attempt)
    _download.update(
        state="error",
        error="A letöltés nem sikerült. Ellenőrizd az internetkapcsolatot és a szabad helyet, majd próbáld újra – a már letöltött részek megmaradnak.",
    )


def start_download() -> dict:
    with _lock:
        if not model_present() and _download["state"] != "downloading":
            _download.update(state="downloading", error="")
            threading.Thread(target=_download_worker, name="whisper-download", daemon=True).start()
    return status()


# ── Inference ────────────────────────────────────────────────────────────────

def _pick_device():
    import torch

    if torch.cuda.is_available():
        try:
            free, _total = torch.cuda.mem_get_info()
            if free >= GPU_MIN_FREE_BYTES:
                return "cuda:0", torch.float16
        except Exception:
            pass
    return "cpu", torch.float32


def _load_pipeline():
    global _pipeline, _device
    if _pipeline is not None:
        return _pipeline
    from huggingface_hub import snapshot_download
    from transformers import pipeline

    local_dir = snapshot_download(MODEL_ID, allow_patterns=MODEL_FILES, local_files_only=True)
    device, dtype = _pick_device()
    started = time.monotonic()
    _pipeline = pipeline(
        "automatic-speech-recognition", model=local_dir, dtype=dtype, device=device,
    )
    _device = device
    log.info("Whisper loaded on %s in %.1fs", device, time.monotonic() - started)
    return _pipeline


def unload() -> None:
    global _pipeline, _device
    with _lock:
        if _pipeline is None:
            return
        _pipeline = None
        _device = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        log.info("Whisper unloaded after idle period")


def _schedule_unload() -> None:
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(IDLE_UNLOAD_SEC, unload)
    _unload_timer.daemon = True
    _unload_timer.start()


def _load_audio(path: Path) -> np.ndarray:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        import torch
        import torchaudio.functional as audio_functional

        mono = audio_functional.resample(torch.from_numpy(mono), rate, SAMPLE_RATE).numpy()
    return np.ascontiguousarray(mono, dtype=np.float32)


def transcribe(path: Path, language: str = "hu") -> dict:
    """Transcribe a short prepared clip; the model must already be downloaded."""
    if not model_present():
        raise VoiceSampleError(
            f"Az automatikus átirathoz előbb le kell tölteni a beszédfelismerőt (egyszer, kb. {MODEL_SIZE_GB:.1f} GB)."
        )
    audio = _load_audio(Path(path))
    with _lock:
        try:
            recognizer = _load_pipeline()
            result = recognizer(
                {"raw": audio, "sampling_rate": SAMPLE_RATE},
                generate_kwargs={"language": language, "task": "transcribe"},
            )
        except VoiceSampleError:
            raise
        except Exception as error:
            log.exception("Whisper transcription failed")
            raise VoiceSampleError(
                "Az automatikus átirat most nem sikerült. Írd be kézzel, mi hangzik el a felvételen."
            ) from error
        finally:
            _schedule_unload()
    text = " ".join(str(result.get("text") or "").split())
    if not text:
        raise VoiceSampleError("A felvételen nem ismerhető fel beszéd. Írd be kézzel, mi hangzik el.")
    return {"text": text, "language_code": language, "device": _device}
