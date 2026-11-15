const VOX_AUDIO_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

// A 44-byte header and no samples. Played through the shared answer element inside the tap
// that starts the session, which is what makes iOS allow the later play() of a real answer.
const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=';

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
  tabId: (() => {
    try {
      const key = 'voicechat_tab_id';
      let value = sessionStorage.getItem(key);
      if (!value) {
        value = crypto.randomUUID();
        sessionStorage.setItem(key, value);
      }
      return value;
    } catch (_) {
      return crypto.randomUUID();
    }
  })(),
  visitId: crypto.randomUUID(),
  returnGreetingPending: true,
  pageWasHidden: false,
  // The one capture stream for the session. vad-web's defaults STOP the tracks on pause and
  // re-run getUserMedia on resume; this app pauses the detector on every turn, so that pairing
  // means a permission prompt mid-conversation. Caching the stream is what stops that.
  stream: null,
  answerAudio: null,
  gestureArmed: false,
  tapAttempts: 0,
};

const orb = document.getElementById('status-orb');
const status = document.getElementById('status');
const errorBox = document.getElementById('error');

function setPhase(phase, message) {
  state.voxPhase = phase;
  orb.className = `status-orb ${phase}`;
  orb.setAttribute('aria-label',
    phase === 'off' || phase === 'error' || phase === 'needs-tap' ? 'Start listening' : 'Listening');
  status.textContent = message;
}

function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  errorBox.textContent = message;
  errorBox.hidden = false;
  setPhase('error', 'Microphone unavailable');
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('X-Voice-Chat-Tab', state.tabId);
  const response = await fetch(path, {...options, headers});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function presence(stateName, keepalive = false) {
  return fetch('/api/session/presence', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Voice-Chat-Tab': state.tabId},
    body: JSON.stringify({state: stateName, tab_id: state.tabId, visit_id: state.visitId}),
    keepalive,
  }).then((response) => {
    if (!response.ok) throw new Error(`Presence update failed (${response.status})`);
    return response.json();
  });
}

async function requestReturnGreeting() {
  if (!state.returnGreetingPending || !state.voxEnabled || state.busy || state.voxPhase !== 'active') return;
  state.returnGreetingPending = false;
  let paused = false;
  try {
    paused = await pauseVoxForWork();
    setPhase('processing', 'Checking in…');
    const result = await request('/api/return-greeting', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Voice-Chat-Mode': 'conversation',
        'X-Voice-Chat-Tab': state.tabId,
      },
      body: JSON.stringify({tab_id: state.tabId, visit_id: state.visitId}),
    });
    if (!result.skipped && !result.no_reply && result.audio_url) {
      setPhase('speaking', 'Speaking…');
      await playAnswer(result.audio_url);
    }
  } catch (error) {
    state.returnGreetingPending = true;
    showError(error);
  } finally {
    if (paused && state.voxEnabled) await resumeVoxAfterWork();
    if (state.voxEnabled && state.voxPhase !== 'error') setPhase('active', 'Listening…');
    if (state.returnGreetingPending) setTimeout(() => void requestReturnGreeting(), 0);
    scheduleAutonomousCheck();
  }
}

function microphoneError(error) {
  if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') {
    if (window.isSecureContext === false) {
      return new Error('Microphone access requires HTTPS. Open the secure voice URL, or use localhost.');
    }
    // NeedsTapError rather than a plain Error so the handlers can tell "the browser wants a
    // gesture / the user said no" apart from a network or server failure. The old code returned
    // an untyped Error here, which is why every iOS refusal came out as a red box that killed
    // the session mid-answer.
    return new NeedsTapError('Microphone permission was denied. Allow microphone access for this site and tap the light to retry.');
  }
  if (error?.name === 'NotFoundError') return new Error('No microphone was found on this device.');
  if (error?.name === 'NotReadableError') return new Error('The microphone is busy or unavailable.');
  if (!navigator.mediaDevices?.getUserMedia) {
    return new Error('Microphone access requires HTTPS. Open the secure voice URL, or use localhost.');
  }
  return error instanceof Error ? error : new Error(String(error));
}

// The microphone is gone and no API call can bring it back without a user gesture, so the app
// goes quiet and waits on the orb instead of tearing the session down with a red error box.
// markNeedsTap deliberately does NOT stop the cached tracks: if the interruption was benign the
// next tap reuses the same stream and never prompts again.
function markNeedsTap(message) {
  state.voxEnabled = false;
  state.busy = false;
  state.voxPausedForWork = false;
  setPhase('needs-tap', message || 'Tap the light to keep listening');
}

// Raised when capture cannot be resumed programmatically. Named distinctly from the browser's
// own NotAllowedError so the two are not confused in the handlers, but handled identically.
class NeedsTapError extends Error {
  constructor(message) {
    super(message || 'Tap the light to keep listening');
    this.name = 'NeedsTap';
  }
}

// All of these mean the same thing on a phone: the browser will only hand over a microphone
// from within a tap, and we are not in one right now.
function isNeedsTap(error) {
  return error?.name === 'NeedsTap'
    || error?.name === 'NotAllowedError'
    || error?.name === 'SecurityError';
}

function liveTrack(stream) {
  return stream?.getAudioTracks().find((track) => track.readyState === 'live') || null;
}

function setCaptureEnabled(stream, enabled) {
  stream?.getAudioTracks().forEach((track) => { track.enabled = enabled; });
}

async function getMicrophone() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw microphoneError();
  // Reuse the session's stream. Every call that does not need to reach the hardware is a call
  // that cannot prompt, cannot be rejected for want of a gesture, and cannot fail mid-answer.
  // Re-enabling here matters because vad-web's destroy() routes through pauseStream, so a
  // rebuilt detector would otherwise be handed a stream whose tracks are still muted.
  if (liveTrack(state.stream)) {
    setCaptureEnabled(state.stream, true);
    return state.stream;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: VOX_AUDIO_CONSTRAINTS});
    const track = liveTrack(stream);
    if (!track) {
      // Safari will sometimes hand back a stream whose track has already ended. Accepting it
      // means listening to nothing forever, silently.
      stream.getTracks().forEach((t) => t.stop());
      throw new NeedsTapError('The browser released the microphone straight away. Tap the light to try again.');
    }
    state.stream = stream;
    stream.getAudioTracks().forEach((t) => {
      t.applyConstraints(VOX_AUDIO_CONSTRAINTS).catch(() => {});
      // A hardware interruption - an incoming call, Siri, another app, or iOS swapping the
      // audio session when the answer starts playing - ends the track. Nothing can restart it
      // without a gesture, so say so rather than pretending to listen.
      t.addEventListener('ended', () => {
        if (state.voxEnabled && state.stream === stream) markNeedsTap();
      }, {once: true});
    });
    return stream;
  } catch (error) {
    if (error instanceof NeedsTapError) throw error;
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
    // The pair that keeps the microphone allocated for the whole conversation. vad-web calls
    // pauseStream() from pause() and resumeStream(oldStream) from start(), and its defaults
    // stop the tracks and re-run getUserMedia. Muting instead of stopping means resume hands
    // back the SAME live stream, so after the one tap that started the session there is no
    // second permission request anywhere in a turn.
    pauseStream: async (stream) => setCaptureEnabled(stream, false),
    resumeStream: async (previous) => {
      const stream = liveTrack(previous) ? previous : state.stream;
      if (!liveTrack(stream)) throw new NeedsTapError();
      setCaptureEnabled(stream, true);
      state.stream = stream;
      return stream;
    },
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
    if (isNeedsTap(error)) {
      // This is the mid-answer failure Ed saw: resume ran getUserMedia from an async
      // continuation, iOS refused it, and the old handler reported that as "permission was
      // denied" and killed the session. Retrying the same call would fail the same way, so the
      // orb carries it instead.
      markNeedsTap();
      return;
    }
    showError(error);
  }
}

// One element reused for every answer, unlocked inside the tap that started the session: iOS
// gates media playback on a gesture, so a play() from the continuation of a POST is refused.
function answerElement() {
  if (!state.answerAudio) {
    const audio = new Audio();
    audio.preload = 'auto';
    state.answerAudio = audio;
  }
  return state.answerAudio;
}

function unlockPlayback() {
  const audio = answerElement();
  audio.src = SILENT_WAV;
  // Deliberately unchecked: the point is that the element has been played from within a
  // gesture, not that two frames of silence reached the speaker.
  audio.play().catch(() => {});
}

async function playAnswer(url) {
  if (!url) return;
  const audio = answerElement();
  audio.src = url;
  await waitForPlayback(audio);
}

async function waitForPlayback(audio) {
  if (!audio) return;
  if (audio.paused) {
    try {
      await audio.play();
    } catch (_) {
      // Playback was refused - iOS with an expired gesture, most often. The old code returned
      // here, which is how a turn could "fail as it goes to answer" with no message and no
      // speech: the sentence was decoded and simply never played. Queue it for the next touch.
      await new Promise((resolve) => {
        const timer = setTimeout(() => {
          document.removeEventListener('pointerdown', unlock);
          resolve();
        }, 20000);
        const unlock = () => {
          clearTimeout(timer);
          document.removeEventListener('pointerdown', unlock);
          audio.play().then(() => audio.addEventListener('ended', resolve, {once: true})).catch(resolve);
        };
        document.addEventListener('pointerdown', unlock, {once: true});
      });
      return;
    }
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
  // If the user speaks before the startup greeting gets dispatched, let the
  // normal response carry the return context instead of greeting afterwards.
  state.returnGreetingPending = false;
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
      await playAnswer(result.audio_url);
    }
  } catch (error) {
    showError(error);
  } finally {
    if (paused && state.voxEnabled) await resumeVoxAfterWork();
    else state.busy = false;
    if (state.voxEnabled && state.voxPhase !== 'error') setPhase('active', 'Listening…');
    if (state.returnGreetingPending) setTimeout(() => void requestReturnGreeting(), 0);
    scheduleAutonomousCheck();
  }
}

async function runAutonomousCheck() {
  state.autonomousTimer = null;
  if (state.returnGreetingPending) {
    requestReturnGreeting();
    return;
  }
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
      await playAnswer(result.audio_url);
    }
  } catch (error) {
    showError(error);
  } finally {
    state.autonomousInFlight = false;
    if (paused && state.voxEnabled) await resumeVoxAfterWork();
    if (state.returnGreetingPending) setTimeout(() => void requestReturnGreeting(), 0);
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

// Rebuild the detector graph without touching the microphone. Needed because iOS suspends the
// AudioContext when the tab is backgrounded and vad-web has no hook to resume it, which would
// otherwise leave the detector reading silence for the rest of the visit. The capture stream is
// deliberately kept, so this never prompts.
async function restartVadGraph() {
  if (state.vadStarting || !state.voxEnabled || !liveTrack(state.stream)) return;
  state.vadStarting = true;
  try {
    await destroyVad();
    if (!state.voxEnabled) return;
    state.vad = await createVad();
    await state.vad.start();
    if (state.voxEnabled && state.voxPhase !== 'recording' && state.voxPhase !== 'speaking') {
      setPhase('active', 'Listening…');
    }
  } catch (error) {
    state.voxEnabled = false;
    await destroyVad();
    if (isNeedsTap(error)) markNeedsTap();
    else showError(error);
  } finally {
    state.vadStarting = false;
  }
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
    // Give the detector a short settling period before the one-shot return
    // greeting. This keeps startup noise from being mistaken for a turn and
    // makes the greeting feel like the call has just connected.
    setTimeout(() => void requestReturnGreeting(), 800);
  } catch (error) {
    state.voxEnabled = false;
    await destroyVad();
    // A gesture-less start at boot is expected to fail on iOS, and a first refusal after a tap
    // is usually the browser interrupting capture, not the user denying it. Only after two taps
    // has a permission denial been demonstrated often enough to be worth an error box.
    if (isNeedsTap(error) && (!state.gestureArmed || state.tapAttempts < 2)) {
      markNeedsTap(state.gestureArmed ? 'Tap the light to keep listening'
                                       : 'Tap the light to start listening');
      return;
    }
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
  // This is the only place the capture is actually released: the user ended the conversation,
  // so give the hardware and the recording indicator back.
  state.stream?.getTracks().forEach((track) => track.stop());
  state.stream = null;
  if (state.answerAudio) {
    state.answerAudio.pause();
    state.answerAudio = null;
  }
  setPhase('off', 'Tap the light to listen');
}

orb.addEventListener('click', () => {
  // Conversation mode stays locked to VOX. The light is only a permission
  // retry affordance once startup failed; it is not an on/off control.
  if (!state.voxEnabled) {
    // The tap is the gesture iOS requires for both the microphone and playback, so everything
    // that needs it happens inside this handler, synchronously where possible.
    state.gestureArmed = true;
    state.tapAttempts += 1;
    unlockPlayback();
    enableVox().catch(showError);
  }
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    requestWakeLock().catch(() => {});
    if (state.pageWasHidden) {
      // A visibility return is a new browser visit within the same tab. This
      // lets the server measure the hidden interval without changing the
      // cookie-scoped conversation.
      state.visitId = crypto.randomUUID();
      state.returnGreetingPending = true;
      // Returning to the tab on iOS leaves a suspended AudioContext behind, and on Android it
      // can leave a muted track; rebuild the graph so the orb is not lying about listening.
      if (state.voxEnabled) void restartVadGraph();
    }
    state.pageWasHidden = false;
    presence('visible').then((result) => {
      if (result.returned) state.returnGreetingPending = true;
      if (state.returnGreetingPending) setTimeout(() => void requestReturnGreeting(), 0);
    }).catch(() => {});
  } else {
    state.pageWasHidden = true;
    presence('away', true).catch(() => {});
  }
});
window.addEventListener('pagehide', () => {
  presence('away', true).catch(() => {});
});
window.addEventListener('beforeunload', () => {
  disableVox();
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
});

async function boot() {
  try {
    const session = await request('/api/session');
    await presence('visible').catch(() => {});
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
