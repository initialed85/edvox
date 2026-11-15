// Emulates the two iOS rules that broke Vox, plus the vendored detector's contract, so the
// real app.js can be driven headlessly. See tests/test_web_microphone.py.
//
//   1. getUserMedia() is refused unless it runs inside a user gesture, and a grant does not
//      survive stopping the tracks - so a stopped microphone means another prompt.
//   2. HTML media play() is refused unless that element has already played inside a gesture.
//
// vad-web contract: pause() -> options.pauseStream(stream); start() from 'paused' ->
// options.resumeStream(previousStream); from 'uninitialized' -> options.getStream(). Its
// defaults stop the tracks on pause and re-run getUserMedia on resume.
window.__errs = [];
window.addEventListener('error', (e) => window.__errs.push('error: ' + e.message + ' @' + (e.filename || '') + ':' + (e.lineno || 0)));
window.addEventListener('unhandledrejection', (e) => window.__errs.push('rejection: ' + ((e.reason && (e.reason.stack || e.reason.message)) || e.reason)));

window.__log = [];
const out = (m) => { window.__log.push(m); document.getElementById('out').textContent = window.__log.join('\n'); };
window.out = out;
window.results = [];
const check = (name, ok, detail) => { const line = `${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`; window.results.push({ok, line}); out(line); };

// A click is the gesture. It expires on the next macrotask, exactly as it does on a phone.
window.__inGesture = false;
document.addEventListener('click', () => {
  window.__inGesture = true;
  setTimeout(() => { window.__inGesture = false; }, 0);
}, true);

let gumCalls = 0, gumPrompts = 0, plays = [];
class FakeTrack {
  constructor() { this.readyState = 'live'; this.enabled = true; this.listeners = {}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  removeEventListener() {}
  fire(name) { (this.listeners[name] || []).forEach((fn) => fn({type: name})); }
  stop() { this.readyState = 'ended'; this.fire('ended'); }
  kill() { this.readyState = 'ended'; this.fire('ended'); }
  // iOS ending capture without emitting 'ended': the app has to notice by itself.
  silentKill() { this.readyState = 'ended'; }
  async applyConstraints() { return; }
}
class FakeStream {
  constructor() { this.track = new FakeTrack(); }
  getAudioTracks() { return [this.track]; }
  getTracks() { return [this.track]; }
}
let current = null;
Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: {
    getUserMedia: async () => {
      gumCalls += 1;
      if (!window.__inGesture) throw new DOMException('requires user gesture', 'NotAllowedError');
      gumPrompts += 1;
      current = new FakeStream();
      return current;
    },
  },
});
class FakeAudio {
  constructor(src) { this._src = src || ''; this.paused = true; this.ended = false; this.listeners = {}; this.armed = false; }
  get src() { return this._src; }
  set src(v) { this._src = v; this.ended = false; this.paused = true; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  removeEventListener() {}
  fire(n) { (this.listeners[n] || []).forEach((f) => f()); }
  pause() { this.paused = true; }
  load() {}
  async play() {
    plays.push(this._src);
    // __forceRefuse models iOS deciding the gesture has expired, independently of the arming
    // rule, so the "playback was refused" branch of the app can be exercised.
    if (window.__forceRefuse || (!this.armed && !window.__inGesture)) throw new DOMException('playback requires a gesture', 'NotAllowedError');
    this.armed = true;
    this.paused = false;
    this.ended = true; this.paused = true;
    setTimeout(() => this.fire('ended'), 0);
  }
}
window.Audio = FakeAudio;

// vad-web's default pauseStream, used when the page does not override it.
const defaultPauseStream = async (stream) => { stream.getTracks().forEach((t) => t.stop()); };
window.__vadLog = [];
class FakeVad {
  constructor(opts) { this.opts = opts; this.state = 'uninitialized'; this.listening = false; this.stream = null; window.__vadLog.push('new'); }
  async start() {
    window.__vadLog.push(`start(${this.state},listen=${this.listening},track=${this.stream && this.stream.track.readyState})`);
    if (this.state === 'paused') { window.__vadLog.push('resumeStream'); this.stream = await this.opts.resumeStream(this.stream); }
    else if (this.state === 'uninitialized') { window.__vadLog.push('getStream'); this.stream = await this.opts.getStream(); }
    else { window.__vadLog.push(`start:already-${this.state}`); this.listening = true; return; }
    this.state = 'running';
    this.listening = true;
  }
  async pause() {
    window.__vadLog.push(`pause(${this.state},listen=${this.listening})`);
    if (!this.listening || this.state !== 'running') return;
    this.listening = false;
    this.state = 'paused';
    await (this.opts.pauseStream || defaultPauseStream)(this.stream);
  }
  async destroy() { window.__vadLog.push(`destroy(${this.state})`); if (this.state === 'running') await this.pause(); this.state = 'destroyed'; this.listening = false; }
  speak() { window.__vadLog.push('speak'); this.opts.onSpeechStart(); this.opts.onSpeechEnd(new Float32Array(3200)); }
}
window.vad = {MicVAD: {new: async (opts) => { window.__vad = new FakeVad(opts); return window.__vad; }}};

let turns = 0;
const baseFetch = (url) => {
  const u = String(url);
  const json = (o) => Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(o)});
  if (u === '/api/session') return json({tab_id: 't', autonomous_after_seconds: 999, autonomous_interval_seconds: 999});
  if (u === '/api/health') return json({ok: true});
  if (u.startsWith('/api/presence')) return json({status: 'ok', returned: false});
  if (u.startsWith('/api/config')) return json({wake_phrases: [], autonomous_after_seconds: 999});
  if (u.startsWith('/api/realtime')) return json({mode: 'voxeo'});
  if (u.startsWith('/api/return-greeting')) return json({skipped: true});
  if (u.startsWith('/api/autonomous-turn')) return json({skipped: true});
  // A voice turn ends the capture mid-flight when asked to, which is where the iPhone failure
  // used to land: the answer comes back and the resume has nowhere to go.
  if (u.startsWith('/api/voice-turn')) {
    turns += 1;
    const result = json({text: 'hello', reply: 'hi there', audio_url: `/audio/answer${turns}.wav`});
    if (window.__killDuringTurn) result.then(() => { if (current) current.track.silentKill(); });
    return result;
  }
  return json({});
};
window.fetch = baseFetch;

window.__counters = () => ({gumCalls, gumPrompts, plays: plays.slice(), turns});
window.__stream = () => current;
window.__status = () => document.getElementById('status').textContent;
window.__errorHidden = () => document.getElementById('error').hidden;
window.__wait = (ms) => new Promise((r) => setTimeout(r, ms));
window.__tap = async () => {
  document.getElementById('status-orb').dispatchEvent(new MouseEvent('click', {bubbles: true}));
  await window.__wait(40);
};
window.__check = check;
window.__setKill = (v) => { window.__killDuringTurn = v; };
window.__setRefuse = (v) => { window.__forceRefuse = v; };
window.__touch = () => document.dispatchEvent(new MouseEvent('pointerdown', {bubbles: true}));
