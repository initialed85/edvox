// Drives a fake conversation through the real app.js and asserts the microphone and playback
// behave the way a phone needs them to. Regression cover for the bug where every turn stopped
// the capture and re-asked for permission, which iOS refused mid-answer.
(async () => {
  const wait = window.__wait, tap = window.__tap, status = window.__status, check = window.__check;
  const counters = window.__counters, stream = window.__stream, setKill = window.__setKill;
  const setRefuse = window.__setRefuse, touch = window.__touch;
  const track = () => {
    check('microphone stream exists', Boolean(stream()), JSON.stringify(counters()));
    return stream();
  };
  try {
    await wait(200);                       // boot() attempts a gesture-less getUserMedia, iOS-style
    check('gesture-less boot waits for a tap instead of erroring',
      /Tap the light to start/.test(status()) && window.__errorHidden() === true, status());

    let c = counters();
    await tap();
    check('the tap acquires the microphone',
      counters().gumCalls === c.gumCalls + 1 && status() === 'Listening…',
      `gum=${counters().gumCalls} status=${status()}`);

    // Turn 1: the round trip must not ask for the microphone again.
    c = counters();
    window.__vadLog = [];
    window.__vad.speak();
    await wait(150);
    check('the turn paused the detector (so the answer cannot self-trigger it)',
      window.__vadLog.some((entry) => entry.startsWith('pause(')), JSON.stringify(window.__vadLog));
    check('a full turn raises no new permission request', counters().gumCalls === c.gumCalls,
      `delta=${counters().gumCalls - c.gumCalls}`);
    check('the answer is played, not just fetched',
      counters().plays.slice(c.plays.length).some((p) => p.includes('/audio/answer')),
      JSON.stringify(counters().plays.slice(c.plays.length)));
    check('the element carrying answers was unlocked inside the tap',
      counters().plays[0] && counters().plays[0].startsWith('data:'), String(counters().plays[0]));
    check('returns to listening after the answer', status() === 'Listening…', status());
    const tr = track();
    if (tr) {
      check('capture is muted between turns, never stopped',
        tr.track.readyState === 'live' && tr.track.enabled === true,
        `readyState=${tr.track.readyState} enabled=${tr.track.enabled}`);
    }

    // Turn 2: the browser ends capture while the request is in flight - the reported failure.
    c = counters();
    setKill(true);
    window.__vad.speak();
    await wait(250);
    setKill(false);
    check('interrupted capture causes no gesture-less request', counters().gumCalls === c.gumCalls,
      `delta=${counters().gumCalls - c.gumCalls}`);
    check('interrupted capture asks for a tap rather than claiming to listen',
      /Tap the light/.test(status()), status());

    c = counters();
    await tap();
    check('tapping restores listening after an interruption',
      counters().gumCalls === c.gumCalls + 1 && status() === 'Listening…',
      `gum=${counters().gumCalls} status=${status()}`);

    c = counters();
    window.__vad.speak();
    await wait(200);
    check('and the next turn works normally',
      counters().gumCalls === c.gumCalls && status() === 'Listening…',
      `gum=${counters().gumCalls} status=${status()}`);

    // Turn 3: playback itself is refused (the gesture expired). The old code swallowed that and
    // the answer was never heard, which is what "it fails as it goes to answer" looked like.
    c = counters();
    setRefuse(true);
    window.__vad.speak();
    await wait(200);
    check('a refused answer says so instead of going silent', /Tap to hear the answer/.test(status()), status());
    setRefuse(false);
    touch();
    await wait(200);
    check('the next touch plays the refused answer',
      counters().plays.slice(c.plays.length).some((p) => p.includes('/audio/answer')),
      JSON.stringify(counters().plays.slice(c.plays.length)));
    await wait(150);
    check('and listening resumes once it has played', status() === 'Listening…', status());
    window.out(`prompts the user would have seen: ${counters().gumPrompts}`);
  } catch (e) {
    window.out('RUNNER ERROR ' + e.message + ' ' + (e.stack || ''));
    window.results.push({ok: false, line: 'FAIL runner error'});
  }
  if (window.__errs.length) window.out('page errors: ' + window.__errs.join(' | '));
  document.title = window.results.length >= 15 && window.results.every((r) => r.ok) ? 'ALL-PASS' : 'SOME-FAIL';
})();
