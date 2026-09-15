"""Voice-cloning reference clips: length detection, cropping and transcription."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import app as app_module
from core import database, experience, local_stt, voice_sample
from core import settings as app_settings

FFMPEG = shutil.which("ffmpeg")


def tone(path: Path, seconds: float, rate: int = 24000, channels: int = 1) -> Path:
    samples = np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(rate * seconds))) * 0.3
    data = np.column_stack([samples] * channels) if channels > 1 else samples
    sf.write(str(path), data, rate)
    return path


def speech_like(path: Path, layout: list[tuple[str, float]], rate: int = 24000) -> tuple[Path, list[tuple[float, float]]]:
    """Word-like bursts over a quiet noise floor.

    ``layout`` items are ("word", seconds) or ("pause", seconds); returns the
    file and the (start, end) time of every word.
    """
    rng = np.random.default_rng(7)
    pieces, words, cursor = [], [], 0.0
    for kind, seconds in layout:
        count = int(rate * seconds)
        if kind == "word":
            t = np.arange(count) / rate
            envelope = np.hanning(count) ** 0.3
            pieces.append(0.4 * envelope * np.sin(2 * np.pi * (180 + 40 * np.sin(6 * t)) * t))
            words.append((cursor, cursor + seconds))
        else:
            pieces.append(np.zeros(count))
        cursor += seconds
    audio = np.concatenate(pieces)
    audio += rng.normal(0, 0.0005, len(audio))  # about -66 dBFS room noise
    sf.write(str(path), audio.astype(np.float32), rate)
    return path, words


def inside_word(moment: float, words: list[tuple[float, float]], margin: float = 0.02) -> bool:
    return any(start + margin < moment < end - margin for start, end in words)


def encode(source: Path, target: Path) -> Path:
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(source), str(target)], check=True)
    return target


class PrepareReferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_long_wav_is_cropped_to_first_ten_seconds_mono(self):
        source = tone(self.dir / "long.wav", 14.0, channels=2)
        info = voice_sample.prepare_reference(source, self.dir / "out.wav")
        self.assertTrue(info["cropped"])
        self.assertAlmostEqual(info["original_duration"], 14.0, delta=0.1)
        self.assertAlmostEqual(info["duration"], 10.0, delta=0.05)
        self.assertFalse(info["cut_at_pause"])  # a steady tone has no pauses
        self.assertEqual(sf.info(str(self.dir / "out.wav")).channels, 1)

    def test_cut_prefers_sentence_pause_and_drops_leading_silence(self):
        layout = [("pause", 1.2)]
        for _sentence in range(7):
            layout += [("word", 0.45), ("pause", 0.08), ("word", 0.6), ("pause", 0.08), ("word", 0.5), ("pause", 0.5)]
        source, words = speech_like(self.dir / "speech.wav", layout)
        info = voice_sample.prepare_reference(source, self.dir / "out.wav")

        self.assertTrue(info["cropped"])
        self.assertTrue(info["cut_at_pause"])
        self.assertAlmostEqual(info["start"], 1.1, delta=0.03)  # 0.1 s kept before the first word
        self.assertLessEqual(info["duration"], 10.0)
        self.assertGreaterEqual(info["duration"], 7.0)
        self.assertFalse(inside_word(info["end"], words))
        sentence_ends = [end for (start, end), nxt in zip(words, words[1:]) if nxt[0] - end >= 0.4]
        self.assertTrue(any(0 <= info["end"] - end <= 0.2 for end in sentence_ends), info)

    def test_cut_falls_back_to_gap_between_words(self):
        layout = [("word", 0.4), ("pause", 0.1)] * 30  # steady speech, no sentence breaks
        source, words = speech_like(self.dir / "words.wav", layout)
        info = voice_sample.prepare_reference(source, self.dir / "out.wav")
        self.assertTrue(info["cut_at_pause"])
        self.assertGreater(info["duration"], 9.0)
        self.assertLessEqual(info["duration"], 10.0)
        self.assertFalse(inside_word(info["end"], words))
        clip, rate = sf.read(str(self.dir / "out.wav"), dtype="float32")
        self.assertLess(np.abs(clip[-int(rate * 0.005):]).max(), 0.01)  # faded, no click

    def test_short_recording_loses_only_surrounding_silence(self):
        source, words = speech_like(
            self.dir / "short.wav",
            [("pause", 0.8), ("word", 0.5), ("pause", 0.1), ("word", 0.7), ("word", 0.6), ("pause", 1.5)],
        )
        info = voice_sample.prepare_reference(source, self.dir / "out.wav")
        self.assertFalse(info["cropped"])
        self.assertAlmostEqual(info["start"], 0.7, delta=0.03)
        self.assertAlmostEqual(info["end"], words[-1][1] + voice_sample.TAIL_KEEP_SEC, delta=0.05)

    def test_short_ogg_is_kept_and_flagged_too_short(self):
        wav = tone(self.dir / "short.wav", 2.0)
        ogg = self.dir / "short.ogg"
        data, rate = sf.read(str(wav))
        sf.write(str(ogg), data, rate, format="OGG", subtype="VORBIS")
        info = voice_sample.prepare_reference(ogg, self.dir / "out.wav")
        self.assertFalse(info["cropped"])
        self.assertTrue(info["too_short"])
        self.assertAlmostEqual(info["duration"], 2.0, delta=0.1)

    def test_soundfile_fallback_without_ffmpeg(self):
        source = tone(self.dir / "long.wav", 12.0)
        with patch.object(voice_sample.shutil, "which", return_value=None):
            info = voice_sample.prepare_reference(source, self.dir / "out.wav")
            self.assertAlmostEqual(info["duration"], 10.0, delta=0.05)
            self.assertTrue(info["cropped"])
            with self.assertRaisesRegex(voice_sample.VoiceSampleError, "FFmpeg"):
                voice_sample.prepare_reference(self.dir / "x.m4a", self.dir / "o.wav")

    @unittest.skipUnless(FFMPEG, "ffmpeg not installed")
    def test_mp3_and_m4a_are_converted_and_cropped(self):
        wav = tone(self.dir / "base.wav", 12.0)
        for extension in (".mp3", ".m4a"):
            with self.subTest(extension=extension):
                source = encode(wav, self.dir / f"voice{extension}")
                target = self.dir / f"out{extension}.wav"
                info = voice_sample.prepare_reference(source, target)
                self.assertTrue(info["cropped"])
                self.assertAlmostEqual(info["duration"], 10.0, delta=0.1)
                self.assertEqual(sf.info(str(target)).format, "WAV")

    def test_unsupported_and_broken_files_give_plain_errors(self):
        with self.assertRaisesRegex(voice_sample.VoiceSampleError, "WAV, MP3, M4A vagy OGG"):
            voice_sample.prepare_reference(self.dir / "a.flac", self.dir / "o.wav")
        broken = self.dir / "broken.mp3"
        broken.write_bytes(b"not audio")
        with self.assertRaises(voice_sample.VoiceSampleError):
            voice_sample.prepare_reference(broken, self.dir / "o.wav")


class LocalSttTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clip = tone(Path(self.tmp.name) / "clip.wav", 1.0, rate=24000)

    def tearDown(self):
        local_stt.unload()
        self.tmp.cleanup()

    def test_transcribe_resamples_to_16k_and_requests_hungarian(self):
        calls = []

        def fake_pipeline(inputs, generate_kwargs):
            calls.append((inputs, generate_kwargs))
            return {"text": "  Szia,   itt vagyok. "}

        with patch.object(local_stt, "model_present", return_value=True), \
                patch.object(local_stt, "_load_pipeline", return_value=fake_pipeline), \
                patch.object(local_stt, "_schedule_unload") as unload:
            result = local_stt.transcribe(self.clip)
        self.assertEqual(result["text"], "Szia, itt vagyok.")
        inputs, kwargs = calls[0]
        self.assertEqual(inputs["sampling_rate"], 16000)
        self.assertEqual(inputs["raw"].dtype, np.float32)
        self.assertAlmostEqual(len(inputs["raw"]) / 16000, 1.0, delta=0.01)
        self.assertEqual(kwargs, {"language": "hu", "task": "transcribe"})
        unload.assert_called_once()

    def test_missing_model_and_failures_give_plain_messages(self):
        with patch.object(local_stt, "model_present", return_value=False):
            with self.assertRaisesRegex(voice_sample.VoiceSampleError, "le kell tölteni"):
                local_stt.transcribe(self.clip)
        with patch.object(local_stt, "model_present", return_value=True), \
                patch.object(local_stt, "_load_pipeline", side_effect=RuntimeError("CUDA OOM")), \
                patch.object(local_stt, "_schedule_unload"):
            with self.assertRaisesRegex(voice_sample.VoiceSampleError, "Írd be kézzel"):
                local_stt.transcribe(self.clip)
        with patch.object(local_stt, "model_present", return_value=True), \
                patch.object(local_stt, "_load_pipeline", return_value=lambda *a, **k: {"text": " "}), \
                patch.object(local_stt, "_schedule_unload"):
            with self.assertRaisesRegex(voice_sample.VoiceSampleError, "nem ismerhető fel"):
                local_stt.transcribe(self.clip)

    def test_download_runs_once_in_background_with_model_files_only(self):
        started = []
        with patch.object(local_stt, "model_present", return_value=False), \
                patch.object(local_stt, "_cache_bytes", return_value=810_000_000), \
                patch.object(local_stt.threading, "Thread") as thread:
            thread.return_value.start.side_effect = lambda: started.append(True)
            local_stt._download.update(state="idle", error="")
            first = local_stt.start_download()
            second = local_stt.start_download()
        self.assertEqual(first["state"], "downloading")
        self.assertEqual(first["percent"], 50)
        self.assertEqual(second["state"], "downloading")
        self.assertEqual(len(started), 1)

        with patch("huggingface_hub.snapshot_download") as snapshot, \
                patch.object(local_stt, "model_present", return_value=True):
            local_stt._download_worker()
        self.assertEqual(snapshot.call_args.args[0], "openai/whisper-large-v3-turbo")
        self.assertIn("model.safetensors", snapshot.call_args.kwargs["allow_patterns"])
        self.assertEqual(local_stt._download["state"], "ready")
        local_stt._download.update(state="idle", error="")

    def test_download_retries_connection_resets_and_incomplete_snapshots(self):
        results = iter([ConnectionResetError("10054"), None, None])
        present = iter([False, True])

        def flaky(*args, **kwargs):
            outcome = next(results)
            if isinstance(outcome, Exception):
                raise outcome

        with patch("huggingface_hub.snapshot_download", side_effect=flaky) as snapshot, \
                patch.object(local_stt, "model_present", side_effect=lambda: next(present)), \
                patch.object(local_stt.time, "sleep"):
            local_stt._download_worker()
        self.assertEqual(snapshot.call_count, 3)
        self.assertEqual(local_stt._download["state"], "ready")

        with patch("huggingface_hub.snapshot_download", side_effect=OSError("offline")), \
                patch.object(local_stt.time, "sleep"):
            local_stt._download_worker()
        self.assertEqual(local_stt._download["state"], "error")
        self.assertIn("megmaradnak", local_stt._download["error"])
        local_stt._download.update(state="idle", error="")

    def test_partial_snapshot_is_not_reported_as_present(self):
        def cached(repo, name):
            return None if name == "vocab.json" else f"C:/cache/{name}"

        with patch("huggingface_hub.try_to_load_from_cache", side_effect=cached):
            self.assertFalse(local_stt.model_present())
        with patch("huggingface_hub.try_to_load_from_cache", side_effect=lambda repo, name: f"C:/cache/{name}"):
            self.assertTrue(local_stt.model_present())


class ReferenceApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (database.DB_PATH, app_settings.SETTINGS_FILE, app_module.UPLOAD_DIR, app_module._startup_complete)
        database.DB_PATH = os.path.join(self.tmp.name, "reader.db")
        app_settings.SETTINGS_FILE = Path(self.tmp.name) / "settings.json"
        app_module.UPLOAD_DIR = self.tmp.name
        app_module._startup_complete = True
        database.init_db()
        experience.initialize()
        self.client = app_module.app.test_client()

    def tearDown(self):
        (database.DB_PATH, app_settings.SETTINGS_FILE, app_module.UPLOAD_DIR, app_module._startup_complete) = self.original
        self.tmp.cleanup()

    def _upload(self, seconds=12.0):
        source = tone(Path(self.tmp.name) / "upload.wav", seconds)
        return self.client.post(
            "/api/voices/reference",
            data={"file": (io.BytesIO(source.read_bytes()), "Anya hangja.wav")},
            content_type="multipart/form-data",
        )

    def test_prepare_listen_transcribe_and_save_with_token(self):
        with patch.object(local_stt, "model_present", return_value=False):
            response = self._upload()
            self.assertEqual(response.status_code, 200)
            info = response.get_json()
            self.assertTrue(info["cropped"])
            self.assertFalse(info["stt_available"])
            missing = self.client.post(f"/api/voices/reference/{info['token']}/transcribe")
        self.assertEqual(missing.status_code, 409)
        self.assertTrue(missing.get_json()["stt_missing_model"])

        audio = self.client.get(info["audio_url"])
        self.assertEqual(audio.status_code, 200)
        self.assertAlmostEqual(sf.info(io.BytesIO(audio.data)).duration, 10.0, delta=0.05)
        audio.close()

        with patch.object(local_stt, "model_present", return_value=True), \
                patch.object(local_stt, "transcribe", return_value={"text": "Szia!", "language_code": "hu"}) as stt:
            done = self.client.post(f"/api/voices/reference/{info['token']}/transcribe").get_json()
        self.assertEqual(done["text"], "Szia!")
        self.assertTrue(str(stt.call_args.args[0]).endswith(f"ref_{info['token']}.wav"))

        saved = self.client.post(
            "/api/voices",
            data={"name": "Anya", "ref_text": "Szia!", "reference_token": info["token"], "file_name": "Anya hangja.wav"},
            content_type="multipart/form-data",
        ).get_json()
        self.assertEqual(saved["ref_audio_name"], "Anya hangja.wav")
        self.assertAlmostEqual(sf.info(saved["ref_audio_path"]).duration, 10.0, delta=0.05)

    def test_stt_status_endpoint(self):
        with patch.object(local_stt, "model_present", return_value=True):
            status = self.client.get("/api/stt/status").get_json()
        self.assertTrue(status["model_present"])
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["size_gb"], 1.6)

    def test_invalid_token_and_unsupported_type(self):
        self.assertEqual(self.client.get("/api/voices/reference/../../app.wav").status_code, 404)
        self.assertEqual(self.client.post(f"/api/voices/reference/{'0' * 32}/transcribe").status_code, 400)
        bad = self.client.post(
            "/api/voices/reference",
            data={"file": (io.BytesIO(b"x"), "voice.flac")},
            content_type="multipart/form-data",
        )
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
