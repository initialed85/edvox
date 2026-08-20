const state = {
  mediaRecorder: null,
  recordingChunks: [],
  recordingStream: null,
  busy: false,
  pressed: false,
};

const $ = (id) => document.getElementById(id);

function setBusy(busy, activity = '') {
  state.busy = busy;
  $('send-button').disabled = busy;
  $('ptt-button').disabled = busy;
  $('save-settings').disabled = busy;
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
  if (audioUrl) {
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.autoplay = true;
    audio.src = audioUrl;
    item.append(audio);
  }
  $('messages').append(item);
  $('messages').scrollTop = $('messages').scrollHeight;
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
  $('messages').querySelectorAll('.message').forEach((node) => node.remove());
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

async function handleAudio(blob) {
  setBusy(true, 'Transcribing, thinking, and synthesizing…');
  try {
    const form = new FormData();
    form.append('audio', blob, 'microphone.webm');
    const result = await request('/api/voice-turn', {method: 'POST', body: form});
    addMessage('user', result.transcript);
    addMessage('assistant', result.reply, result.audio_url);
  } catch (error) { showError(error); }
  finally { setBusy(false); }
}

async function startRecording() {
  if (state.busy || state.mediaRecorder) return;
  try {
    state.recordingStream = await navigator.mediaDevices.getUserMedia({audio: true});
    state.recordingChunks = [];
    const options = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? {mimeType: 'audio/webm;codecs=opus'} : {};
    state.mediaRecorder = new MediaRecorder(state.recordingStream, options);
    state.mediaRecorder.ondataavailable = (event) => event.data.size && state.recordingChunks.push(event.data);
    state.mediaRecorder.onstop = () => {
      const blob = new Blob(state.recordingChunks, {type: state.mediaRecorder?.mimeType || 'audio/webm'});
      state.recordingStream?.getTracks().forEach((track) => track.stop());
      state.recordingStream = null;
      state.mediaRecorder = null;
      $('ptt-button').classList.remove('recording');
      $('ptt-button').querySelector('span:last-child').textContent = 'Hold to talk';
      if (blob.size) handleAudio(blob);
    };
    state.mediaRecorder.start();
    $('ptt-button').classList.add('recording');
    $('ptt-button').querySelector('span:last-child').textContent = 'Release to send';
  } catch (error) {
    showError(error);
    state.recordingStream?.getTracks().forEach((track) => track.stop());
    state.recordingStream = null;
  }
}

function stopRecording() {
  if (state.mediaRecorder?.state === 'recording') state.mediaRecorder.stop();
}

$('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  sendText($('message').value);
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

const ptt = $('ptt-button');
ptt.addEventListener('pointerdown', (event) => { event.preventDefault(); state.pressed = true; startRecording(); });
ptt.addEventListener('pointerup', (event) => { event.preventDefault(); state.pressed = false; stopRecording(); });
ptt.addEventListener('pointercancel', () => { state.pressed = false; stopRecording(); });
ptt.addEventListener('pointerleave', (event) => { if (state.pressed && event.buttons === 0) { state.pressed = false; stopRecording(); } });
window.addEventListener('keydown', (event) => {
  if (event.code !== 'Space' || event.repeat || state.busy || event.target.matches('textarea, input, select, button')) return;
  event.preventDefault();
  state.pressed = true;
  startRecording();
});
window.addEventListener('keyup', (event) => {
  if (event.code !== 'Space' || !state.pressed) return;
  event.preventDefault();
  state.pressed = false;
  stopRecording();
});

loadSession().catch(showError);
