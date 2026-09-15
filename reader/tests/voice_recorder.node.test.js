const test = require('node:test');
const assert = require('node:assert/strict');

const {
  RECORDING_SCRIPT,
  analyseRecording,
  encodeWav,
  joinChunks,
  micErrorMessage,
  microphoneOptions,
  microphoneSupport,
  normalise,
} = require('../static/js/voice_recorder.js');

function tone(seconds, rate, amplitude) {
  const samples = new Float32Array(Math.round(seconds * rate));
  for (let i = 0; i < samples.length; i++) samples[i] = amplitude * Math.sin((2 * Math.PI * 220 * i) / rate);
  return samples;
}

test('reading script fits a 3-10 second reference and covers Hungarian vowels', () => {
  const words = RECORDING_SCRIPT.trim().split(/\s+/);
  assert.ok(words.length >= 15 && words.length <= 25, `${words.length} words`);
  for (const letter of ['a', 'á', 'e', 'é', 'i', 'í', 'o', 'ó', 'ö', 'ő', 'u', 'ú', 'ü', 'ű']) {
    assert.ok(RECORDING_SCRIPT.toLowerCase().includes(letter), `missing ${letter}`);
  }
  assert.match(RECORDING_SCRIPT, /\?$/);
});

test('WAV header and samples are 16-bit mono PCM', () => {
  const wav = new DataView(encodeWav(Float32Array.from([0, 1, -1, 0.5]), 48000));
  const text = (offset, length) => String.fromCharCode(...Array.from({ length }, (_, i) => wav.getUint8(offset + i)));
  assert.equal(text(0, 4), 'RIFF');
  assert.equal(text(8, 4), 'WAVE');
  assert.equal(wav.getUint16(20, true), 1);
  assert.equal(wav.getUint16(22, true), 1);
  assert.equal(wav.getUint32(24, true), 48000);
  assert.equal(wav.getUint16(34, true), 16);
  assert.equal(wav.getUint32(40, true), 8);
  assert.deepEqual([44, 46, 48, 50].map((o) => wav.getInt16(o, true)), [0, 32767, -32768, 16383]);
});

test('analysis flags silence, short, quiet and clipped recordings', () => {
  const rate = 16000;
  assert.equal(analyseRecording(new Float32Array(rate * 5), rate).silent, true);
  assert.equal(analyseRecording(tone(1, rate, 0.3), rate).tooShort, true);

  const quiet = analyseRecording(tone(5, rate, 0.03), rate);
  assert.equal(quiet.silent, false);
  assert.equal(quiet.tooQuiet, true);

  const loud = analyseRecording(tone(5, rate, 1.4).map((v) => Math.max(-1, Math.min(1, v))), rate);
  assert.equal(loud.tooLoud, true);

  const good = analyseRecording(tone(8, rate, 0.4), rate);
  assert.deepEqual(
    [good.silent, good.tooShort, good.tooQuiet, good.tooLoud],
    [false, false, false, false],
  );
  assert.ok(Math.abs(good.seconds - 8) < 0.001);
});

test('normalise lifts quiet takes at most +12 dB and never touches loud ones', () => {
  const quiet = tone(1, 8000, 0.05);
  const lifted = normalise(quiet, 0.05);
  assert.ok(Math.abs(Math.max(...lifted) - 0.2) < 0.001); // capped at 4x
  const medium = normalise(tone(1, 8000, 0.35), 0.35);
  assert.ok(Math.abs(Math.max(...medium) - 0.7) < 0.001);
  const loud = tone(1, 8000, 0.9);
  assert.equal(normalise(loud, 0.9), loud);
});

test('chunks join in order', () => {
  assert.deepEqual(Array.from(joinChunks([Float32Array.from([1, 2]), Float32Array.from([3])])), [1, 2, 3]);
});

test('microphone errors map to actionable Hungarian messages', () => {
  const named = (name) => Object.assign(new Error(name), { name });
  assert.match(micErrorMessage(named('NotAllowedError')), /engedélyezd a mikrofont/);
  assert.match(micErrorMessage(named('NotFoundError')), /Nem található mikrofon/);
  assert.match(micErrorMessage(named('NoMicrophoneError')), /Nem található mikrofon/);
  assert.match(micErrorMessage(named('NotReadableError')), /másik program/);
  assert.match(micErrorMessage(named('OverconstrainedError')), /Válassz másikat/);
  assert.match(micErrorMessage(named('DeviceLostError')), /megszakadt/);
  assert.match(micErrorMessage(named('InsecureContextError')), /127\.0\.0\.1/);
  assert.match(micErrorMessage(new Error('weird')), /nem sikerült elindítani/);
});

test('support check catches insecure pages and missing APIs', () => {
  assert.equal(microphoneSupport({ isSecureContext: false }).name, 'InsecureContextError');
  assert.equal(microphoneSupport({ isSecureContext: true, navigator: {} }).name, 'UnsupportedError');
  assert.equal(
    microphoneSupport({ isSecureContext: true, navigator: { mediaDevices: { getUserMedia() {} } }, AudioContext: function () {} }),
    null,
  );
});

test('device list keeps only inputs and labels unnamed ones', () => {
  const options = microphoneOptions([
    { kind: 'audiooutput', deviceId: 'speaker', label: 'Hangszóró' },
    { kind: 'audioinput', deviceId: 'default', label: '' },
    { kind: 'audioinput', deviceId: 'usb', label: 'USB mikrofon' },
    { kind: 'audioinput', deviceId: 'x', label: '' },
  ]);
  assert.deepEqual(options, [
    { id: 'default', label: 'Alapértelmezett mikrofon' },
    { id: 'usb', label: 'USB mikrofon' },
    { id: 'x', label: 'Mikrofon 3' },
  ]);
});
