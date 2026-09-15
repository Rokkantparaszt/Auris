/**
 * Microphone recording for voice cloning.
 *
 * Audio is captured with the Web Audio API and encoded to 16-bit mono WAV in
 * the browser, so the server needs no extra codec (MediaRecorder would give
 * WebM/OGG/MP4 depending on the browser). The finished File goes through the
 * same upload → pause-aware trim → Whisper flow as an uploaded recording.
 */
(function (root) {
  // About 8 seconds read aloud: all Hungarian vowel lengths incl. ő/ű, a
  // statement and a question for varied intonation, and a natural pause.
  const RECORDING_SCRIPT =
    "Hűvös szél fújt, amikor leültem az ablak mellé a régi, sárga könyvvel. " +
    "Vajon ki írta bele előttem a nevét, és hová utazott azóta?";

  const MAX_RECORD_SECONDS = 20;
  const MIN_RECORD_SECONDS = 2;
  const SILENT_RMS_DB = -50;     // quieter than this on average: nothing was heard
  const QUIET_PEAK_DB = -24;     // loudest moment below this: too far or too quiet
  const CLIP_LEVEL = 0.99;
  const CLIPPED_RATIO = 0.002;   // share of samples at full scale that sounds distorted
  const TARGET_PEAK = 0.7;       // about -3 dBFS after gentle normalisation
  const MAX_GAIN = 4;            // never boost more than +12 dB

  function toDb(value) {
    return 20 * Math.log10(Math.max(value, 1e-9));
  }

  /** Loudness facts about a finished recording. */
  function analyseRecording(samples, sampleRate) {
    let sumSquares = 0;
    let peak = 0;
    let clipped = 0;
    for (let i = 0; i < samples.length; i++) {
      const value = Math.abs(samples[i]);
      sumSquares += value * value;
      if (value > peak) peak = value;
      if (value >= CLIP_LEVEL) clipped++;
    }
    const rms = samples.length ? Math.sqrt(sumSquares / samples.length) : 0;
    const seconds = sampleRate ? samples.length / sampleRate : 0;
    return {
      seconds,
      rmsDb: toDb(rms),
      peakDb: toDb(peak),
      peak,
      clippedRatio: samples.length ? clipped / samples.length : 0,
      silent: toDb(rms) < SILENT_RMS_DB,
      tooQuiet: toDb(peak) < QUIET_PEAK_DB,
      tooLoud: samples.length ? clipped / samples.length > CLIPPED_RATIO : false,
      tooShort: seconds < MIN_RECORD_SECONDS,
    };
  }

  /** Raise quiet recordings towards -3 dBFS without ever clipping. */
  function normalise(samples, peak) {
    if (!peak || peak >= TARGET_PEAK) return samples;
    const gain = Math.min(MAX_GAIN, TARGET_PEAK / peak);
    const out = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) out[i] = samples[i] * gain;
    return out;
  }

  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const text = (offset, value) => {
      for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
    };
    text(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    text(8, "WAVE");
    text(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);             // PCM
    view.setUint16(22, 1, true);             // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    text(36, "data");
    view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const value = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(44 + i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    }
    return buffer;
  }

  function joinChunks(chunks) {
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const out = new Float32Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }

  /** Plain-language explanation for getUserMedia and device failures. */
  function micErrorMessage(error) {
    const name = error?.name || "";
    if (name === "InsecureContextError") {
      return "Ezen a címen a böngésző nem engedi a mikrofont. Nyisd meg az Aurist a http://127.0.0.1:7860 címen, vagy tölts fel egy hangfájlt.";
    }
    if (name === "UnsupportedError") {
      return "Ez a böngésző nem tud hangot felvenni. Próbáld Chrome, Edge vagy Firefox böngészővel, vagy tölts fel egy hangfájlt.";
    }
    if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
      return "A mikrofon használata le van tiltva. Kattints a címsor bal oldalán a lakat (vagy mikrofon) ikonra, engedélyezd a mikrofont, majd kattints újra a „Mikrofon bekapcsolása” gombra.";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "NoMicrophoneError") {
      return "Nem található mikrofon. Csatlakoztass egyet (vagy kapcsold be a fejhallgatód mikrofonját), és kattints az „Újrakeresés” gombra.";
    }
    if (name === "OverconstrainedError") {
      return "A kiválasztott mikrofon már nem érhető el. Válassz másikat a listából.";
    }
    if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
      return "A mikrofont most nem lehet használni – valószínűleg egy másik program (például Teams, Zoom vagy Discord) foglalja. Zárd be, majd próbáld újra.";
    }
    if (name === "DeviceLostError") {
      return "A mikrofon kapcsolata megszakadt (kihúzták vagy kikapcsolt). Csatlakoztasd újra, és vedd fel még egyszer.";
    }
    return "A mikrofont nem sikerült elindítani. Ellenőrizd, hogy csatlakoztatva van-e, és próbáld újra.";
  }

  function namedError(name) {
    const error = new Error(name);
    error.name = name;
    return error;
  }

  /** Browser support check before asking for permission. */
  function microphoneSupport(env = root) {
    if (env.isSecureContext === false) return namedError("InsecureContextError");
    const devices = env.navigator?.mediaDevices;
    if (!devices?.getUserMedia || !(env.AudioContext || env.webkitAudioContext)) {
      return namedError("UnsupportedError");
    }
    return null;
  }

  function microphoneOptions(devices) {
    return devices
      .filter((device) => device.kind === "audioinput")
      .map((device, index) => ({
        id: device.deviceId,
        label: device.label || (device.deviceId === "default" || index === 0 ? "Alapértelmezett mikrofon" : `Mikrofon ${index + 1}`),
      }));
  }

  // Capture tap: forwards each audio block (mixed to mono) to the page.
  const WORKLET_SOURCE = `
    class AurisRecorderTap extends AudioWorkletProcessor {
      process(inputs) {
        const channels = inputs[0];
        if (channels && channels.length) {
          const mono = new Float32Array(channels[0].length);
          for (const channel of channels) {
            for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / channels.length;
          }
          this.port.postMessage(mono, [mono.buffer]);
        }
        return true;
      }
    }
    registerProcessor("auris-recorder-tap", AurisRecorderTap);
  `;

  class MicrophoneRecorder {
    constructor({ onLevel, onDeviceLost } = {}) {
      this.onLevel = onLevel || (() => {});
      this.onDeviceLost = onDeviceLost || (() => {});
      this.stream = null;
      this.context = null;
      this.chunks = [];
      this.recording = false;
      this.meterFrame = null;
    }

    get active() {
      return Boolean(this.stream);
    }

    async listMicrophones() {
      const devices = await root.navigator.mediaDevices.enumerateDevices();
      return microphoneOptions(devices);
    }

    /** Ask for permission and start the live level meter. */
    async open(deviceId) {
      const unsupported = microphoneSupport();
      if (unsupported) throw unsupported;
      await this.close();
      const available = await this.listMicrophones().catch(() => []);
      if (!available.length) throw namedError("NoMicrophoneError");

      const audio = {
        channelCount: 1,
        // Raw voice is best for cloning: no filtering that colours the timbre.
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      };
      if (deviceId) audio.deviceId = { exact: deviceId };
      this.stream = await root.navigator.mediaDevices.getUserMedia({ audio });
      const [track] = this.stream.getAudioTracks();
      track?.addEventListener("ended", () => this.handleDeviceLost());

      const Context = root.AudioContext || root.webkitAudioContext;
      this.context = new Context();
      if (this.context.state === "suspended") await this.context.resume();
      this.source = this.context.createMediaStreamSource(this.stream);
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 2048;
      this.source.connect(this.analyser);
      await this.attachTap();
      this.startMeter();
      return track?.getSettings?.().deviceId || deviceId || "";
    }

    async attachTap() {
      const silent = this.context.createGain();
      silent.gain.value = 0; // keeps the tap processing without audible monitoring
      silent.connect(this.context.destination);
      if (this.context.audioWorklet && root.AudioWorkletNode) {
        const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
        try {
          await this.context.audioWorklet.addModule(url);
        } finally {
          URL.revokeObjectURL(url);
        }
        this.tap = new root.AudioWorkletNode(this.context, "auris-recorder-tap");
        this.tap.port.onmessage = (event) => {
          if (this.recording) this.chunks.push(event.data);
        };
      } else {
        this.tap = this.context.createScriptProcessor(4096, 1, 1);
        this.tap.onaudioprocess = (event) => {
          if (this.recording) this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
        };
      }
      this.source.connect(this.tap);
      this.tap.connect(silent);
    }

    startMeter() {
      const data = new Float32Array(this.analyser.fftSize);
      const tick = () => {
        if (!this.analyser) return;
        this.analyser.getFloatTimeDomainData(data);
        let sum = 0;
        let peak = 0;
        for (const value of data) {
          sum += value * value;
          peak = Math.max(peak, Math.abs(value));
        }
        this.onLevel({ rmsDb: toDb(Math.sqrt(sum / data.length)), peak });
        this.meterFrame = root.requestAnimationFrame(tick);
      };
      tick();
    }

    start() {
      if (!this.stream) throw namedError("NotFoundError");
      this.chunks = [];
      this.startedAt = root.performance.now();
      this.recording = true;
    }

    elapsed() {
      return this.recording ? (root.performance.now() - this.startedAt) / 1000 : 0;
    }

    /** Stop recording; returns { file, analysis } or throws a named error. */
    stop() {
      this.recording = false;
      const rate = this.context?.sampleRate || 48000;
      const samples = joinChunks(this.chunks);
      this.chunks = [];
      const analysis = analyseRecording(samples, rate);
      const wav = encodeWav(normalise(samples, analysis.peak), rate);
      const file = new File([wav], "Saját felvétel.wav", { type: "audio/wav" });
      return { file, analysis };
    }

    handleDeviceLost() {
      const wasRecording = this.recording;
      this.recording = false;
      this.close();
      this.onDeviceLost(namedError("DeviceLostError"), wasRecording);
    }

    async close() {
      this.recording = false;
      if (this.meterFrame) root.cancelAnimationFrame(this.meterFrame);
      this.meterFrame = null;
      this.analyser = null;
      this.stream?.getTracks().forEach((track) => track.stop());
      this.stream = null;
      if (this.context && this.context.state !== "closed") {
        await this.context.close().catch(() => {});
      }
      this.context = null;
    }
  }

  const api = {
    RECORDING_SCRIPT,
    MAX_RECORD_SECONDS,
    MIN_RECORD_SECONDS,
    analyseRecording,
    encodeWav,
    joinChunks,
    micErrorMessage,
    microphoneOptions,
    microphoneSupport,
    normalise,
    MicrophoneRecorder,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AurisRecorder = api;
})(typeof window !== "undefined" ? window : globalThis);
