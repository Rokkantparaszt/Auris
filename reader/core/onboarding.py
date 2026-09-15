"""Getting-started guide, built-in voices and the optional demo book.

The guide never guesses from clicks: every step is derived from the real
library state (engine files present, saved voices, books, generated audio,
finished exports), so it stays correct after restarts, restores and deletes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from core import enrichment, structure
from core import settings as app_settings
from core.database import get_conn

log = logging.getLogger(__name__)

READER_DIR = Path(__file__).resolve().parent.parent
DEMO_BOOK_PATH = READER_DIR / "fixtures" / "demo_book.json"
DEFAULT_UPLOAD_DIR = READER_DIR / "uploads"
AUDIO_CACHE_DIR = READER_DIR / "audio_cache"

# Ready-made OmniVoice voice designs. They need no recording, so every user
# has usable narrator and character voices as soon as the engine is installed.
BUILTIN_VOICES: list[dict[str, str]] = [
    {
        "id": "narrator-female",
        "name": "Nyugodt mesélőnő",
        "description": "Középkorú női hang. Jó általános narrátor.",
        "gender": "female",
        "instruct": "female, middle-aged, moderate pitch",
    },
    {
        "id": "narrator-male",
        "name": "Mély hangú mesélő",
        "description": "Középkorú, mély férfihang. Jó általános narrátor.",
        "gender": "male",
        "instruct": "male, middle-aged, low pitch",
    },
    {
        "id": "elderly-male",
        "name": "Idős férfi",
        "description": "Nagyapák, bölcs öregek, tanárok.",
        "gender": "male",
        "instruct": "male, elderly, low pitch",
    },
    {
        "id": "elderly-female",
        "name": "Idős nő",
        "description": "Nagymamák, idős hölgyek.",
        "gender": "female",
        "instruct": "female, elderly, moderate pitch",
    },
    {
        "id": "young-female",
        "name": "Fiatal nő",
        "description": "Élénk, fiatal női szereplők.",
        "gender": "female",
        "instruct": "female, young adult, high pitch",
    },
    {
        "id": "young-male",
        "name": "Fiatal férfi",
        "description": "Fiatal férfi szereplők.",
        "gender": "male",
        "instruct": "male, young adult, moderate pitch",
    },
    {
        "id": "teen-female",
        "name": "Kamaszlány",
        "description": "Tizenéves lány szereplők.",
        "gender": "female",
        "instruct": "female, teenager, moderate pitch",
    },
    {
        "id": "child-female",
        "name": "Kislány",
        "description": "Gyermek szereplők. Mindig hallgasd meg előre.",
        "gender": "female",
        "instruct": "female, child, high pitch",
    },
]


def builtin_voice(voice_id: str) -> dict[str, str] | None:
    return next((v for v in BUILTIN_VOICES if v["id"] == voice_id), None)


# ── Guide state ──────────────────────────────────────────────────────────────

def _events() -> dict[str, Any]:
    events = app_settings.get("onboarding_events") or {}
    return events if isinstance(events, dict) else {}


GUIDE_EVENTS = {
    "listened",          # audio actually played in the reader
    "characters_seen",   # opened a book's voice page (optional step)
}


def record_event(name: str) -> None:
    """Remember a milestone the database cannot show."""
    if name not in GUIDE_EVENTS:
        raise ValueError("Ismeretlen esemény.")
    events = _events()
    if not events.get(name):
        app_settings.save({"onboarding_events": {**events, name: True}})


def set_dismissed(dismissed: bool) -> None:
    app_settings.save({"guide_dismissed": bool(dismissed)})


def model_files_present(config: dict | None = None) -> bool:
    """True when the selected engine can load without the user downloading anything."""
    config = config or app_settings.load()
    if (config.get("tts_engine") or "omnivoice") == "higgs":
        if (config.get("higgs_model_source") or "download") == "local":
            return os.path.isdir(str(config.get("higgs_model_path") or ""))
        return True  # Downloaded into the HuggingFace cache on first load.
    path = str(config.get("model_path") or "")
    return os.path.isfile(os.path.join(path, "config.json"))


def llm_configured(config: dict | None = None) -> bool:
    config = config or app_settings.load()
    if (config.get("llm_provider") or "local") == "openai":
        return bool(config.get("openai_api_key") and config.get("openai_model"))
    return bool(config.get("llm_base_url") and config.get("llm_model"))


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def guide_status(engine_status: dict | None = None) -> dict[str, Any]:
    config = app_settings.load()
    engine_status = engine_status or {}
    events = _events()
    with get_conn() as conn:
        books = conn.execute(
            "SELECT id, title, is_sample, single_narrator_mode, "
            "(SELECT COUNT(*) FROM characters c WHERE c.book_id=books.id) AS character_count "
            "FROM books ORDER BY COALESCE(last_read, added_at) DESC"
        ).fetchall()
        voice_count = conn.execute("SELECT COUNT(*) FROM voice_profiles").fetchone()[0]
        has_audio = bool(conn.execute(
            "SELECT 1 FROM tts_segments WHERE audio_path IS NOT NULL LIMIT 1"
        ).fetchone())
        exported = _table_exists(conn, "jobs") and bool(conn.execute(
            "SELECT 1 FROM jobs WHERE state='complete' "
            "AND type IN ('export_chapter','export_book') LIMIT 1"
        ).fetchone())

    books = [dict(b) for b in books]
    multi_book = next(
        (b for b in books if not b["single_narrator_mode"] and b["character_count"]),
        None,
    )
    demo = next((b for b in books if b["is_sample"]), None)
    recent = books[0] if books else None
    present = model_files_present(config)
    state = engine_status.get("state") or "not_loaded"

    steps = [
        {"id": "engine", "done": present and state != "error"},
        {"id": "voices", "done": voice_count > 0},
        {"id": "book", "done": bool(books)},
        {"id": "listen", "done": bool(events.get("listened") or has_audio)},
        {
            "id": "characters",
            "done": multi_book is not None or bool(events.get("characters_seen")),
            "optional": True,
        },
        {"id": "export", "done": exported},
    ]
    required = [s for s in steps if not s.get("optional")]
    next_step = next((s["id"] for s in steps if not s["done"]), None)
    return {
        "steps": steps,
        "next_step": next_step,
        "completed_count": sum(1 for s in required if s["done"]),
        "total_count": len(required),
        "all_completed": all(s["done"] for s in required),
        "dismissed": bool(config.get("guide_dismissed", False)),
        "engine": {
            "name": config.get("tts_engine") or "omnivoice",
            "state": state,
            "message": engine_status.get("message", ""),
            "model_present": present,
        },
        "llm_configured": llm_configured(config),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "voice_count": voice_count,
        "book_count": len(books),
        "recent_book_id": recent["id"] if recent else None,
        "multi_book_id": multi_book["id"] if multi_book else None,
        "demo_book_id": demo["id"] if demo else None,
    }


# ── Demo book ────────────────────────────────────────────────────────────────

def create_demo_book(upload_dir: str | os.PathLike | None = None) -> dict[str, Any]:
    """Add the short demo story with ready speaker assignments.

    It is imported like a normal TXT book (real source file, chapters, voices),
    so every feature works on it and the ordinary remove action deletes it.
    """
    data = json.loads(DEMO_BOOK_PATH.read_text(encoding="utf-8"))
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM books WHERE is_sample=1 AND title=?", (data["title"],)
        ).fetchone()
    if existing:
        return {"book_id": existing["id"], "created": False}

    text = "\n\n".join(f"{c['title']}\n\n{c['content']}" for c in data["chapters"])
    raw = text.encode("utf-8")
    folder = Path(upload_dir or DEFAULT_UPLOAD_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / f"{uuid.uuid4().hex}.txt"
    source.write_bytes(raw)

    chapters = structure.enrich_chapters([
        {"title": c["title"], "content": c["content"], "order_num": i + 1}
        for i, c in enumerate(data["chapters"])
    ])
    try:
        with get_conn() as conn:
            book_id = conn.execute(
                """INSERT INTO books(title, author, file_path, file_type, language,
                single_narrator_mode, narrator_instruct, total_chapters,
                character_analysis_status, character_analysis_provider,
                character_analysis_model, character_analysis_message,
                content_hash, description, is_sample)
                VALUES(?,?,?,?,?,0,?,?,?,?,?,?,?,?,1)""",
                (
                    data["title"], data["author"], str(source), "txt",
                    data.get("language", "hu"), data["narrator_instruct"],
                    len(chapters), "complete", "demo", "Auris próbakönyv",
                    "A szereplők előre be vannak állítva.",
                    hashlib.sha256(raw).hexdigest(), data.get("description", ""),
                ),
            ).lastrowid
            frequency = {c["name"]: 0 for c in data["characters"]}
            for chapter, spec in zip(chapters, data["chapters"]):
                chapter_id = conn.execute(
                    "INSERT INTO chapters(book_id,title,order_num,section_type,content,word_count) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        book_id, chapter["title"], chapter["order_num"],
                        chapter.get("section_type", "chapter"), chapter["content"],
                        len(chapter["content"].split()),
                    ),
                ).lastrowid
                speakers = spec["speakers"]
                for unit in enrichment.build_speaker_units(chapter["content"]):
                    turn = unit.get("turn_index")
                    if not unit["dialogue_candidate"] or turn is None or turn >= len(speakers):
                        continue
                    conn.execute(
                        "INSERT INTO speaker_annotations(book_id,chapter_id,unit_index,unit_text,"
                        "speaker_name,confidence,source) VALUES(?,?,?,?,?,1.0,'automatic')",
                        (book_id, chapter_id, unit["index"], unit["text"], speakers[turn]),
                    )
                for name in speakers:
                    frequency[name] += 1
            for character in data["characters"]:
                conn.execute(
                    "INSERT INTO characters(book_id,name,gender,frequency,instruct,color_hex) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        book_id, character["name"], character["gender"],
                        frequency.get(character["name"], 0), character["instruct"],
                        character["color_hex"],
                    ),
                )
    except Exception:
        source.unlink(missing_ok=True)
        raise
    return {"book_id": book_id, "created": True}


# ── Cleanup of the earlier fake sample library ───────────────────────────────

def cleanup_legacy_samples() -> int:
    """Remove the placeholder library an earlier onboarding draft auto-inserted.

    Those rows pointed at non-existent ``sample://`` sources and tone-only
    audio, so they only confused new users. Real books are never touched.
    """
    removed = 0
    with get_conn() as conn:
        removed += conn.execute(
            "DELETE FROM books WHERE file_path LIKE 'sample://%'"
        ).rowcount
        for table in ("voice_profiles", "pronunciation_rules"):
            if not _table_exists(conn, table):
                continue
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "is_sample" in cols:
                removed += conn.execute(f"DELETE FROM {table} WHERE is_sample=1").rowcount
    for path in AUDIO_CACHE_DIR.glob("sample_ch*_seg_*.wav"):
        path.unlink(missing_ok=True)
    if removed:
        log.info("Removed %d placeholder onboarding rows", removed)
    return removed
