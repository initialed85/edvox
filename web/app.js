const VOX_AUDIO_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

const VOX_CONFIG = {
  calibrationMs: 800,
  minSpeechMs: 300,
  silenceMs: 1400,
  continuationMs: 1800,
  backoffMs: 700,
  maxUtteranceMs: 30000,
  startFloor: 0.012,
  minStartRms: 0.025,
  startMultiplier: 2.5,
  startMargin: 0.012,
  speechMultiplier: 1.6,
  speechMargin: 0.006,
};

const state = {
  busy: false,
  voxEnabled: false,
  voxPhase: 'off',
  voxStream: null,
  voxContext: null,
  voxAnalyser: null,
  voxData: null,
  voxAnimationFrame: null,
  voxStartedAt: 0,
  voxNoiseFloor: VOX_CONFIG.startFloor,
  voxCalibrated: false,
  voxRecorder: null,
  voxChunks: [],
  voxCandidateSince: 0,
  voxLastSpeechAt: 0,
  voxBackoffUntil: 0,
  voxContinuationUntil: 0,
  voxSubmit: false,
  voxPlaybackPaused: false,
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

function recorderOptions() {
  return MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? {mimeType: 'audio/webm;codecs=opus'} : {};
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

async function getMicrophone(constraints) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw microphoneError();
  try {
    return await navigator.mediaDevices.getUserMedia(constraints);
  } catch (error) {
    throw microphoneError(error);
  }
}

function voxThresholds() {
  return {
    start: Math.max(VOX_CONFIG.minStartRms, state.voxNoiseFloor * VOX_CONFIG.startMultiplier + VOX_CONFIG.startMargin),
    speech: Math.max(0.018, state.voxNoiseFloor * VOX_CONFIG.speechMultiplier + VOX_CONFIG.speechMargin),
  };
}

function rmsFromAnalyser() {
  state.voxAnalyser.getByteTimeDomainData(state.voxData);
  let sum = 0;
  for (const value of state.voxData) {
    const sample = (value - 128) / 128;
    sum += sample * sample;
  }
  return Math.sqrt(sum / state.voxData.length);
}

function startVoxRecorder(now) {
  if (!state.voxEnabled || state.voxRecorder || state.busy) return;
  let recorder;
  try {
    recorder = new MediaRecorder(state.voxStream, recorderOptions());
    state.voxChunks = [];
    state.voxRecorder = recorder;
    state.voxCandidateSince = now;
    state.voxLastSpeechAt = now;
    state.voxBackoffUntil = now + VOX_CONFIG.backoffMs;
    state.voxSubmit = false;
    recorder.ondataavailable = (event) => event.data.size && state.voxChunks.push(event.data);
    recorder.onerror = () => {
      state.voxSubmit = false;
      if (recorder.state === 'recording') recorder.stop();
      showError(new Error('Browser audio recording failed.'));
    };
    recorder.onstop = () => {
      const blob = new Blob(state.voxChunks, {type: recorder.mimeType || 'audio/webm'});
      if (state.voxRecorder === recorder) state.voxRecorder = null;
      state.voxChunks = [];
      const submit = state.voxSubmit;
      state.voxSubmit = false;
      state.voxCandidateSince = 0;
      state.voxLastSpeechAt = 0;
      state.voxBackoffUntil = 0;
      state.voxContinuationUntil = 0;
      if (submit && blob.size && state.voxEnabled) {
        pauseVoxForWork();
        handleAudio(blob);
      } else if (state.voxEnabled && !state.voxPlaybackPaused) {
        setPhase('active', 'Listening…');
      }
    };
    recorder.start(100);
    setPhase('recording', 'Listening…');
  } catch (error) {
    state.voxRecorder = null;
    state.voxChunks = [];
    showError(error);
  }
}

function stopVoxRecorder(submit) {
  const recorder = state.voxRecorder;
  if (!recorder) {
    if (state.voxEnabled) setPhase('active', 'Listening…');
    return;
  }
  state.voxSubmit = submit;
  if (recorder.state === 'recording') recorder.stop();
}

function beginVoxContinuation(now) {
  if (!state.voxRecorder || state.voxRecorder.state !== 'recording') return;
  state.voxContinuationUntil = now + VOX_CONFIG.continuationMs;
  state.voxLastSpeechAt = now;
  status.textContent = 'Still listening…';
}

function voxTick(now) {
  if (!state.voxEnabled || !state.voxAnalyser) return;
  const rms = rmsFromAnalyser();
  const elapsed = now - state.voxStartedAt;
  const calibrating = elapsed < VOX_CONFIG.calibrationMs;
  const thresholds = voxThresholds();

  if (state.voxPhase === 'active' && !state.busy) {
    if (rms < thresholds.start) state.voxNoiseFloor = state.voxNoiseFloor * 0.95 + rms * 0.05;
    if (!calibrating && rms > thresholds.start) startVoxRecorder(now);
    if (!calibrating && !state.voxCalibrated) {
      state.voxCalibrated = true;
      status.textContent = 'Listening…';
    }
  } else if (state.voxPhase === 'recording' && !state.busy) {
    if (rms > thresholds.speech) {
      state.voxLastSpeechAt = now;
      state.voxContinuationUntil = 0;
      status.textContent = 'Listening…';
    } else if (state.voxContinuationUntil && now >= state.voxContinuationUntil) {
      const longEnough = now - state.voxCandidateSince >= VOX_CONFIG.minSpeechMs;
      stopVoxRecorder(longEnough);
    } else if (!state.voxContinuationUntil && now >= state.voxBackoffUntil && now - state.voxLastSpeechAt >= VOX_CONFIG.silenceMs) {
      beginVoxContinuation(now);
    } else if (now - state.voxCandidateSince >= VOX_CONFIG.maxUtteranceMs) {
      stopVoxRecorder(true);
    }
  }
  state.voxAnimationFrame = requestAnimationFrame(voxTick);
}

async function startVoxCapture() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) throw new Error('This browser does not support local audio analysis.');
  state.voxStream = await getMicrophone({audio: VOX_AUDIO_CONSTRAINTS});
  state.voxStream.getAudioTracks().forEach((track) => track.applyConstraints(VOX_AUDIO_CONSTRAINTS).catch(() => {}));
  state.voxContext = new AudioContextClass();
  await state.voxContext.resume();
  const source = state.voxContext.createMediaStreamSource(state.voxStream);
  state.voxAnalyser = state.voxContext.createAnalyser();
  state.voxAnalyser.fftSize = 1024;
  state.voxAnalyser.smoothingTimeConstant = 0;
  source.connect(state.voxAnalyser);
  state.voxData = new Uint8Array(state.voxAnalyser.fftSize);
  state.voxStartedAt = performance.now();
  state.voxNoiseFloor = VOX_CONFIG.startFloor;
  state.voxCalibrated = false;
  state.voxAnimationFrame = requestAnimationFrame(voxTick);
}

function stopVoxCapture() {
  if (state.voxAnimationFrame) cancelAnimationFrame(state.voxAnimationFrame);
  state.voxAnimationFrame = null;
  state.voxStream?.getTracks().forEach((track) => track.stop());
  state.voxStream = null;
  if (state.voxContext) state.voxContext.close().catch(() => {});
  state.voxContext = null;
  state.voxAnalyser = null;
  state.voxData = null;
}

function pauseVoxForWork() {
  if (!state.voxEnabled || state.voxPlaybackPaused) return false;
  state.voxPlaybackPaused = true;
  state.voxSubmit = false;
  if (state.voxRecorder?.state === 'recording') state.voxRecorder.stop();
  state.busy = true;
  setPhase('processing', 'Thinking…');
  stopVoxCapture();
  return true;
}

async function resumeVoxAfterWork() {
  if (!state.voxEnabled || !state.voxPlaybackPaused) return;
  state.voxPlaybackPaused = false;
  state.busy = false;
  try {
    await startVoxCapture();
    if (state.voxEnabled) setPhase('active', 'Listening…');
  } catch (error) {
    state.voxEnabled = false;
    stopVoxCapture();
    setPhase('error', 'Microphone unavailable');
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

async function handleAudio(blob) {
  state.busy = true;
  setPhase('processing', 'Thinking…');
  let result;
  try {
    const form = new FormData();
    form.append('audio', blob, 'microphone.webm');
    result = await request('/api/voice-turn', {
      method: 'POST',
      headers: {'X-Voice-Chat-Mode': 'conversation'},
      body: form,
    });
    if (result.ignored || result.no_reply) return;
    setPhase('speaking', 'Speaking…');
    await waitForPlayback(new Audio(result.audio_url));
  } catch (error) {
    showError(error);
  } finally {
    if (state.voxEnabled) await resumeVoxAfterWork();
    else state.busy = false;
    if (state.voxEnabled && state.voxPhase !== 'error') setPhase('active', 'Listening…');
    scheduleAutonomousCheck();
  }
}

async function runAutonomousCheck() {
  state.autonomousTimer = null;
  if (state.busy || !state.voxEnabled || state.voxPhase !== 'active' || state.autonomousInFlight) return;
  state.autonomousInFlight = true;
  const paused = pauseVoxForWork();
  let result;
  try {
    result = await request('/api/autonomous-turn', {
      method: 'POST',
      headers: {'X-Voice-Chat-Mode': 'conversation'},
    });
    if (!result.skipped && !result.no_reply) {
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
  errorBox.hidden = true;
  try {
    state.voxEnabled = true;
    setPhase('processing', 'Starting microphone…');
    await startVoxCapture();
    setPhase('active', 'Listening…');
    scheduleAutonomousCheck();
  } catch (error) {
    state.voxEnabled = false;
    stopVoxCapture();
    showError(error);
  }
}

function disableVox() {
  state.voxEnabled = false;
  state.voxSubmit = false;
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
  state.autonomousTimer = null;
  if (state.voxRecorder?.state === 'recording') state.voxRecorder.stop();
  state.voxRecorder = null;
  state.voxChunks = [];
  stopVoxCapture();
  state.voxPlaybackPaused = false;
  state.busy = false;
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
