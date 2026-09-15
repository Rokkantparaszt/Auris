(function () {
  const $ = (id) => document.getElementById(id);
  const audio = $("voice-audio");
  let builtins = [];
  let profiles = [];

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "A művelet nem sikerült.");
    return data;
  }

  const postJson = (url, body) => api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function setStatus(id, text, kind = "") {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    el.className = `voice-status${kind ? " is-" + kind : ""}`;
  }

  function previewText() {
    return $("preview-text").value.trim();
  }

  // Generating a preview can take a few seconds; keep the button honest.
  async function play(button, request, statusId) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Készül…";
    if (statusId) setStatus(statusId, "A próbahang készül, ez néhány másodperc…");
    try {
      const data = await request();
      audio.src = `${data.audio_url}?t=${Date.now()}`;
      await audio.play();
      if (statusId) setStatus(statusId, "");
    } catch (error) {
      if (statusId) setStatus(statusId, error.message, "error");
      else alert(error.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  function renderBuiltins() {
    const names = new Set(profiles.map((p) => `${p.name}|${p.instruct}`));
    $("builtin-voices").innerHTML = builtins.map((voice) => {
      const added = names.has(`${voice.name}|${voice.instruct}`);
      return `<article class="voice-card">
        <h3><span class="voice-icon" aria-hidden="true">${voice.gender === "female" ? "♀" : "♂"}</span>${esc(voice.name)}</h3>
        <p>${esc(voice.description)}</p>
        <div class="voice-card-actions">
          <button type="button" class="btn btn-sm btn-ghost" data-play-builtin="${esc(voice.id)}">▶ Meghallgatás</button>
          <button type="button" class="btn btn-sm ${added ? "btn-ghost" : "btn-primary"}" data-add-builtin="${esc(voice.id)}"${added ? " disabled" : ""}>${added ? "✓ Hangjaid között" : "+ Hangjaimhoz"}</button>
        </div>
      </article>`;
    }).join("");
  }

  function renderProfiles() {
    $("my-voices").innerHTML = profiles.length
      ? profiles.map((profile) => `<article class="voice-card">
          <h3><span class="voice-icon" aria-hidden="true">${profile.ref_audio_path ? "●" : "♪"}</span>${esc(profile.name)}</h3>
          <p>${esc(window.AurisGuide.describeVoice(profile))}</p>
          <div class="voice-card-actions">
            <button type="button" class="btn btn-sm btn-ghost" data-play-profile="${profile.id}">▶ Meghallgatás</button>
            ${profile.ref_audio_path ? `<a class="btn btn-sm btn-ghost" href="/api/voice-profiles/${profile.id}/export" title="Fájlba mentés, hogy egy másik gépen is használhasd">Mentés fájlba</a>` : ""}
            <button type="button" class="btn btn-sm btn-ghost" data-delete-profile="${profile.id}">Törlés</button>
          </div>
        </article>`).join("")
      : `<div class="voice-empty-note">Még nincs saját hanglistád. Kezdésnek add hozzá valamelyik <strong>beépített hangot</strong> lent – egy kattintás.</div>`;
  }

  async function loadAll() {
    [builtins, profiles] = await Promise.all([
      api("/api/voices/builtin"),
      api("/api/voice-profiles"),
    ]);
    renderProfiles();
    renderBuiltins();
  }

  async function refreshEngineBanner() {
    try {
      const status = await api("/api/guide/status");
      const engine = status.engine;
      const ready = engine.state === "ready";
      $("engine-banner").hidden = ready;
      if (!ready) {
        $("engine-banner-text").textContent = !engine.model_present
          ? "A hangok meghallgatásához előbb töltsd le a beszédmotort."
          : engine.state === "loading"
            ? "A beszédmotor töltődik. Amint kész, meghallgathatod a hangokat."
            : "A hangok meghallgatásához a beszédmotornak futnia kell.";
      }
      if (!ready) setTimeout(refreshEngineBanner, 4000);
    } catch (_) {}
  }

  document.addEventListener("click", async (event) => {
    const playBuiltin = event.target.closest("[data-play-builtin]");
    const addBuiltin = event.target.closest("[data-add-builtin]");
    const playProfile = event.target.closest("[data-play-profile]");
    const deleteProfile = event.target.closest("[data-delete-profile]");
    try {
      if (playBuiltin) {
        await play(playBuiltin, () => postJson("/api/voices/preview", {
          builtin_id: playBuiltin.dataset.playBuiltin, text: previewText(),
        }));
      } else if (playProfile) {
        await play(playProfile, () => postJson("/api/voices/preview", {
          profile_id: Number(playProfile.dataset.playProfile), text: previewText(),
        }));
      } else if (addBuiltin) {
        addBuiltin.disabled = true;
        await postJson("/api/voices", { builtin_id: addBuiltin.dataset.addBuiltin });
        await loadAll();
        window.AurisGuide?.refresh();
      } else if (deleteProfile) {
        const profile = profiles.find((p) => p.id === Number(deleteProfile.dataset.deleteProfile));
        if (!confirm(`Törlöd a(z) „${profile?.name}” hangot a Hangjaim közül? A könyvek, amelyek már ezt használják, megtartják.`)) return;
        await api(`/api/voice-profiles/${profile.id}`, { method: "DELETE" });
        await loadAll();
        window.AurisGuide?.refresh();
      }
    } catch (error) {
      alert(error.message);
      if (addBuiltin) addBuiltin.disabled = false;
    }
  });

  function designInstruct() {
    const gender = document.querySelector('[name="design-gender"]:checked').value;
    return [gender, $("design-age").value, $("design-pitch").value].join(", ");
  }

  function updateDesignWarning() {
    $("design-warning").hidden = !["child", "teenager"].includes($("design-age").value);
  }
  $("design-age").addEventListener("change", updateDesignWarning);

  $("design-preview").addEventListener("click", (event) => play(
    event.currentTarget,
    () => postJson("/api/voices/preview", { instruct: designInstruct(), text: previewText() }),
    "design-status",
  ));

  $("design-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const saved = await postJson("/api/voices", { name: $("design-name").value, instruct: designInstruct() });
      setStatus("design-status", `„${saved.name}” elmentve a Hangjaim közé.`, "ok");
      $("design-name").value = "";
      await loadAll();
      window.AurisGuide?.refresh();
    } catch (error) {
      setStatus("design-status", error.message, "error");
    }
  });

  // The recording is prepared once on selection (length, crop to 10 s, WAV);
  // preview and save then refer to that clip by token.
  let clip = null;
  let autoTranscript = "";
  let sttReady = false;
  let sttPoll = null;

  function renderStt(stt) {
    sttReady = stt.model_present;
    const downloading = stt.state === "downloading";
    $("stt-offer").hidden = sttReady;
    $("stt-download").hidden = downloading;
    $("stt-progress").classList.toggle("hidden", !downloading && !stt.error);
    $("stt-bar").style.width = `${stt.percent}%`;
    $("stt-msg").textContent = stt.error || (downloading ? `Letöltés: ${stt.percent}%. Közben nyugodtan dolgozhatsz tovább.` : "");
    if (downloading && !sttPoll) {
      sttPoll = setInterval(async () => {
        try { renderStt(await api("/api/stt/status")); } catch (_) {}
      }, 2000);
    }
    if (!downloading && sttPoll) {
      clearInterval(sttPoll);
      sttPoll = null;
      // Finished while a recording waits: transcribe it now.
      if (sttReady && clip && !$("clone-text").value.trim()) transcribeClip();
    }
    $("clone-stt-hint").textContent = sttReady
      ? "Az Auris a gépeden magától leírja, mi hangzik el – neked csak ellenőrizned és javítanod kell."
      : "Írd le szó szerint, mi hangzik el a felvételen.";
  }

  $("stt-download").addEventListener("click", async () => {
    try {
      renderStt(await api("/api/stt/download", { method: "POST" }));
    } catch (error) {
      $("stt-msg").textContent = error.message;
    }
  });

  api("/api/stt/status").then(renderStt).catch(() => {});

  function seconds(value) {
    return `${Number(value).toLocaleString("hu-HU", { maximumFractionDigits: 1 })} mp`;
  }

  function describeClip(info) {
    if (info.cropped && !info.cut_at_pause) {
      return {
        text: `A felvétel ${seconds(info.original_duration)} hosszú. Az első 10 másodpercben nem találtam szünetet, ezért a hangminta szó közben érhet véget. Ha így hallod, válassz olyan felvételt, amelyben van egy rövid szünet.`,
        warn: true,
      };
    }
    if (info.cropped) {
      return {
        text: `A felvétel ${seconds(info.original_duration)} hosszú. Kivágtam belőle egy ${seconds(info.duration)} részletet, amely szünetnél ér véget, nem szó közepén – hallgasd meg lent.`,
        warn: false,
      };
    }
    if (!info.too_short && info.original_duration - info.duration > 0.3) {
      return {
        text: `A felvétel ${seconds(info.original_duration)} hosszú. Az elején és végén lévő csendet levágtam, a hangminta ${seconds(info.duration)}.`,
        warn: false,
      };
    }
    if (info.too_short) {
      return {
        text: `A felvétel csak ${seconds(info.duration)}. Legalább ${seconds(info.min_seconds)} ajánlott, különben a hang kevésbé lesz hasonló.`,
        warn: true,
      };
    }
    return { text: `A felvétel ${seconds(info.duration)} hosszú – ez jó hangminta.`, warn: false };
  }

  async function transcribeClip() {
    if (!clip) return;
    const button = $("clone-transcribe");
    const textarea = $("clone-text");
    const token = clip.token;
    button.hidden = false;
    button.disabled = true;
    textarea.disabled = true;
    textarea.placeholder = "Átirat készül a gépeden…";
    setStatus("clone-status", "Átirat készül a gépeden. Első alkalommal a beszédfelismerő betöltése fél percig is eltarthat…");
    try {
      const data = await api(`/api/voices/reference/${token}/transcribe`, { method: "POST" });
      if (clip?.token !== token) return;
      // Never overwrite something the user typed themselves.
      if (!textarea.value.trim() || textarea.value === autoTranscript) {
        textarea.value = data.text;
        autoTranscript = data.text;
      }
      setStatus("clone-status", "Kész az átirat. Hallgasd meg a felvételt, és javítsd ki, ahol nem pontos.", "ok");
    } catch (error) {
      setStatus("clone-status", `${error.message} Az átiratot kézzel is beírhatod.`, "error");
    } finally {
      button.disabled = false;
      textarea.disabled = false;
      textarea.placeholder = "Pontosan azt írd ide, amit a felvételen mondanak.";
      textarea.focus();
    }
  }

  function resetClip() {
    clip = null;
    $("clone-clip").hidden = true;
    $("clone-transcribe").hidden = true;
    if ($("clone-text").value === autoTranscript) $("clone-text").value = "";
  }

  /**
   * Shared path for uploaded files and microphone recordings:
   * prepare (length, pause-aware trim) → play back → transcript.
   * Throws so each source can show the error next to its own controls.
   */
  async function useRecording(file, { fromMicrophone = false } = {}) {
    resetClip();
    setStatus("clone-status", "A felvétel feldolgozása…");
    const form = new FormData();
    form.append("file", file);
    let info;
    try {
      info = await api("/api/voices/reference", { method: "POST", body: form });
    } finally {
      setStatus("clone-status", "");
    }
    clip = { ...info, fileName: file.name };
    const summary = describeClip(info);
    $("clone-clip-info").textContent = summary.text;
    $("clone-clip-info").classList.toggle("is-warn", summary.warn);
    $("clone-clip-audio").src = `${info.audio_url}?t=${Date.now()}`;
    $("clone-clip").hidden = false;
    if (!$("clone-name").value.trim()) {
      $("clone-name").value = fromMicrophone ? "Saját hangom" : file.name.replace(/\.[^.]+$/, "").slice(0, 100);
    }
    if (info.stt_available) {
      await transcribeClip();
    } else if (fromMicrophone) {
      // No speech recognition yet: start from the text that was meant to be read.
      const textarea = $("clone-text");
      if (!textarea.value.trim() || textarea.value === autoTranscript) {
        textarea.value = window.AurisRecorder.RECORDING_SCRIPT;
        autoTranscript = textarea.value;
      }
      $("stt-offer").hidden = false;
      setStatus(
        "clone-status",
        info.cropped
          ? "Beírtam a felolvasandó szöveget. A felvételt levágtam – töröld a szöveg végéből, ami már nem hallható, és javítsd, ahol eltértél tőle."
          : "Beírtam a felolvasandó szöveget. Hallgasd meg a felvételt, és javítsd ki, ahol eltértél tőle.",
      );
    } else {
      $("stt-offer").hidden = false;
    }
  }

  $("clone-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) {
      resetClip();
      return;
    }
    try {
      await useRecording(file);
    } catch (error) {
      event.target.value = "";
      setStatus("clone-status", error.message, "error");
    }
  });

  // ── Microphone recording ─────────────────────────────────────────────────
  const Recorder = window.AurisRecorder;
  const MIC_STORAGE = "auris-microphone";
  let recorder = null;
  let recordTimer = null;

  $("record-script").textContent = Recorder.RECORDING_SCRIPT;

  function setRecordStatus(text, kind = "") {
    setStatus("record-status", text, kind);
  }

  function formatTimer(secondsElapsed) {
    const whole = Math.floor(secondsElapsed);
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  }

  function showLevel({ rmsDb, peak }) {
    const width = Math.max(0, Math.min(100, ((rmsDb + 60) / 60) * 100));
    const fill = $("mic-level");
    fill.style.width = `${width}%`;
    fill.classList.toggle("is-quiet", rmsDb < -45);
    fill.classList.toggle("is-loud", peak >= 0.99);
    if (!recorder?.recording) {
      $("mic-level-hint").textContent = peak >= 0.99
        ? "Túl hangos – menj kicsit távolabb a mikrofontól."
        : "Mondj valamit – a csíknak mozognia kell. Ha zöld, jó a hangerő.";
    }
  }

  async function fillMicrophoneList(selectedId) {
    let options = [];
    try {
      options = await recorder.listMicrophones();
    } catch (_) {}
    const select = $("mic-select");
    select.innerHTML = options.map((option) => `<option value="${esc(option.id)}">${esc(option.label)}</option>`).join("");
    if (selectedId && options.some((option) => option.id === selectedId)) select.value = selectedId;
    document.querySelector(".mic-select-label").hidden = options.length < 2;
    return options;
  }

  function setMicUi(state) {
    const open = state !== "closed";
    const recording = state === "recording";
    $("mic-open").hidden = open;
    $("mic-refresh").hidden = true; // error handlers show it when it helps
    $("mic-live").hidden = !open;
    $("record-start").hidden = recording;
    $("record-stop").hidden = !recording;
    $("mic-select").disabled = recording;
    $("clone-source-mic").classList.toggle("recording", recording);
    document.querySelectorAll('[name="clone-source"]').forEach((input) => { input.disabled = recording; });
  }

  async function openMicrophone(deviceId) {
    if (!recorder) {
      recorder = new Recorder.MicrophoneRecorder({
        onLevel: showLevel,
        onDeviceLost: (error, wasRecording) => {
          stopTimer();
          setMicUi("closed");
          setRecordStatus(
            wasRecording ? `${Recorder.micErrorMessage(error)} A félbemaradt felvételt nem használtam fel.` : Recorder.micErrorMessage(error),
            "error",
          );
          $("mic-refresh").hidden = false;
        },
      });
    }
    $("mic-open").disabled = true;
    setRecordStatus("A böngésző engedélyt kérhet a mikrofonhoz – kattints az „Engedélyezés” gombra.");
    try {
      let preferred = deviceId;
      if (preferred === undefined) {
        try { preferred = localStorage.getItem(MIC_STORAGE) || ""; } catch (_) { preferred = ""; }
      }
      let activeId;
      try {
        activeId = await recorder.open(preferred);
      } catch (error) {
        // A remembered microphone may be gone; fall back to the default one.
        if (preferred && error.name === "OverconstrainedError") activeId = await recorder.open("");
        else throw error;
      }
      await fillMicrophoneList(activeId);
      try { localStorage.setItem(MIC_STORAGE, activeId || ""); } catch (_) {}
      setMicUi("open");
      setRecordStatus("");
    } catch (error) {
      await recorder.close();
      setMicUi("closed");
      setRecordStatus(Recorder.micErrorMessage(error), "error");
      $("mic-refresh").hidden = false;
    } finally {
      $("mic-open").disabled = false;
    }
  }

  function stopTimer() {
    clearInterval(recordTimer);
    recordTimer = null;
  }

  function startRecording() {
    try {
      recorder.start();
    } catch (error) {
      setRecordStatus(Recorder.micErrorMessage(error), "error");
      return;
    }
    setMicUi("recording");
    setRecordStatus("Felvétel folyamatban – olvasd fel a szöveget, majd kattints a „Kész” gombra.");
    $("mic-level-hint").textContent = "Figyeld a csíkot: ha végig a bal szélén marad, túl halk vagy.";
    $("record-timer").textContent = "0:00";
    recordTimer = setInterval(() => {
      const elapsed = recorder.elapsed();
      $("record-timer").textContent = formatTimer(elapsed);
      if (elapsed >= Recorder.MAX_RECORD_SECONDS) finishRecording(true);
    }, 200);
  }

  async function finishRecording(automatic = false) {
    if (!recorder?.recording) return;
    stopTimer();
    const { file, analysis } = recorder.stop();
    setMicUi("open");
    if (analysis.tooShort) {
      setRecordStatus("Ez túl rövid volt. Olvasd fel a teljes szöveget, és csak utána kattints a „Kész” gombra.", "error");
      return;
    }
    if (analysis.silent) {
      setRecordStatus("A felvételen nem hallatszik semmi. Lehet, hogy rossz mikrofon van kiválasztva, vagy le van némítva. Nézd meg, mozog-e a csík, amikor beszélsz, és vedd fel újra.", "error");
      return;
    }
    const warnings = [];
    if (automatic) warnings.push(`A felvétel ${Recorder.MAX_RECORD_SECONDS} másodperc után magától leállt.`);
    if (analysis.tooLoud) warnings.push("Helyenként túl hangos volt, ezért torzulhat – ha így hallod, menj kicsit távolabb, és vedd fel újra.");
    else if (analysis.tooQuiet) warnings.push("Elég halk lett. Felerősítettem, de jobb lesz, ha közelebb mész a mikrofonhoz, és újra felveszed.");
    setRecordStatus(warnings.join(" ") || "Kész a felvétel. Lent meghallgathatod. Ha nem tetszik, vedd fel újra.", warnings.length ? "error" : "ok");
    $("record-start").textContent = "● Újra felveszem";
    // Free the microphone once there is a take; the browser indicator turns off.
    await recorder.close();
    setMicUi("closed");
    $("mic-open").textContent = "🎙 Újra felveszem";
    try {
      await useRecording(file, { fromMicrophone: true });
    } catch (error) {
      setStatus("clone-status", error.message, "error");
    }
  }

  $("mic-open").addEventListener("click", () => openMicrophone());
  $("mic-refresh").addEventListener("click", () => openMicrophone());
  $("mic-select").addEventListener("change", (event) => openMicrophone(event.target.value));
  $("record-start").addEventListener("click", startRecording);
  $("record-stop").addEventListener("click", () => finishRecording(false));

  navigator.mediaDevices?.addEventListener?.("devicechange", async () => {
    if (!recorder?.active) return;
    const options = await fillMicrophoneList($("mic-select").value);
    if (!options.length) recorder.handleDeviceLost();
  });

  document.querySelectorAll('[name="clone-source"]').forEach((input) => {
    input.addEventListener("change", async () => {
      const mic = document.querySelector('[name="clone-source"]:checked').value === "mic";
      $("clone-source-mic").hidden = !mic;
      $("clone-source-file").hidden = mic;
      if (!mic && recorder?.active) {
        await recorder.close();
        setMicUi("closed");
      }
    });
  });

  const unsupported = Recorder.microphoneSupport();
  if (unsupported) {
    setRecordStatus(Recorder.micErrorMessage(unsupported), "error");
    $("mic-open").disabled = true;
  }
  window.addEventListener("pagehide", () => recorder?.close());

  $("clone-transcribe").addEventListener("click", transcribeClip);

  function cloneForm(requireName) {
    const text = $("clone-text").value.trim();
    if (!clip) throw new Error("Előbb készíts vagy válassz ki egy felvételt (1. lépés).");
    if (!text) throw new Error("Írd le, mi hangzik el a felvételen (2. lépés).");
    if (requireName && !$("clone-name").value.trim()) throw new Error("Adj nevet a hangnak (3. lépés).");
    const form = new FormData();
    form.append("reference_token", clip.token);
    form.append("file_name", clip.fileName);
    form.append("ref_text", text);
    form.append("name", $("clone-name").value);
    form.append("text", previewText());
    return form;
  }

  $("clone-preview").addEventListener("click", (event) => play(
    event.currentTarget,
    async () => api("/api/voices/preview", { method: "POST", body: cloneForm(false) }),
    "clone-status",
  ));

  $("clone-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const saved = await api("/api/voices", { method: "POST", body: cloneForm(true) });
      setStatus("clone-status", `„${saved.name}” elmentve a Hangjaim közé.`, "ok");
      event.target.reset();
      clip = null;
      autoTranscript = "";
      $("clone-clip").hidden = true;
      $("clone-transcribe").hidden = true;
      $("clone-source-mic").hidden = false;
      $("clone-source-file").hidden = true;
      $("mic-open").textContent = "🎙 Mikrofon bekapcsolása";
      $("record-start").textContent = "● Felvétel indítása";
      setStatus("record-status", "");
      await loadAll();
      window.AurisGuide?.refresh();
    } catch (error) {
      setStatus("clone-status", error.message, "error");
    }
  });

  $("import-voice").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const imported = await api("/api/voice-profiles/import", { method: "POST", body: form });
      setStatus("import-status", `„${imported.name}” betöltve a Hangjaim közé.`, "ok");
      await loadAll();
      window.AurisGuide?.refresh();
    } catch (error) {
      setStatus("import-status", error.message, "error");
    } finally {
      event.target.value = "";
    }
  });

  loadAll().catch((error) => {
    $("my-voices").innerHTML = `<div class="voice-empty-note">${esc(error.message)}</div>`;
  });
  refreshEngineBanner();
})();
