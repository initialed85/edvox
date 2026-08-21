const VOX_AUDIO_CONSTRAINTS = {
  // These help speech detection while the microphone is active. The stream is
  // fully released before output playback, so it cannot keep the browser in a
  // communications/handset audio route while the reply is speaking.
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

const VOX_CONFIG = {
  calibrationMs: 800,
  minSpeechMs: 300,
  // A conversational pause is not necessarily the end of an utterance. Keep
  // listening through ordinary pauses, then give Whisper a final quiet tail.
  silenceMs: 1400,
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
  mediaRecorder: null,
  recordingChunks: [],
  recordingStream: null,
  busy: false,
  pressed: false,
  spacePttEnabled: false,
  cloneRecording: false,
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
  voxSubmit: false,
  autonomousEnabled: true,
  autonomousAfterSeconds: 10,
  autonomousIntervalSeconds: 10,
  autonomousTimer: null,
  autonomousInFlight: false,
  keepScreenAwake: true,
  wakeLock: null,
  voxPlaybackPaused: false,
};

const $ = (id) => document.getElementById(id);

function refreshControls() {
  $('send-button').disabled = state.busy;
  $('ptt-button').disabled = state.busy || state.voxEnabled;
  $('clone-voice-button').disabled = state.busy || state.voxEnabled;
  $('voice-file').disabled = state.busy || state.voxEnabled;
  $('save-settings').disabled = state.busy;
  // Keep the VOX button available while VOX is processing so it can be
  // disabled if the user wants to stop listening before the response returns.
  $('vox-toggle').disabled = state.busy && !state.voxEnabled;
}

function setBusy(busy, activity = '') {
  state.busy = busy;
  refreshControls();
  $('activity').hidden = !activity;
  $('activity').textContent = activity;
}

function voxIsOccupied() {
  return state.voxEnabled && state.voxPhase !== 'armed';
}

function scheduleAutonomousCheck() {
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
  state.autonomousTimer = null;
  if (!state.autonomousEnabled || state.busy || voxIsOccupied()) return;
  state.autonomousTimer = setTimeout(runAutonomousCheck, Math.max(1, state.autonomousIntervalSeconds) * 1000);
}

async function runAutonomousCheck() {
  state.autonomousTimer = null;
  if (!state.autonomousEnabled || state.busy || voxIsOccupied() || state.autonomousInFlight) return;
  if ([...document.querySelectorAll('audio')].some((audio) => !audio.paused && !audio.ended)) {
    scheduleAutonomousCheck();
    return;
  }
  state.autonomousInFlight = true;
  const voxPaused = pauseVoxForWork();
  setBusy(true, 'Checking in…');
  let result;
  try {
    result = await request('/api/autonomous-turn', {method: 'POST'});
    if (result.skipped && !result.retry) return;
    if (!result.skipped && !result.no_reply && result.reply) {
      const audio = addMessage('assistant', result.reply, result.audio_url, result.timings);
      if (voxPaused) await waitForPlayback(audio);
    }
  } catch (error) {
    showError(error);
  } finally {
    state.autonomousInFlight = false;
    if (voxPaused && state.voxEnabled) await resumeVoxAfterWork();
    setBusy(false, formatTimings(result?.timings));
    scheduleAutonomousCheck();
  }
}

function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  $('activity').hidden = false;
  $('activity').textContent = `Error: ${message}`;
  $('activity').style.color = 'var(--danger)';
  setTimeout(() => { $('activity').style.color = ''; }, 3000);
}

function formatTimings(timings) {
  if (!timings) return '';
  const parts = [];
  for (const [key, label] of [['stt_ms', 'STT'], ['chat_ms', 'chat'], ['tts_ms', 'TTS'], ['tts_model_ms', 'TTS model'], ['total_ms', 'total']]) {
    if (typeof timings[key] === 'number') parts.push(`${label} ${(timings[key] / 1000).toFixed(1)}s`);
  }
  return parts.join(' · ');
}

async function requestWakeLock() {
  if (!state.keepScreenAwake || !('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
  if (state.wakeLock && !state.wakeLock.released) return;
  try {
    state.wakeLock = await navigator.wakeLock.request('screen');
    state.wakeLock.addEventListener('release', () => {
      state.wakeLock = null;
      if (state.keepScreenAwake && document.visibilityState === 'visible') {
        setTimeout(() => requestWakeLock().catch(() => {}), 250);
      }
    }, {once: true});
    $('wake-note').textContent = 'Screen wake lock is active while this page is visible.';
  } catch (_) {
    state.wakeLock = null;
    $('wake-note').textContent = 'This browser did not allow screen wake lock.';
  }
}

async function releaseWakeLock() {
  const lock = state.wakeLock;
  state.wakeLock = null;
  if (lock && !lock.released) await lock.release().catch(() => {});
  $('wake-note').textContent = state.keepScreenAwake
    ? 'Wake lock will resume when the page is visible again.'
    : 'The phone may lock normally.';
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function addMessage(role, text, audioUrl = '', timings = null) {
  $('empty-state')?.remove();
  const item = document.createElement('article');
  item.className = `message ${role}`;
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'user' ? 'You' : 'Assistant';
  const body = document.createElement('div');
  body.className = 'message-body';
  body.textContent = text;
  item.append(label, body);
  let audio = null;
  if (audioUrl) {
    audio = document.createElement('audio');
    audio.controls = true;
    audio.autoplay = true;
    audio.playsInline = true;
    audio.setAttribute('playsinline', '');
    audio.src = audioUrl;
    item.append(audio);
  }
  const timingText = formatTimings(timings);
  if (timingText) {
    const timing = document.createElement('div');
    timing.className = 'message-timing';
    timing.textContent = timingText;
    item.append(timing);
  }
  $('messages').append(item);
  $('messages').scrollTop = $('messages').scrollHeight;
  return audio;
}

function renderSession(session) {
  $('backend').value = session.backend || 'chatterbox';
  $('persona').value = session.persona || '';
  state.autonomousAfterSeconds = Number(session.autonomous_after_seconds || 10);
  state.autonomousIntervalSeconds = Number(session.autonomous_interval_seconds || state.autonomousAfterSeconds || 10);

  if (session.reference) {
    $('voice-status').textContent = session.reference;
    $('clear-voice').hidden = false;
  } else {
    $('voice-status').textContent = 'No reference uploaded';
    $('clear-voice').hidden = true;
  }
  $('messages').querySelectorAll('.message, .empty-state').forEach((node) => node.remove());
  if (!(session.messages || []).length) {
    $('messages').insertAdjacentHTML('afterbegin', '<div id="empty-state" class="empty-state"><div class="orb">◉</div><h2>Ready when you are.</h2><p>Enable VOX, send a text message, or hold the PTT button to speak.</p></div>');
  }
  (session.messages || []).forEach((message) => {
    if (message.role === 'user' && message.content?.trim()) addMessage('user', message.content, '', message.timings);
    if (message.role === 'assistant' && message.content?.trim()) addMessage('assistant', message.content, '', message.timings);
  });
}

async function loadSession() {
  const session = await request('/api/session');
  renderSession(session);
  const health = await request('/api/health');
  const services = health.services || {};
  const ready = ['chat', 'stt'].every((key) => services[key]?.status === 'ok') && services.tts?.status === 'ok';
  $('service-status').textContent = ready ? 'All services ready' : 'Check local services';
  $('service-status').className = `status-pill ${ready ? 'ready' : 'warn'}`;
}

async function saveSettings() {
  setBusy(true, 'Saving session settings…');
  try {
    const session = await request('/api/session/settings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({backend: $('backend').value, persona: $('persona').value}),
    });
    renderSession(session);
  } finally { setBusy(false); scheduleAutonomousCheck(); }
}

async function sendText(text) {
  if (!text.trim() || state.busy) return;
  $('message').value = '';
  addMessage('user', text.trim());
  const voxPaused = pauseVoxForWork();
  setBusy(true, 'Thinking and synthesizing…');
  let result;
  try {
    result = await request('/api/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text.trim()}),
    });
    if (!result.no_reply) {
      const audio = addMessage('assistant', result.reply, result.audio_url, result.timings);
      if (voxPaused) await waitForPlayback(audio);
    }
  } catch (error) {
    showError(error);
  } finally {
    if (voxPaused && state.voxEnabled) await resumeVoxAfterWork();
    setBusy(false, formatTimings(result?.timings));
    scheduleAutonomousCheck();
  }
}

async function uploadVoice(file) {
  if (!file) return;
  setBusy(true, 'Preparing voice reference…');
  try {
    const form = new FormData();
    form.append('voice', file, file.name);
    const result = await request('/api/session/voice', {method: 'POST', body: form});
    $('voice-status').textContent = `${result.reference} (${result.duration_seconds}s)`;
    $('clear-voice').hidden = false;
  } catch (error) { showError(error); }
  finally { setBusy(false); scheduleAutonomousCheck(); }
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

async function handleAudio(blob, {vox = false} = {}) {
  setBusy(true, vox ? 'Transcribing…' : 'Transcribing, thinking, and synthesizing…');
  let result;
  try {
    const form = new FormData();
    form.append('audio', blob, 'microphone.webm');
    result = await request('/api/voice-turn', {method: 'POST', body: form});
    if (result.ignored) return;
    addMessage('user', result.transcript);
    const audio = result.no_reply ? null : addMessage('assistant', result.reply, result.audio_url, result.timings);
    if (vox) {
      setVoxPhase('speaking');
      setBusy(true, 'Speaking…');
      await waitForPlayback(audio);
    }
  } catch (error) { showError(error); }
  finally {
    if (vox && state.voxEnabled) await resumeVoxAfterWork();
    setBusy(false, formatTimings(result?.timings));
    scheduleAutonomousCheck();
  }
}

function recorderOptions() {
  return MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? {mimeType: 'audio/webm;codecs=opus'} : {};
}

function microphoneError(error) {
  if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') {
    if (window.isSecureContext === false) {
      return new Error('Microphone access requires HTTPS when connecting from another device. Open the Caddy HTTPS URL, or use localhost.');
    }
    return new Error('Microphone permission was denied. Check this site’s browser permission and try again.');
  }
  if (error?.name === 'NotFoundError') return new Error('No microphone was found on this device.');
  if (error?.name === 'NotReadableError') return new Error('The microphone is busy or unavailable.');
  if (!navigator.mediaDevices?.getUserMedia) {
    return new Error('Microphone access requires HTTPS when connecting from another device. Open the Caddy HTTPS URL, or use localhost.');
  }
  return error instanceof Error ? error : new Error(String(error));
}

async function getMicrophone(constraints) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    throw microphoneError();
  }
  try {
    return await navigator.mediaDevices.getUserMedia(constraints);
  } catch (error) {
    throw microphoneError(error);
  }
}

async function startRecording({clone = false} = {}) {
  if (state.busy || state.mediaRecorder || state.voxEnabled) return;
  state.cloneRecording = clone;
  try {
    state.recordingStream = await getMicrophone({audio: true});
    state.recordingChunks = [];
    state.mediaRecorder = new MediaRecorder(state.recordingStream, recorderOptions());
    state.mediaRecorder.ondataavailable = (event) => event.data.size && state.recordingChunks.push(event.data);
    state.mediaRecorder.onstop = () => {
      const blob = new Blob(state.recordingChunks, {type: state.mediaRecorder?.mimeType || 'audio/webm'});
      state.recordingStream?.getTracks().forEach((track) => track.stop());
      state.recordingStream = null;
      state.mediaRecorder = null;
      $('ptt-button').classList.remove('recording');
      $('clone-voice-button').classList.remove('recording');
      $('ptt-button').querySelector('span:last-child').textContent = 'Hold to talk';
      $('clone-voice-button').textContent = 'Record my voice';
      const wasCloneRecording = state.cloneRecording;
      if (blob.size) {
        if (wasCloneRecording) uploadVoice(new File([blob], 'my-voice.webm', {type: blob.type}));
        else handleAudio(blob);
      }
      state.cloneRecording = false;
    };
    state.mediaRecorder.start();
    if (clone) {
      $('clone-voice-button').classList.add('recording');
      $('clone-voice-button').textContent = 'Release to save voice';
    } else {
      $('ptt-button').classList.add('recording');
      $('ptt-button').querySelector('span:last-child').textContent = 'Release to send';
    }
  } catch (error) {
    showError(error);
    state.recordingStream?.getTracks().forEach((track) => track.stop());
    state.recordingStream = null;
    state.cloneRecording = false;
  }
}

function stopRecording() {
  if (state.mediaRecorder?.state === 'recording') state.mediaRecorder.stop();
}

function setVoxStatus(text) {
  $('vox-status').textContent = text;
}

function setVoxPhase(phase) {
  state.voxPhase = phase;
  const button = $('vox-toggle');
  const label = button.querySelector('.vox-label');
  const enabled = state.voxEnabled;
  button.classList.toggle('active', enabled);
  button.classList.toggle('recording', phase === 'recording');
  label.textContent = enabled ? 'Disable VOX' : 'Enable VOX';
  if (!enabled || phase === 'off') {
    setVoxStatus('VOX is off. Enable it to listen for speech locally.');
  } else if (phase === 'armed') {
    setVoxStatus(state.voxCalibrated ? 'Listening locally… speak when ready.' : 'Calibrating microphone…');
  } else if (phase === 'recording') {
    setVoxStatus('Speech detected — waiting for the end of your utterance…');
  } else if (phase === 'processing') {
    setVoxStatus('Transcribing and responding…');
  } else if (phase === 'speaking') {
    setVoxStatus('Speaking… VOX pauses while the reply plays.');
  }
  refreshControls();
  if (phase === 'armed') scheduleAutonomousCheck();
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
      showError(new Error('browser audio recording failed'));
      state.voxSubmit = false;
      if (recorder.state === 'recording') recorder.stop();
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
      if (submit && blob.size && state.voxEnabled) {
        pauseVoxForWork();
        handleAudio(blob, {vox: true});
      } else if (state.voxEnabled && !state.voxPlaybackPaused) {
        setVoxPhase('armed');
      }
    };
    recorder.start(100);
    setVoxPhase('recording');
  } catch (error) {
    showError(error);
    state.voxRecorder = null;
    state.voxChunks = [];
  }
}

function stopVoxRecorder(submit) {
  const recorder = state.voxRecorder;
  if (!recorder) {
    if (state.voxEnabled) setVoxPhase('armed');
    return;
  }
  state.voxSubmit = submit;
  if (recorder.state === 'recording') recorder.stop();
}

function voxTick(now) {
  if (!state.voxEnabled || !state.voxAnalyser) return;
  const rms = rmsFromAnalyser();
  const elapsed = now - state.voxStartedAt;
  const calibrating = elapsed < VOX_CONFIG.calibrationMs;
  const thresholds = voxThresholds();

  if (state.voxPhase === 'armed' && !state.busy) {
    if (rms < thresholds.start) {
      state.voxNoiseFloor = state.voxNoiseFloor * 0.95 + rms * 0.05;
    }
    if (!calibrating && rms > thresholds.start) startVoxRecorder(now);
    if (!calibrating && !state.voxCalibrated) {
      state.voxCalibrated = true;
      setVoxStatus('Listening locally… speak when ready.');
    }
  } else if (state.voxPhase === 'recording' && !state.busy) {
    if (rms > thresholds.speech) {
      state.voxLastSpeechAt = now;
    } else if (
      now >= state.voxBackoffUntil &&
      now - state.voxLastSpeechAt >= VOX_CONFIG.silenceMs
    ) {
      const longEnough = now - state.voxCandidateSince >= VOX_CONFIG.minSpeechMs;
      stopVoxRecorder(longEnough);
    } else if (now - state.voxCandidateSince >= VOX_CONFIG.maxUtteranceMs) {
      stopVoxRecorder(true);
    }
  }
  state.voxAnimationFrame = requestAnimationFrame(voxTick);
}

async function startVoxCapture() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) throw new Error('this browser does not support local audio analysis');
  state.voxStream = await getMicrophone({audio: VOX_AUDIO_CONSTRAINTS});
  state.voxStream.getAudioTracks().forEach((track) => {
    track.applyConstraints(VOX_AUDIO_CONSTRAINTS).catch(() => {});
  });
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

// VOX is half-duplex. Do not leave microphone capture or an AudioContext alive
// while the browser is playing the reply: mobile browsers may route audio as
// a call, and desktop browsers can otherwise keep the capture/output paths
// competing with one another.
function pauseVoxForWork() {
  if (!state.voxEnabled || state.voxPlaybackPaused) return false;
  state.voxPlaybackPaused = true;
  state.voxSubmit = false;
  if (state.voxRecorder?.state === 'recording') state.voxRecorder.stop();
  setVoxPhase('processing');
  stopVoxCapture();
  return true;
}

async function resumeVoxAfterWork() {
  if (!state.voxEnabled || !state.voxPlaybackPaused) return;
  state.voxPlaybackPaused = false;
  try {
    await startVoxCapture();
    if (state.voxEnabled) setVoxPhase('armed');
  } catch (error) {
    state.voxEnabled = false;
    stopVoxCapture();
    setVoxPhase('off');
    showError(error);
  }
}

async function enableVox() {
  if (state.busy || state.voxEnabled) return;
  setBusy(true, 'Requesting microphone permission…');
  state.voxEnabled = true;
  try {
    await startVoxCapture();
    setVoxPhase('armed');
  } catch (error) {
    state.voxEnabled = false;
    stopVoxCapture();
    setVoxPhase('off');
    showError(error);
  } finally {
    setBusy(false);
    scheduleAutonomousCheck();
  }
}

function disableVox() {
  state.voxEnabled = false;
  state.voxSubmit = false;
  state.voxBackoffUntil = 0;
  if (state.voxRecorder?.state === 'recording') state.voxRecorder.stop();
  state.voxRecorder = null;
  state.voxChunks = [];
  stopVoxCapture();
  state.voxPlaybackPaused = false;
  state.voxPhase = 'off';
  setVoxPhase('off');
  refreshControls();
  scheduleAutonomousCheck();
}

$('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  sendText($('message').value);
});
$('message').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendText(event.currentTarget.value);
  }
});
$('save-settings').addEventListener('click', () => saveSettings().catch(showError));
$('voice-file').addEventListener('change', (event) => uploadVoice(event.target.files[0]));
$('clear-voice').addEventListener('click', async () => {
  try { renderSession(await request('/api/session/clear-voice', {method: 'POST'})); }
  catch (error) { showError(error); }
});
$('reset-button').addEventListener('click', async () => {
  try { renderSession(await request('/api/session/reset', {method: 'POST'})); }
  catch (error) { showError(error); }
});
$('vox-toggle').addEventListener('click', () => {
  if (state.voxEnabled) disableVox();
  else enableVox().catch(showError);
});

const ptt = $('ptt-button');
ptt.addEventListener('pointerdown', (event) => { event.preventDefault(); state.pressed = true; startRecording(); });
ptt.addEventListener('pointerup', (event) => { event.preventDefault(); state.pressed = false; stopRecording(); });
ptt.addEventListener('pointercancel', () => { state.pressed = false; stopRecording(); });
ptt.addEventListener('pointerleave', (event) => { if (state.pressed && event.buttons === 0) { state.pressed = false; stopRecording(); } });

const cloneButton = $('clone-voice-button');
cloneButton.addEventListener('pointerdown', (event) => { event.preventDefault(); state.pressed = true; startRecording({clone: true}); });
cloneButton.addEventListener('pointerup', (event) => { event.preventDefault(); state.pressed = false; stopRecording(); });
cloneButton.addEventListener('pointercancel', () => { state.pressed = false; stopRecording(); });
cloneButton.addEventListener('pointerleave', (event) => { if (state.pressed && event.buttons === 0) { state.pressed = false; stopRecording(); } });

function shouldHandleSpace(event) {
  const target = event.target;
  const isFormControl = target instanceof Element && target.closest('textarea, input, select, button');
  return event.code === 'Space' && !event.repeat && !state.busy && !state.voxEnabled && !isFormControl && state.spacePttEnabled;
}
window.addEventListener('keydown', (event) => {
  if (!shouldHandleSpace(event)) return;
  event.preventDefault();
  state.pressed = true;
  startRecording();
});
window.addEventListener('keyup', (event) => {
  if (event.code !== 'Space' || !state.pressed || !state.spacePttEnabled) return;
  event.preventDefault();
  state.pressed = false;
  stopRecording();
});
$('space-ptt').checked = state.spacePttEnabled;
$('wake-screen').checked = state.keepScreenAwake;
$('wake-screen').addEventListener('change', (event) => {
  state.keepScreenAwake = event.target.checked;
  if (state.keepScreenAwake) requestWakeLock().catch(() => {});
  else releaseWakeLock().catch(() => {});
});
$('autonomous-toggle').checked = state.autonomousEnabled;
$('autonomous-note').textContent = `Enabled. The model is checked every ${state.autonomousIntervalSeconds} seconds after silence.`;
$('autonomous-toggle').addEventListener('change', (event) => {
  state.autonomousEnabled = event.target.checked;
  $('autonomous-note').textContent = state.autonomousEnabled
    ? `Enabled. The model is checked every ${state.autonomousIntervalSeconds} seconds after silence.`
    : 'Off. The model will only respond after you speak.';
  scheduleAutonomousCheck();
});
$('space-ptt').addEventListener('change', (event) => {
  state.spacePttEnabled = event.target.checked;
  event.target.blur();
  $('keyboard-note').textContent = state.spacePttEnabled ? 'Spacebar PTT is enabled while this page is focused.' : 'Spacebar is navigation only; use the PTT button to talk.';
});
window.addEventListener('blur', () => {
  if (state.pressed) {
    state.pressed = false;
    stopRecording();
  }
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') requestWakeLock().catch(() => {});
});
window.addEventListener('beforeunload', () => {
  if (state.voxEnabled) disableVox();
  if (state.autonomousTimer) clearTimeout(state.autonomousTimer);
  releaseWakeLock().catch(() => {});
});

setVoxPhase('off');
refreshControls();
loadSession().then(() => {
  requestWakeLock().catch(() => {});
  scheduleAutonomousCheck();
}).catch(showError);
