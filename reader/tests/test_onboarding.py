"""Getting-started guide, demo book and book-independent voice library."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from core import database, enrichment, experience, onboarding
from core import settings as app_settings


class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (
            database.DB_PATH, app_settings.SETTINGS_FILE,
            app_module.UPLOAD_DIR, app_module._startup_complete,
        )
        database.DB_PATH = os.path.join(self.tmp.name, "reader.db")
        app_settings.SETTINGS_FILE = Path(self.tmp.name) / "settings.json"
        app_module.UPLOAD_DIR = self.tmp.name
        app_module._startup_complete = True
        database.init_db()
        experience.initialize()
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()
        self.not_loaded = patch.object(
            app_module.tts, "status", return_value={"state": "not_loaded", "model_exists": False}
        )
        self.not_loaded.start()

    def tearDown(self):
        self.not_loaded.stop()
        (database.DB_PATH, app_settings.SETTINGS_FILE,
         app_module.UPLOAD_DIR, app_module._startup_complete) = self.original
        self.tmp.cleanup()

    def _step(self, status, step_id):
        return next(s for s in status["steps"] if s["id"] == step_id)

    def test_empty_library_points_to_engine_setup_first(self):
        with patch.object(onboarding, "model_files_present", return_value=False):
            status = self.client.get("/api/guide/status").get_json()
        self.assertEqual(status["next_step"], "engine")
        self.assertEqual(status["completed_count"], 0)
        self.assertEqual(status["total_count"], 5)
        self.assertTrue(self._step(status, "characters")["optional"])
        self.assertFalse(status["engine"]["model_present"])

    def test_demo_book_fixture_speakers_match_parser_turns(self):
        data = json.loads(onboarding.DEMO_BOOK_PATH.read_text(encoding="utf-8"))
        names = {c["name"] for c in data["characters"]}
        for chapter in data["chapters"]:
            turns = {
                u["turn_index"] for u in enrichment.build_speaker_units(chapter["content"])
                if u["dialogue_candidate"]
            }
            self.assertEqual(len(turns), len(chapter["speakers"]), chapter["title"])
            self.assertTrue(set(chapter["speakers"]) <= names)

    def test_demo_book_plays_with_character_voices_without_language_model(self):
        created = self.client.post("/api/guide/demo-book").get_json()
        self.assertTrue(created["created"])
        again = self.client.post("/api/guide/demo-book").get_json()
        self.assertEqual(again, {"book_id": created["book_id"], "created": False})

        book_id = created["book_id"]
        with database.get_conn() as conn:
            book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
            chapter_id = conn.execute(
                "SELECT id FROM chapters WHERE book_id=? ORDER BY order_num", (book_id,)
            ).fetchone()["id"]
        self.assertTrue(Path(book["file_path"]).is_file())
        self.assertEqual(book["is_sample"], 1)
        self.assertEqual(book["single_narrator_mode"], 0)

        segments = app_module._compute_segments_for_chapter(book_id, chapter_id)
        speakers = [s["character_name"] for s in segments if s["character_name"]]
        self.assertIn("Nagyapa", speakers)
        self.assertIn("Panni", speakers)
        self.assertTrue(any(s["text"].startswith("Esett az eső") and not s["character_name"] for s in segments))

        status = self.client.get("/api/guide/status").get_json()
        self.assertTrue(self._step(status, "book")["done"])
        self.assertTrue(self._step(status, "characters")["done"])
        self.assertEqual(status["demo_book_id"], book_id)

    def test_listen_event_and_saved_voice_complete_their_steps(self):
        status = self.client.post("/api/guide/state", json={"event": "listened"}).get_json()
        self.assertTrue(self._step(status, "listen")["done"])
        self.assertEqual(self.client.post("/api/guide/state", json={"event": "nope"}).status_code, 400)

        first = self.client.post("/api/voices", json={"builtin_id": "elderly-male"}).get_json()
        second = self.client.post("/api/voices", json={"builtin_id": "elderly-male"}).get_json()
        self.assertEqual(first["name"], "Idős férfi")
        self.assertEqual(second["name"], "Idős férfi (2)")
        self.assertEqual(first["instruct"], "male, elderly, low pitch")

        status = self.client.post("/api/guide/state", json={"dismissed": True}).get_json()
        self.assertTrue(self._step(status, "voices")["done"])
        self.assertTrue(status["dismissed"])

    def test_opening_a_book_voice_page_completes_optional_character_step(self):
        with database.get_conn() as conn:
            book_id = conn.execute(
                "INSERT INTO books(title,author,file_path,file_type,single_narrator_mode) "
                "VALUES('Saját','x','own.txt','txt',1)"
            ).lastrowid
        status = self.client.get("/api/guide/status").get_json()
        self.assertFalse(self._step(status, "characters")["done"])

        with patch.object(app_module.tts, "load_async"):
            page = self.client.get(f"/voice-studio/{book_id}")
        self.assertEqual(page.status_code, 200)
        status = self.client.get("/api/guide/status").get_json()
        self.assertTrue(self._step(status, "characters")["done"])
        self.assertIsNone(status["multi_book_id"])  # completed by visiting, not by a multi-voice book
        # Optional steps never block "all required steps done".
        self.assertEqual(status["total_count"], 5)

    def test_voice_creation_validates_plain_language_errors(self):
        self.assertEqual(self.client.post("/api/voices", json={"name": "X"}).status_code, 400)
        missing_text = self.client.post(
            "/api/voices",
            data={"name": "Anya", "file": (io.BytesIO(b"RIFF"), "anya.wav")},
            content_type="multipart/form-data",
        )
        self.assertEqual(missing_text.status_code, 400)
        self.assertIn("mi hangzik el", missing_text.get_json()["error"])

        wrong_type = self.client.post(
            "/api/voices",
            data={"name": "Anya", "ref_text": "Szia.", "file": (io.BytesIO(b"ID3"), "anya.mp3")},
            content_type="multipart/form-data",
        )
        self.assertEqual(wrong_type.status_code, 400)

        wav = io.BytesIO()
        import numpy as np
        import soundfile as sf
        sf.write(wav, np.zeros(24000 * 4), 24000, format="WAV")
        wav.seek(0)
        cloned = self.client.post(
            "/api/voices",
            data={"name": "Anya", "ref_text": "Szia.", "file": (wav, "anya.wav")},
            content_type="multipart/form-data",
        ).get_json()
        self.assertTrue(Path(cloned["ref_audio_path"]).is_file())
        self.assertEqual(cloned["ref_text"], "Szia.")

    def test_voice_assign_sets_narrator_and_character_voice(self):
        book_id = self.client.post("/api/guide/demo-book").get_json()["book_id"]
        with database.get_conn() as conn:
            conn.execute(
                "UPDATE books SET narrator_ref_audio_path='x.wav', narrator_ref_text='t' WHERE id=?",
                (book_id,),
            )
            panni = conn.execute(
                "SELECT id FROM characters WHERE book_id=? AND name='Panni'", (book_id,)
            ).fetchone()["id"]

        ok = self.client.post(f"/api/books/{book_id}/voice-assign", json={"builtin_id": "narrator-male"})
        self.assertEqual(ok.status_code, 200)
        ok = self.client.post(
            f"/api/books/{book_id}/voice-assign", json={"builtin_id": "child-female", "char_id": panni}
        )
        self.assertEqual(ok.status_code, 200)
        with database.get_conn() as conn:
            book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
            character = conn.execute("SELECT * FROM characters WHERE id=?", (panni,)).fetchone()
        self.assertEqual(book["narrator_instruct"], "male, middle-aged, low pitch")
        self.assertIsNone(book["narrator_ref_audio_path"])
        self.assertEqual(character["instruct"], "female, child, high pitch")

        bad = self.client.post(f"/api/books/{book_id}/voice-assign", json={"builtin_id": "missing"})
        self.assertEqual(bad.status_code, 400)

    def test_preview_explains_missing_engine_instead_of_model_not_ready(self):
        with patch.object(onboarding, "model_files_present", return_value=False):
            response = self.client.post("/api/voices/preview", json={"builtin_id": "narrator-female"})
        self.assertEqual(response.status_code, 503)
        body = response.get_json()
        self.assertTrue(body["setup_needed"])
        self.assertIn("Gyors beállítás", body["error"])

    def test_cleanup_removes_only_placeholder_sample_rows(self):
        demo_id = self.client.post("/api/guide/demo-book").get_json()["book_id"]
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO books(title,author,file_path,file_type,is_sample) "
                "VALUES('Régi minta','x','sample://Régi minta','epub',1)"
            )
            conn.execute(
                "INSERT INTO books(title,author,file_path,file_type) VALUES('Saját','y','own.txt','txt')"
            )
            conn.execute("INSERT INTO voice_profiles(name,instruct,is_sample) VALUES('Minta','x',1)")
            conn.execute("INSERT INTO voice_profiles(name,instruct) VALUES('Enyém','male')")

        self.assertEqual(onboarding.cleanup_legacy_samples(), 2)
        with database.get_conn() as conn:
            titles = {r["title"] for r in conn.execute("SELECT title FROM books")}
            profiles = {r["name"] for r in conn.execute("SELECT name FROM voice_profiles")}
            demo = conn.execute("SELECT id FROM books WHERE id=?", (demo_id,)).fetchone()
        self.assertEqual(titles, {"A kék esernyő", "Saját"})
        self.assertEqual(profiles, {"Enyém"})
        self.assertIsNotNone(demo)

    def test_voices_page_and_navigation_render(self):
        page = self.client.get("/voices").get_data(as_text=True)
        self.assertIn("Beépített hangok", page)
        self.assertIn('id="nav-guide"', page)
        library = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="guide-home"', library)
        builtins = self.client.get("/api/voices/builtin").get_json()
        self.assertEqual(len(builtins), len(onboarding.BUILTIN_VOICES))


if __name__ == "__main__":
    unittest.main()
