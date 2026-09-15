"""Getting-started guide and book-independent voice library endpoints."""

from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

from core import experience, local_stt, onboarding, voice_sample
from core.database import get_conn, get_db_path

bp = Blueprint("guide", __name__)

PREVIEW_TEXT = (
    "A délutáni fényben csendesen lapoztam a könyvet. "
    "– Hát itt vagy végre! – kiáltott fel valaki az ajtóban."
)


@bp.errorhandler(ValueError)
def invalid_request(error):
    return jsonify(error=str(error)), 400


def engine_not_ready_message(status: dict) -> str:
    """Plain-language reason why speech cannot be generated right now."""
    state = status.get("state")
    if state == "loading":
        return ("A beszédmotor most töltődik be. Első alkalommal ez néhány percig "
                "is tarthat – a jobb felső sarokban látod, mikor lesz kész.")
    if state == "paused":
        return ("Most szereplőelemzés fut, ezért a beszédmotor szünetel. "
                "A Feladatok oldalon látod, mikor végez.")
    if not onboarding.model_files_present():
        return ("Még nincs letöltve a beszédmotor. Nyisd meg a Beállítások → "
                "Gyors beállítás oldalt, és kattints a Letöltés gombra.")
    if state == "error":
        return ("A beszédmotor nem tudott elindulni: " + str(status.get("message") or "ismeretlen hiba")
                + " Nézd meg a Beállítások → Gyors beállítás oldalt.")
    return "A beszédmotor még nem áll készen. Várj egy kicsit, majd próbáld újra."


def engine_not_ready_response(status: dict):
    return jsonify(
        error=engine_not_ready_message(status),
        status=status,
        setup_needed=True,
        setup_url="/settings#setup",
    ), 503


def _app():
    import app as application
    return application


def _engine_status() -> dict:
    application = _app()
    if application._character_analysis_is_active():
        return {"state": "paused"}
    return application.tts.status()


# ── Guide ────────────────────────────────────────────────────────────────────

@bp.route("/api/guide/status")
def guide_status():
    return jsonify(onboarding.guide_status(_engine_status()))


@bp.route("/api/guide/state", methods=["POST"])
def guide_state():
    data = request.get_json(silent=True) or {}
    if "dismissed" in data:
        onboarding.set_dismissed(bool(data["dismissed"]))
    if data.get("event"):
        onboarding.record_event(str(data["event"]))
    return jsonify(onboarding.guide_status(_engine_status()))


@bp.route("/api/guide/demo-book", methods=["POST"])
def demo_book():
    return jsonify(onboarding.create_demo_book(_app().UPLOAD_DIR))


# ── Voice library ────────────────────────────────────────────────────────────

@bp.route("/voices")
def voices_page():
    return render_template("voices.html")


@bp.route("/api/voices/builtin")
def builtin_voices():
    return jsonify(onboarding.BUILTIN_VOICES)


def _profile(profile_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise ValueError("A hang nem található.")
    return dict(row)


def _voice_source(data) -> dict:
    """Resolve builtin_id / profile_id / instruct into generation parameters."""
    if data.get("builtin_id"):
        voice = onboarding.builtin_voice(str(data["builtin_id"]))
        if not voice:
            raise ValueError("Ismeretlen beépített hang.")
        return {"instruct": voice["instruct"], "ref_audio": None, "ref_text": None}
    if data.get("profile_id"):
        profile = _profile(int(data["profile_id"]))
        ref = profile["ref_audio_path"]
        if ref and not Path(ref).is_file():
            raise ValueError("A hang felvétele hiányzik. Töröld, és készítsd el újra.")
        return {
            "instruct": profile["instruct"] or "",
            "ref_audio": ref or None,
            "ref_text": (profile["ref_text"] or None) if ref else None,
        }
    instruct = str(data.get("instruct") or "").strip()
    if not instruct:
        raise ValueError("Válassz hangot a meghallgatáshoz.")
    return {"instruct": instruct, "ref_audio": None, "ref_text": None}


_TOKEN = re.compile(r"[0-9a-f]{32}")


def _reference_dir() -> Path:
    folder = Path(_app().UPLOAD_DIR) / ".staging"
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 86400
    for old in folder.glob("ref_*"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return folder


def _reference_path(token: str) -> Path:
    token = str(token or "")
    path = _reference_dir() / f"ref_{token}.wav"
    if not _TOKEN.fullmatch(token) or not path.is_file():
        raise ValueError("A felvétel lejárt vagy nem található. Válaszd ki újra.")
    return path


def _prepare_upload(uploaded) -> tuple[str, dict]:
    """Store an uploaded recording as a cropped WAV clip; return its token."""
    extension = Path(str(uploaded.filename or "")).suffix.lower()
    if extension not in voice_sample.SUPPORTED_EXTENSIONS:
        raise ValueError("WAV, MP3, M4A vagy OGG felvételt válassz.")
    token = uuid.uuid4().hex
    folder = _reference_dir()
    source = folder / f"ref_src_{token}{extension}"
    uploaded.save(source)
    try:
        info = voice_sample.prepare_reference(source, folder / f"ref_{token}.wav")
    finally:
        source.unlink(missing_ok=True)
    return token, info


def _uploaded_reference(data) -> tuple[Path | None, bool]:
    """Reference WAV from a prepared token or a direct upload (temporary)."""
    if data.get("reference_token"):
        return _reference_path(data["reference_token"]), False
    uploaded = request.files.get("file")
    if uploaded:
        token, _info = _prepare_upload(uploaded)
        return _reference_path(token), True
    return None, False


@bp.route("/api/voices/reference", methods=["POST"])
def prepare_reference():
    uploaded = request.files.get("file")
    if not uploaded:
        raise ValueError("Válaszd ki a felvételt.")
    token, info = _prepare_upload(uploaded)
    return jsonify(
        token=token,
        audio_url=f"/api/voices/reference/{token}.wav",
        max_seconds=voice_sample.MAX_SECONDS,
        min_seconds=voice_sample.MIN_SECONDS,
        stt_available=local_stt.model_present(),
        **info,
    )


@bp.route("/api/voices/reference/<token>.wav")
def reference_audio(token):
    return send_file(_reference_path(token), mimetype="audio/wav")


@bp.route("/api/voices/reference/<token>/transcribe", methods=["POST"])
def transcribe_reference(token):
    path = _reference_path(token)
    if not local_stt.model_present():
        return jsonify(
            error=f"Az automatikus átirathoz előbb töltsd le a beszédfelismerőt (egyszer, kb. {local_stt.MODEL_SIZE_GB:.1f} GB).",
            stt_missing_model=True,
        ), 409
    return jsonify(local_stt.transcribe(path))


@bp.route("/api/stt/status")
def stt_status():
    return jsonify(local_stt.status())


@bp.route("/api/stt/download", methods=["POST"])
def stt_download():
    return jsonify(local_stt.start_download())


@bp.route("/api/voices/preview", methods=["POST"])
def preview_voice():
    application = _app()
    status = _engine_status()
    if status.get("state") != "ready":
        return engine_not_ready_response(status)

    temp = None
    if request.files.get("file") or request.form.get("reference_token"):
        data = request.form
        ref, is_temp = _uploaded_reference(data)
        temp = ref if is_temp else None
        source = {
            "instruct": str(data.get("instruct") or ""),
            "ref_audio": str(ref),
            "ref_text": str(data.get("ref_text") or "").strip() or None,
        }
    else:
        data = request.get_json(silent=True) or {}
        source = _voice_source(data)
    text = str(data.get("text") or PREVIEW_TEXT).strip()[:1500] or PREVIEW_TEXT
    try:
        result = application.tts.generate_preview(
            instruct=source["instruct"],
            sample_text=text,
            language="hu",
            ref_audio=source["ref_audio"],
            ref_text=source["ref_text"],
        )
    finally:
        if source["ref_audio"]:
            # The clip or its transcript may still change before saving.
            try:
                application.tts.invalidate_voice_prompt(source["ref_audio"], source["ref_text"])
            except Exception:
                pass
        if temp:
            temp.unlink(missing_ok=True)
    return jsonify(audio_url=f"/api/audio/{result['cache_key']}")


def _unique_name(conn, base: str) -> str:
    base = base.strip()[:100]
    if not base:
        raise ValueError("Adj nevet a hangnak.")
    existing = {str(r["name"]).casefold() for r in conn.execute("SELECT name FROM voice_profiles")}
    name, suffix = base, 2
    while name.casefold() in existing:
        tail = f" ({suffix})"
        name = base[:100 - len(tail)] + tail
        suffix += 1
    return name


@bp.route("/api/voices", methods=["POST"])
def create_voice():
    uploaded = request.files.get("file")
    is_form = bool(uploaded or request.form.get("reference_token"))
    data = request.form if is_form else (request.get_json(silent=True) or {})
    ref_path = ref_name = ref_text = None
    if is_form:
        ref_text = str(data.get("ref_text") or "").strip()
        if not ref_text:
            raise ValueError("Írd le pontosan, mi hangzik el a felvételen.")
        prepared, is_temp = _uploaded_reference(data)
        folder = Path(get_db_path()).parent / "voice_profiles"
        folder.mkdir(parents=True, exist_ok=True)
        ref_path = folder / (uuid.uuid4().hex + ".wav")
        shutil.copy2(prepared, ref_path)
        if is_temp:
            prepared.unlink(missing_ok=True)
        ref_name = Path(str(uploaded.filename if uploaded else data.get("file_name") or "felvetel.wav")).name[:200]
        instruct = str(data.get("instruct") or "").strip()
    elif data.get("builtin_id"):
        voice = onboarding.builtin_voice(str(data["builtin_id"]))
        if not voice:
            raise ValueError("Ismeretlen beépített hang.")
        instruct = voice["instruct"]
        data = {"name": data.get("name") or voice["name"]}
    else:
        instruct = str(data.get("instruct") or "").strip()
        if not instruct:
            raise ValueError("Válaszd ki a hang tulajdonságait.")
    try:
        with get_conn() as conn:
            name = _unique_name(conn, str(data.get("name") or ""))
            profile_id = conn.execute(
                "INSERT INTO voice_profiles(name,instruct,ref_audio_path,ref_audio_name,ref_text) "
                "VALUES(?,?,?,?,?)",
                (name, instruct, str(ref_path) if ref_path else None, ref_name, ref_text),
            ).lastrowid
    except Exception:
        if ref_path:
            ref_path.unlink(missing_ok=True)
        raise
    return jsonify(_profile(profile_id))


@bp.route("/api/books/<int:bid>/voice-assign", methods=["POST"])
def assign_voice(bid):
    """Give the narrator or one character a built-in or saved voice in one step."""
    from core import experience_api

    application = _app()
    data = request.get_json(silent=True) or {}
    char_id = data.get("char_id")
    char_id = int(char_id) if char_id not in (None, "", "narrator") else None
    with application._work_dispatch_lock:
        experience_api._assert_idle(allow_interactive=True)
        if data.get("profile_id"):
            experience.apply_profile(int(data["profile_id"]), bid, char_id)
        else:
            voice = onboarding.builtin_voice(str(data.get("builtin_id") or ""))
            if not voice:
                raise ValueError("Válassz hangot.")
            with get_conn() as conn:
                experience._book(conn, bid)
                if char_id is None:
                    result = conn.execute(
                        "UPDATE books SET narrator_instruct=?, narrator_ref_audio_path=NULL, "
                        "narrator_ref_audio_name=NULL, narrator_ref_text=NULL WHERE id=?",
                        (voice["instruct"], bid),
                    )
                else:
                    result = conn.execute(
                        "UPDATE characters SET instruct=?, gender=?, ref_audio_path=NULL, "
                        "ref_audio_name=NULL, ref_text=NULL WHERE book_id=? AND id=?",
                        (voice["instruct"], voice["gender"], bid, char_id),
                    )
                if result.rowcount == 0:
                    raise ValueError("A szereplő nem található.")
                experience._invalidate(conn, bid)
    return jsonify(ok=True)
