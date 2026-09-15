# Auris – kezdő felhasználói útmutató és felület (ONBOARDING.md)

Cél: egy technikai tudás nélküli felhasználó is magától eljusson a saját hangoskönyvéig.
Minden szöveg magyar, hétköznapi nyelven szól; a technikai beállítások „Haladó” panelek mögött maradnak.

## A felhasználói út

| # | Lépés | Hol | Mikor „Kész” (valós állapotból számolva) |
|---|-------|-----|------------------------------------------|
| 1 | Beszédmotor telepítése | Beállítások → Gyors beállítás | A kiválasztott motor modellfájljai a gépen vannak, és a motor nincs hibaállapotban |
| 2 | Hangok kiválasztása | Hangok (`/voices`) | Legalább egy hang van a „Hangjaim” között |
| 3 | Könyv hozzáadása | Könyvtár | Van legalább egy könyv (saját vagy próbakönyv) |
| 4 | Meghallgatás egy narrátorral | Olvasó | A böngésző ténylegesen lejátszott hangot, vagy van generált hangszakasz |
| 5 | Szereplőhangok *(nem kötelező)* | Könyv → 🎙 Hangok | Van szereplőhangos módban lévő könyv felismert szereplőkkel |
| 6 | Hangoskönyv mentése | Olvasó → ⬇ Hangoskönyv mentése | Van sikeresen befejezett export feladat |

A lépések állapotát a `GET /api/guide/status` adja. Nincs kattintás-alapú „pipálás”, ezért újraindítás,
visszaállítás vagy törlés után is helyes marad. Az egyetlen tárolt esemény a `listened`
(a lejátszás ténye), mert ezt az adatbázis nem mutatja.

## Felületi elemek

- **Első lépések** (felső sáv): a Könyvtárban beágyazott kártyasor, máshol lenyíló panel. Minden lépésnél
  egy mondatnyi magyarázat és egy gomb, ami ténylegesen elvégzi a teendőt (oldal megnyitása, próbakönyv
  hozzáadása, export panel megnyitása). Elrejthető (`guide_dismissed`), a felső gombbal visszahozható.
- **Beszédmotor-jelzés** (jobb felül): érthető állapotszöveg, kattintásra a Gyors beállításra visz.
  Ha lejátszáskor a motor nem kész, az API magyar magyarázatot ad (`setup_needed`), az olvasó pedig
  „Beszédmotor beállítása” gombot mutat a nyers „Model not ready” helyett.
- **Hangok oldal** (`/voices`): Hangjaim · 8 beépített hang (meghallgatás, egy kattintással hozzáadás) ·
  új hang összeállítása (nem, életkor, hangmagasság) · hang egy felvételből (WAV + szó szerinti átirat) ·
  `.aurisvoice` betöltés.
- **Könyv Hangok oldala** (`/voice-studio/<id>`): 1) mód: Egy narrátor / Szereplőhangok (azonnal ment),
  2) narrátor, 3) szereplők. Mindenhol ugyanaz a sor: *Válassz hangot → ▶ Meghallgatás → Ezt használom*.
  A referenciahang, finomhangolás és profilkezelés a „Haladó” panelben van. Ha szereplőhangos módban
  nincs felismert szereplő, a panel elmondja, mi kell hozzá (nyelvi modell), és gombot ad a beállításhoz
  vagy a felismerés indításához.
- **Import**: a „Szereplőhangok” mód nyelvi modell nélkül le van tiltva, magyarázattal és beállítás-linkkel
  (nem utólag hibázik). A narrátor hangja már importáláskor kiválasztható és meghallgatható. Import után
  „Megnyitás és meghallgatás” gomb.
- **Olvasó**: egyszeri, elrejthető tipp (lejátszás, Hangok, mentés), kiemelt „🎙 Hangok” és
  „⬇ Hangoskönyv mentése” gomb. A mentés panel három érthető választást kínál (teljes könyv M4B,
  fejezetenként MP3, csak ez a fejezet WAV); a részletes beállítások összecsukva. FFmpeg nélkül az MP3/M4B
  választás tiltott, magyarázattal.
- **Gyors beállítás**: sorszámozott ellenőrzőlista élő állapottal – letöltés egy kattintással az
  alapértelmezett helyre, motor indítása, próbahang, minőség, FFmpeg, nyelvi modell. Ez a Beállítások
  alapértelmezett lapja.
- **Súgó → Kezdőknek**: a teljes út és gyakori kérdések egyszerű nyelven (`/docs#beginners`).

## Saját hang mikrofonnal

A Hangok oldal „Hang egy felvételből” kártyáján a forrás lehet **mikrofon** vagy **fájl**.
A mikrofonos felvétel a böngészőben készül (Web Audio → 16 bites mono WAV, `static/js/voice_recorder.js`),
így a szervernek nem kell WebM/OGG/MP4 kodek. A kész WAV ugyanazon az úton megy tovább, mint egy feltöltött
fájl: `POST /api/voices/reference` (szünetnél vágás) → Whisper-átirat → szerkesztés → mentés.

- Felolvasandó szöveg (kb. 9 mp, minden magyar magánhangzó, kijelentés + kérdés):
  *„Hűvös szél fújt, amikor leültem az ablak mellé a régi, sárga könyvvel. Vajon ki írta bele előttem a nevét, és hová utazott azóta?”*
- Két lépés: **Mikrofon bekapcsolása** (engedély, eszközlista, élő hangerőcsík), majd **Felvétel indítása**;
  20 mp után magától leáll. Felvétel után a mikrofon felszabadul.
- A böngésző hangszűrői (zajszűrés, visszhangszűrés, automatikus erősítés) ki vannak kapcsolva, mert
  torzítják a hangszínt; a halk felvételt legfeljebb +12 dB-lel erősítjük.
- Az átirat Whisperből jön, tehát az olvasási hibák is benne vannak – a felhasználó csak ellenőriz.
  Whisper nélkül a mintaszöveg kerül a mezőbe, figyelmeztetéssel.
- Kezelt hibák: nincs mikrofon, letiltott engedély (útmutatóval), foglalt mikrofon (Teams/Zoom),
  eltűnt kiválasztott eszköz, kihúzás felvétel közben, nem biztonságos cím / nem támogatott böngésző,
  túl rövid, néma (rossz vagy némított mikrofon), túl halk, túl hangos (torzítás).

## Próbakönyv

`reader/fixtures/demo_book.json`: eredeti, rövid magyar történet („A kék esernyő”), narrátorral és három
szereplővel (Nagyapa, Panni, Feri). Csak kérésre jön létre (`POST /api/guide/demo-book`), soha nem
automatikusan. Valódi TXT forrásfájllal importálódik, ezért minden funkció működik rajta, és a szokásos
Eltávolítás törli. A beszélő-hozzárendelés a `speakers` listából, a párbeszéd-fordulók sorrendjében
készül, így **nyelvi modell nélkül** is azonnal kipróbálható a szereplőhangos felolvasás.
A `tests/test_onboarding.py` ellenőrzi, hogy a lista hossza egyezik a feldolgozó fordulóival.

## Korábbi helykitöltő minták eltávolítása

Egy korábbi vázlat induláskor automatikusan „mintakönyveket” szúrt be (`sample://` forrás, beszéd helyett
zümmögő hang, érvénytelen hangleírású profilok). Induláskor a `cleanup_legacy_samples()` ezeket törli;
valódi könyvekhez, hangokhoz és a próbakönyvhöz nem nyúl.

## API

| Végpont | Leírás |
|---------|--------|
| `GET /api/guide/status` | Lépések, következő lépés, motor/FFmpeg/nyelvi modell állapot |
| `POST /api/guide/state` | `{dismissed}` vagy `{event: "listened"}` |
| `POST /api/guide/demo-book` | Próbakönyv létrehozása (idempotens) |
| `GET /api/voices/builtin` | Beépített hangok |
| `POST /api/voices/reference` | Felvétel (WAV/MP3/M4A/OGG) előkészítése: hossz mérése, első 10 mp, mono WAV; `token`-t ad |
| `GET /api/voices/reference/<token>.wav` | Az előkészített hangminta meghallgatása |
| `POST /api/voices/reference/<token>/transcribe` | Offline átirat Whisperrel (`openai/whisper-large-v3-turbo`, magyar); 409, ha a modell még nincs letöltve |
| `GET /api/stt/status` · `POST /api/stt/download` | A beszédfelismerő állapota és egyszeri letöltése (kb. 1,6 GB, Hugging Face cache) |
| `POST /api/voices/preview` | Próbahang: `builtin_id`, `profile_id`, `instruct`, vagy `reference_token` + `ref_text` |
| `POST /api/voices` | Hang mentése a Hangjaim közé (beépítettből, összeállítva vagy `reference_token`-nel felvételből) |
| `POST /api/books/<id>/voice-assign` | Narrátor (`char_id` nélkül) vagy szereplő hangjának beállítása |

## Kézi ellenőrzés (üres könyvtárral)

1. Könyvtár: látszik az Első lépések kártyasor, a következő lépés kiemelve.
2. Hangok: egy beépített hang meghallgatása és hozzáadása → a 2. lépés kész.
3. Próbakönyv hozzáadása → az olvasó nyílik, megjelenik a tipp.
4. Lejátszás: narrátor, majd „Nagyapa” saját hangon → 4. és 5. lépés kész.
5. 🎙 Hangok: narrátorhang cseréje, mód váltása oda-vissza (újratöltés után is megmarad).
6. Saját TXT importálása nyelvi modell nélkül: a Szereplőhangok opció tiltva, magyarázattal; választott
   narrátorhang beállítódik.
7. Hangoskönyv mentése → Csak ez a fejezet → az export elkészül → minden lépés kész.
