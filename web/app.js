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

function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  $('activity').hidden = false;
  $('activity').textContent = `Error: ${message}`;
  $('activity').style.color = 'var(--danger)';
  setTimeout(() => { $('activity').style.color = ''; }, 3000);
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function addMessage(role, text, audioUrl = '') {
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
    audio.src = audioUrl;
    item.append(audio);
  }
  $('messages').append(item);
  $('messages').scrollTop = $('messages').scrollHeight;
  return audio;
}

function renderSession(session) {
  $('backend').value = session.backend || 'chatterbox';
  $('system-prompt').value = session.system_prompt || '';
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
  (session.messages || []).forEach((message, index) => {
    if (message.role === 'user') {
      const answer = session.messages[index + 1];
      addMessage('user', message.content);
      if (answer?.role === 'assistant') addMessage('assistant', answer.content);
    }
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
      body: JSON.stringify({backend: $('backend').value, system_prompt: $('system-prompt').value}),
    });
    renderSession(session);
  } finally { setBusy(false); }
}

async function sendText(text) {
  if (!text.trim() || state.busy) return;
  $('message').value = '';
  addMessage('user', text.trim());
  setBusy(true, 'Thinking and synthesizing…');
  try {
    const result = await request('/api/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text.trim()}),
    });
    addMessage('assistant', result.reply, result.audio_url);
  } catch (error) {
    showError(error);
  } finally { setBusy(false); }
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
  finally { setBusy(false); }
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
  try {
    const form = new FormData();
    form.append('audio', blob, 'microphone.webm');
    const result = await request('/api/voice-turn', {method: 'POST', body: form});
    if (result.ignored) return;
    addMessage('user', result.transcript);
    const audio = addMessage('assistant', result.reply, result.audio_url);
    if (vox) {
      setVoxPhase('speaking');
      setBusy(true, 'Speaking…');
      await waitForPlayback(audio);
    }
  } catch (error) { showError(error); }
  finally {
    setBusy(false);
    if (vox && state.voxEnabled) setVoxPhase('armed');
  }
}

function recorderOptions() {
  return MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? {mimeType: 'audio/webm;codecs=opus'} : {};
}

async function startRecording({clone = false} = {}) {
  if (state.busy || state.mediaRecorder || state.voxEnabled) return;
  state.cloneRecording = clone;
  try {
    state.recordingStream = await navigator.mediaDevices.getUserMedia({audio: true});
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
        setVoxPhase('processing');
        handleAudio(blob, {vox: true});
      } else if (state.voxEnabled) {
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

async function enableVox() {
  if (state.busy || state.voxEnabled) return;
  setBusy(true, 'Requesting microphone permission…');
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('this browser does not support microphone capture');
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error('this browser does not support local audio analysis');
    state.voxStream = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true},
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
    state.voxEnabled = true;
    setVoxPhase('armed');
    state.voxAnimationFrame = requestAnimationFrame(voxTick);
  } catch (error) {
    state.voxEnabled = false;
    state.voxAnalyser = null;
    state.voxData = null;
    state.voxStream?.getTracks().forEach((track) => track.stop());
    state.voxStream = null;
    if (state.voxContext) state.voxContext.close().catch(() => {});
    state.voxContext = null;
    setVoxPhase('off');
    showError(error);
  } finally {
    setBusy(false);
  }
}

function disableVox() {
  state.voxEnabled = false;
  state.voxSubmit = false;
  state.voxBackoffUntil = 0;
  if (state.voxAnimationFrame) cancelAnimationFrame(state.voxAnimationFrame);
  state.voxAnimationFrame = null;
  if (state.voxRecorder?.state === 'recording') state.voxRecorder.stop();
  state.voxRecorder = null;
  state.voxChunks = [];
  state.voxStream?.getTracks().forEach((track) => track.stop());
  state.voxStream = null;
  if (state.voxContext) state.voxContext.close().catch(() => {});
  state.voxContext = null;
  state.voxAnalyser = null;
  state.voxData = null;
  state.voxPhase = 'off';
  setVoxPhase('off');
  refreshControls();
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
window.addEventListener('beforeunload', () => {
  if (state.voxEnabled) disableVox();
});

setVoxPhase('off');
refreshControls();
loadSession().catch(showError);
