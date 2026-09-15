/**
 * Auris "Első lépések" guide.
 *
 * Step completion comes from /api/guide/status (real library state), so the
 * guide stays correct after restarts. The Library page shows the guide inline;
 * every other page opens it as a drawer from the top navigation.
 */
(function () {
  const TIP_STORAGE = "auris-tips-hidden";

  function escHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function engineLine(engine) {
    if (!engine.model_present) return "A beszédmotor még nincs letöltve.";
    if (engine.state === "ready") return "A beszédmotor kész, bármikor felolvashat.";
    if (engine.state === "loading") return "A beszédmotor a gépeden van, most töltődik be.";
    if (engine.state === "error") return "A beszédmotor nem indult el. Nyisd meg a beállítást a részletekért.";
    return "A beszédmotor a gépeden van. Könyv megnyitásakor magától betöltődik.";
  }

  function onLibraryPage() {
    return Boolean(document.getElementById("file-input"));
  }

  // Each step: what it is, why it matters, and buttons that do the thing.
  function describeSteps(s) {
    const recent = s.recent_book_id;
    const demo = s.demo_book_id;
    const addOwn = onLibraryPage()
      ? { label: "Saját könyv feltöltése", action: "pick-file", primary: true }
      : { label: "Saját könyv feltöltése", href: "/?add=1", primary: true };
    return {
      engine: {
        title: "Beszédmotor telepítése",
        text: "Az Auris a saját gépeden, internet nélkül olvas fel. Ehhez egyszer le kell tölteni a beszédmotort.",
        doneText: engineLine(s.engine),
        actions: s.steps[0].done
          ? []
          : [{ label: "Beszédmotor beállítása", href: "/settings#setup", primary: true }],
      },
      voices: {
        title: "Hangok kiválasztása",
        text: "A hang dönti el, ki olvassa a könyvet. Van 8 kész hang, amit azonnal használhatsz – vagy egy rövid felvételből a saját hangodat is elkészítheted.",
        doneText: `${s.voice_count} hang van a Hangjaim között. Bármikor adhatsz hozzá újat.`,
        actions: [{ label: "Hangok megnyitása", href: "/voices", primary: !s.steps[1].done }],
      },
      book: {
        title: "Könyv hozzáadása",
        text: "Tölts fel egy e-könyvet (EPUB, PDF, DOCX, TXT, MOBI), vagy kezdd a kétperces próbakönyvvel, amelyben a szereplők hangjai már be vannak állítva.",
        doneText: `${s.book_count} könyv van a könyvtáradban.`,
        actions: [
          addOwn,
          demo
            ? { label: "Próbakönyv megnyitása", href: `/reader/${demo}` }
            : { label: "Próbakönyv hozzáadása", action: "demo" },
        ],
      },
      listen: {
        title: "Meghallgatás egy narrátorral",
        text: "Nyisd meg a könyvet, és nyomd meg lent a ▶ Lejátszás gombot. A hang menet közben készül, ezért az első mondat néhány másodpercig is eltarthat.",
        doneText: "Már hallgattál felolvasást. A narrátor hangját a könyvben a „Hangok” gombbal cserélheted.",
        actions: recent ? [{ label: "Könyv megnyitása", href: `/reader/${recent}`, primary: !s.steps[3].done }] : [],
        blocked: !recent ? "Előbb adj hozzá egy könyvet." : "",
      },
      characters: {
        title: "Szereplőhangok",
        optional: true,
        text: "Szereplőhangos módban a párbeszédeket a szereplők a saját hangjukon mondják, a többit a narrátor olvassa. A próbakönyvben ez azonnal kipróbálható."
          + (s.llm_configured
            ? " Saját könyvnél importáláskor válaszd a „Szereplőhangok” módot."
            : " Saját könyvnél a szereplőket egy nyelvi modell ismeri fel – ezt egyszer be kell állítani."),
        doneText: s.multi_book_id
          ? "Van szereplőhangos könyved. A szereplők hangját a könyv „Hangok” oldalán cserélheted."
          : "Már láttad, hol állíthatók be a szereplőhangok. Bármikor bekapcsolhatod őket egy könyv „Hangok” oldalán.",
        actions: [
          s.multi_book_id
            ? { label: "Szereplők hangjai", href: `/voice-studio/${s.multi_book_id}`, primary: true }
            : demo
              ? { label: "Próbakönyv szereplői", href: `/voice-studio/${demo}`, primary: true }
              : recent
                ? { label: "Hangok oldal megnyitása", href: `/voice-studio/${recent}`, primary: true }
                : { label: "Próbakönyv hozzáadása", action: "demo", primary: true },
          ...(!s.multi_book_id && !demo && recent ? [{ label: "Próbakönyv hozzáadása", action: "demo" }] : []),
          ...(s.llm_configured ? [] : [{ label: "Nyelvi modell beállítása", href: "/settings#characters" }]),
        ],
      },
      export: {
        title: "Hangoskönyv mentése",
        text: "A felolvasást fájlba mentheted, és telefonon, autóban vagy bármilyen lejátszón hallgathatod. A könyvben jobb alul találod: ⬇ Hangoskönyv mentése.",
        doneText: "Már készítettél exportot. A fájlokat a Feladatok oldalon is megtalálod.",
        actions: recent
          ? [{ label: "Hangoskönyv mentése", href: `/reader/${recent}?export=1`, primary: !s.steps[5].done }]
          : [],
        blocked: !recent ? "Előbb adj hozzá egy könyvet." : "",
      },
    };
  }

  const Guide = {
    status: null,

    async init() {
      this.navButton = document.getElementById("nav-guide");
      this.navButton?.addEventListener("click", () => this.open());
      await this.refresh();
      const params = new URLSearchParams(location.search);
      if (params.get("guide") === "1") this.open();
      if (params.get("add") === "1" && onLibraryPage()) {
        // Browsers only open a file chooser from a real click, so point at the button.
        const button = document.getElementById("btn-import");
        button?.focus();
        button?.classList.add("guide-pulse");
        setTimeout(() => button?.classList.remove("guide-pulse"), 4000);
      }
    },

    async refresh() {
      try {
        const response = await fetch("/api/guide/status");
        if (!response.ok) return;
        this.status = await response.json();
      } catch (_) {
        return;
      }
      this.render();
    },

    async post(body) {
      const response = await fetch("/api/guide/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (response.ok) this.status = await response.json();
      this.render();
    },

    markListened() {
      try {
        if (localStorage.getItem("auris-listened")) return;
        localStorage.setItem("auris-listened", "1");
      } catch (_) {}
      this.post({ event: "listened" }).catch(() => {});
    },

    async addDemoBook(button) {
      if (button) {
        button.disabled = true;
        button.textContent = "Próbakönyv hozzáadása…";
      }
      try {
        const response = await fetch("/api/guide/demo-book", { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Nem sikerült.");
        location.href = `/reader/${data.book_id}?welcome=1`;
      } catch (error) {
        alert("A próbakönyv hozzáadása nem sikerült: " + error.message);
        if (button) {
          button.disabled = false;
          button.textContent = "Próbakönyv hozzáadása";
        }
      }
    },

    open() {
      if (!this.status) return;
      const home = document.getElementById("guide-home");
      if (home) {
        if (this.status.dismissed) this.post({ dismissed: false });
        home.hidden = false;
        home.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const drawer = this.drawer();
      drawer.hidden = false;
      this.navButton?.setAttribute("aria-expanded", "true");
      drawer.querySelector(".guide-close")?.focus();
    },

    close() {
      const drawer = document.getElementById("guide-drawer");
      if (drawer) drawer.hidden = true;
      this.navButton?.setAttribute("aria-expanded", "false");
      this.navButton?.focus();
    },

    drawer() {
      let drawer = document.getElementById("guide-drawer");
      if (!drawer) {
        drawer = document.createElement("aside");
        drawer.id = "guide-drawer";
        drawer.className = "guide-drawer";
        drawer.hidden = true;
        drawer.setAttribute("aria-label", "Első lépések");
        document.body.appendChild(drawer);
        drawer.addEventListener("click", (event) => this.handleClick(event));
        document.addEventListener("keydown", (event) => {
          if (event.key === "Escape" && !drawer.hidden) this.close();
        });
      }
      return drawer;
    },

    handleClick(event) {
      const button = event.target.closest("[data-guide-action]");
      if (!button) return;
      const action = button.dataset.guideAction;
      if (action === "demo") this.addDemoBook(button);
      else if (action === "pick-file") document.getElementById("file-input")?.click();
      else if (action === "hide") this.post({ dismissed: true });
      else if (action === "close") this.close();
    },

    stepsHtml(compact) {
      const s = this.status;
      const text = describeSteps(s);
      return s.steps.map((step, index) => {
        const t = text[step.id];
        const isNext = step.id === s.next_step;
        const state = step.done ? "done" : isNext ? "next" : "todo";
        const badge = step.done ? "Kész" : t.optional ? "Nem kötelező" : isNext ? "Most ez jön" : "";
        const actions = (t.blocked && !step.done ? [] : t.actions).map((a) => {
          const cls = `btn btn-sm ${a.primary ? "btn-primary" : "btn-ghost"}`;
          return a.href
            ? `<a class="${cls}" href="${escHtml(a.href)}">${escHtml(a.label)}</a>`
            : `<button type="button" class="${cls}" data-guide-action="${escHtml(a.action)}">${escHtml(a.label)}</button>`;
        }).join("");
        const showDetails = !compact || isNext || !step.done;
        return `<li class="guide-step is-${state}${t.optional ? " is-optional" : ""}">
          <span class="guide-step-number" aria-hidden="true">${step.done ? "✓" : index + 1}</span>
          <div class="guide-step-body">
            <div class="guide-step-head"><h3>${escHtml(t.title)}</h3>${badge ? `<span class="guide-step-badge">${badge}</span>` : ""}</div>
            ${showDetails ? `<p>${escHtml(step.done ? t.doneText : t.text)}</p>` : ""}
            ${t.blocked && !step.done ? `<p class="guide-step-blocked">${escHtml(t.blocked)}</p>` : ""}
            ${actions ? `<div class="guide-step-actions">${actions}</div>` : ""}
          </div>
        </li>`;
      }).join("");
    },

    render() {
      const s = this.status;
      if (!s) return;
      const progress = document.getElementById("nav-guide-progress");
      if (progress) progress.textContent = s.all_completed ? "✓" : `${s.completed_count}/${s.total_count}`;

      const intro = s.all_completed
        ? "Minden alaplépés megvan. Ezt a listát bármikor újra megnyithatod felül, az „Első lépések” gombbal."
        : "Öt egyszerű lépés a hangoskönyvig, plusz egy nem kötelező. A kész lépések maguktól kipipálódnak.";

      const home = document.getElementById("guide-home");
      if (home) {
        home.hidden = s.dismissed;
        home.innerHTML = `<div class="guide-header">
            <div><p class="eyebrow">Első lépések · ${s.completed_count}/${s.total_count} kész</p>
            <h2>Így lesz a könyvedből hangoskönyv</h2><p>${intro}</p></div>
            <button type="button" class="btn btn-sm btn-ghost" data-guide-action="hide">Elrejtés</button>
          </div>
          <ol class="guide-steps">${this.stepsHtml(false)}</ol>`;
        if (!home.dataset.bound) {
          home.addEventListener("click", (event) => this.handleClick(event));
          home.dataset.bound = "1";
        }
      }

      const drawer = document.getElementById("guide-drawer");
      if (drawer || !home) {
        const target = this.drawer();
        target.innerHTML = `<div class="guide-header">
            <div><p class="eyebrow">${s.completed_count}/${s.total_count} kész</p><h2>Első lépések</h2><p>${intro}</p></div>
            <button type="button" class="btn btn-sm btn-ghost guide-close" data-guide-action="close" aria-label="Első lépések bezárása">✕</button>
          </div>
          <ol class="guide-steps is-compact">${this.stepsHtml(true)}</ol>`;
      }
    },

    /** Dismissable “what can I do here?” box at the top of a page. */
    tip(container, key, title, items) {
      if (!container) return;
      let hidden = [];
      try { hidden = JSON.parse(localStorage.getItem(TIP_STORAGE) || "[]"); } catch (_) {}
      if (hidden.includes(key)) {
        container.hidden = true;
        return;
      }
      container.hidden = false;
      container.classList.add("page-tip");
      container.innerHTML = `<div class="page-tip-body"><strong>${escHtml(title)}</strong>
          <ol>${items.map((item) => `<li>${item}</li>`).join("")}</ol></div>
        <button type="button" class="btn btn-sm btn-ghost" aria-label="Tipp elrejtése">Értem</button>`;
      container.querySelector("button").addEventListener("click", () => {
        hidden.push(key);
        try { localStorage.setItem(TIP_STORAGE, JSON.stringify(hidden)); } catch (_) {}
        container.hidden = true;
      });
    },
  };

  const VOICE_WORDS = {
    female: "női", male: "férfi",
    child: "gyermek", teenager: "kamasz", "young adult": "fiatal", "middle-aged": "középkorú", elderly: "idős",
    "very low pitch": "nagyon mély", "low pitch": "mély", "moderate pitch": "közepes",
    "high pitch": "magas", "very high pitch": "nagyon magas",
  };

  /** Plain-language summary of a voice, e.g. "Idős, mély férfihang". */
  Guide.describeVoice = function describeVoice(voice) {
    if (voice?.ref_audio_path || voice?.ref_audio_name) return "Saját felvételből készült hang";
    const parts = String(voice?.instruct || "").split(",").map((p) => p.trim().toLowerCase());
    const gender = parts.includes("female") ? "női" : parts.includes("male") ? "férfi" : "";
    const age = ["child", "teenager", "young adult", "middle-aged", "elderly"].find((a) => parts.includes(a));
    const pitch = Object.keys(VOICE_WORDS).filter((k) => k.endsWith("pitch")).find((p) => parts.includes(p));
    const words = [age && VOICE_WORDS[age], pitch && `${VOICE_WORDS[pitch]} hangú`].filter(Boolean);
    const text = `${words.join(", ")}${words.length ? " " : ""}${gender ? gender + "hang" : "hang"}`;
    return text.charAt(0).toUpperCase() + text.slice(1);
  };

  window.AurisGuide = Guide;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => Guide.init());
  } else {
    Guide.init();
  }
})();
