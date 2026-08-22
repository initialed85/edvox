const VOX_AUDIO_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

// Silero VAD handles speech probability, hysteresis, pre-roll, and endpointing.
// These are deliberately a little more relaxed than the former RMS detector so
// natural pauses do not split an utterance too eagerly.
const VOX_CONFIG = {
  model: 'v5',
  positiveSpeechThreshold: 0.30,
  negativeSpeechThreshold: 0.25,
  redemptionMs: 2000,
  preSpeechPadMs: 800,
  minSpeechMs: 350,
};

const state = {
  busy: false,
  voxEnabled: false,
  voxPhase: 'off',
  vad: null,
  vadStarting: false,
  voxPausedForWork: false,
  autonomousAfterSeconds: 10,
  autonomousIntervalSeconds: 10,
  autonomousTimer: null,
  autonomousInFlight: false,
  wakeLock: null,
};

const orb = document.getElementById('status-orb');
const status = document.getElementById('status');
const errorBox = document.getElementById('error');

function setPhase(phase, message) {
  state.voxPhase = phase;
  orb.className = `status-orb ${phase}`;
  orb.setAttribute('aria-label', phase === 'off' || phase === 'error' ? 'Start listening' : 'Listening');
  status.textContent = message;
}

function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  errorBox.textContent = message;
  errorBox.hidden = false;
  setPhase('error', 'Microphone unavailable');
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function microphoneError(error) {
  if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') {
    if (window.isSecureContext === false) {
      return new Error('Microphone access requires HTTPS. Open the secure voice URL, or use localhost.');
    }
    return new Error('Microphone permission was denied. Allow microphone access for this site and tap the light to retry.');
  }
  if (error?.name === 'NotFoundError') return new Error('No microphone was found on this device.');
  if (error?.name === 'NotReadableError') return new Error('The microphone is busy or unavailable.');
  if (!navigator.mediaDevices?.getUserMedia) {
    return new Error('Microphone access requires HTTPS. Open the secure voice URL, or use localhost.');
  }
  return error instanceof Error ? error : new Error(String(error));
}

async function getMicrophone() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw microphoneError();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: VOX_AUDIO_CONSTRAINTS});
    stream.getAudioTracks().forEach((track) => track.applyConstraints(VOX_AUDIO_CONSTRAINTS).catch(() => {}));
    return stream;
  } catch (error) {
    throw microphoneError(error);
  }
}

function encodeWav(samples, sampleRate = 16000) {
  const pcm = samples instanceof Float32Array ? samples : new Float32Array(samples);
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + pcm.length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, pcm.length * 2, true);
  for (let index = 0; index < pcm.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, pcm[index]));
    view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([buffer], {type: 'audio/wav'});
}

async function destroyVad() {
  const detector = state.vad;
  state.vad = null;
  if (!detector) return;
  try { await detector.destroy(); } catch (_) { /* Startup failures can leave a partial detector. */ }
}

async function createVad() {
  if (!window.vad?.MicVAD) {
    throw new Error('The voice detector failed to load. Refresh the page and try again.');
  }
  return window.vad.MicVAD.new({
    model: VOX_CONFIG.model,
    positiveSpeechThreshold: VOX_CONFIG.positiveSpeechThreshold,
    negativeSpeechThreshold: VOX_CONFIG.negativeSpeechThreshold,
    redemptionMs: VOX_CONFIG.redemptionMs,
    preSpeechPadMs: VOX_CONFIG.preSpeechPadMs,
    minSpeechMs: VOX_CONFIG.minSpeechMs,
    startOnLoad: false,
    baseAssetPath: '/vendor/vad/',
    onnxWASMBasePath: '/vendor/ort/',
    getStream: getMicrophone,
    resumeStream: getMicrophone,
    onSpeechStart: () => {
      if (state.voxEnabled && !state.busy) setPhase('recording', 'Listening…');
    },
    onSpeechRealStart: () => {
      if (state.voxEnabled && !state.busy) setPhase('recording', 'Listening…');
    },
    onVADMisfire: () => {
      if (state.voxEnabled && !state.busy) setPhase('active', 'Listening…');
    },
    onSpeechEnd: (audio) => {
      if (state.voxEnabled && !state.busy) void handleAudio(audio);
    },
  });
}

async function pauseVoxForWork() {
  if (!state.voxEnabled || state.voxPausedForWork) return false;
  state.voxPausedForWork = true;
  state.busy = true;
  setPhase('processing', 'Thinking…');
  if (state.vad?.listening) await state.vad.pause();
  return true;
}

async function resumeVoxAfterWork() {
  if (!state.voxEnabled || !state.voxPausedForWork) return;
  state.voxPausedForWork = false;
  try {
    if (!state.vad) state.vad = await createVad();
    await state.vad.start();
    state.busy = false;
    if (state.voxEnabled) setPhase('active', 'Listening…');
  } catch (error) {
    state.busy = false;
    state.voxEnabled = false;
    await destroyVad();
    showError(error);
  }
}

async function waitForPlayback(audio) {
  if (!audio) return;
  if (audio.paused) {
    try { await audio.play(); } catch (_) { return; }
  }
  if (audio.ended) return;
  await new Promise((resolve) => {
    let timer;
    const done = () => {
      clearTimeout(timer);
      audio.removeEventListener('ended', done);
      audio.removeEventListener('error', done);
      resolve();
    };
    timer = setTimeout(done, 120000);
    audio.addEventListener('ended', done, {once: true});
    audio.addEventListener('error', done, {once: true});
  });
}

async function handleAudio(audioSamples) {
  if (state.busy || !state.voxEnabled) return;
  const paused = await pauseVoxForWork();
  try {
    const form = new FormData();
    form.append('audio', encodeWav(audioSamples), 'microphone.wav');
    const result = await request('/api/voice-turn', {
      method: 'POST',
      headers: {'X-Voice-Chat-Mode': 'conversation'},
      body: form,
    });
    if (result.ignored || result.no_reply) return;
    if (result.audio_url) {
      setPhase('speaking', 'Speaking…');
      await waitForPlayback(new Audio(result.audio_url));
    }
  } catch (error) {
    showError(error);
  } finally {
    if (paused && state.voxEnabled) await resumeVoxAfterWork();
    else state.busy = false;
    if (state.voxEnabled && state.voxPhase !== 'error') setPhase('active', 'Listening…');
    scheduleAutonomousCheck();
  }
}

async function runAutonomousCheck() {
  state.autonomousTimer = null;
  if (state.busy || !state.voxEnabled || state.voxPhase !== 'active' || state.autonomousInFlight) return;
  state.autonomousInFlight = true;
  let paused = false;
  try {
    paused = await pauseVoxForWork();
    const result = await request('/api/autonomous-turn', {
      method: 'POST',
      headers: {'X-Voice-Chat-Mode': 'conversation'},
    });
    if (!result.skipped && !result.no_reply && result.audio_url) {
      setPhase('speaking', 'Speaking…');
      await waitForPlayback(new Audio(result.audio_url));
    }
  } catch (error) {
    showError(error);
  } finally {
    state.autonomousInFlight = false;
    if (paused && state.voxEnabled) await resumeVoxAfterWork();
    scheduleAutonomousCheck();
  }
}

function scheduleAutonomousCheck() {
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
  state.autonomousTimer = null;
  if (state.busy || !state.voxEnabled || state.voxPhase !== 'active') return;
  state.autonomousTimer = setTimeout(runAutonomousCheck, Math.max(1, state.autonomousIntervalSeconds) * 1000);
}

async function requestWakeLock() {
  if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
  if (state.wakeLock && !state.wakeLock.released) return;
  try {
    state.wakeLock = await navigator.wakeLock.request('screen');
    state.wakeLock.addEventListener('release', () => {
      state.wakeLock = null;
      if (document.visibilityState === 'visible') setTimeout(() => requestWakeLock().catch(() => {}), 250);
    }, {once: true});
  } catch (_) { /* Screen Wake Lock is deliberately best effort. */ }
}

async function enableVox() {
  if (state.vadStarting || state.voxEnabled) return;
  errorBox.hidden = true;
  state.vadStarting = true;
  state.voxEnabled = true;
  setPhase('processing', 'Loading voice detector…');
  try {
    state.vad = await createVad();
    await state.vad.start();
    if (!state.voxEnabled) return;
    setPhase('active', 'Listening…');
    scheduleAutonomousCheck();
  } catch (error) {
    state.voxEnabled = false;
    await destroyVad();
    showError(error);
  } finally {
    state.vadStarting = false;
  }
}

function disableVox() {
  state.voxEnabled = false;
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
  state.autonomousTimer = null;
  const detector = state.vad;
  state.vad = null;
  state.vadStarting = false;
  state.voxPausedForWork = false;
  state.busy = false;
  if (detector) {
    detector.pause().catch(() => {});
    detector.destroy().catch(() => {});
  }
  setPhase('off', 'Tap the light to listen');
}

orb.addEventListener('click', () => {
  // Conversation mode stays locked to VOX. The light is only a permission
  // retry affordance once startup failed; it is not an on/off control.
  if (!state.voxEnabled) enableVox().catch(showError);
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') requestWakeLock().catch(() => {});
});
window.addEventListener('beforeunload', () => {
  disableVox();
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
});

async function boot() {
  try {
    const session = await request('/api/session');
    state.autonomousAfterSeconds = Number(session.autonomous_after_seconds || 10);
    state.autonomousIntervalSeconds = Number(session.autonomous_interval_seconds || state.autonomousAfterSeconds || 10);
    await request('/api/health');
    await requestWakeLock();
    await enableVox();
  } catch (error) {
    showError(error);
  }
}

setPhase('processing', 'Starting…');
boot();
