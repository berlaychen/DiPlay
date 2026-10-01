// WebCodecs lifecycle regression checks. Fake codecs exercise state transitions;
// browser_smoke.py separately validates real H.264 decoding and audio playback.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/client.js'), 'utf8');

function harness() {
  const elements = new Map(), draws = [], sent = [], instances = [], timers = new Map();
  let nextTimer = 1;
  const context = { drawImage: frame => draws.push(frame), clearRect() {} };
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', value: '', hidden: false, checked: false, width: 640, height: 360,
      getContext: () => context, querySelector: name => element(id + '/' + name),
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 640, height: 360 }),
      setPointerCapture() {}, releasePointerCapture() {},
      addEventListener() {}, removeEventListener() {}, showModal() {}, close() {},
    });
    return elements.get(id);
  }
  class Codec {
    static support = async () => ({ supported: true });
    static isConfigSupported(config) { return Codec.support(config); }
    constructor(callbacks) { this.callbacks = callbacks; this.state = 'unconfigured'; this.decodeQueueSize = 0; instances.push(this); }
    configure(config) { assert.notEqual(this.state, 'closed'); this.config = config; this.state = 'configured'; }
    close() { this.state = 'closed'; }
    reset() { assert.notEqual(this.state, 'closed'); this.state = 'unconfigured'; }
    decode(chunk) { assert.equal(this.state, 'configured'); this.lastChunk = chunk; }
    fail() { this.state = 'closed'; this.callbacks.error(new Error('synthetic device loss')); }
  }
  const globals = {
    document: { getElementById: element, querySelectorAll: () => [], documentElement: {}, addEventListener() {}, removeEventListener() {}, querySelector: () => null },
    window: { VideoDecoder: Codec, addEventListener() {} }, VideoDecoder: Codec,
    EncodedVideoChunk: class { constructor(options) { Object.assign(this, options); } },
    WebSocket: { OPEN: 1 }, isSecureContext: true,
    setTimeout: callback => { const id = nextTimer++; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    TextDecoder, Uint8Array, DataView, performance, console,
    sendTest: value => sent.push(JSON.parse(value)),
  };
  const sandbox = vm.createContext(globals);
  const run = script => vm.runInContext(script, sandbox);
  run(fs.readFileSync(path.join(__dirname, '../web/input.js'), 'utf8'));
  run(source);
  run('ws={readyState:WebSocket.OPEN,send:sendTest};');
  return {
    run, Codec, instances, elements, draws, sent, timers,
    configure: () => run("configure({codec:'avc1.42C01E',width:640,height:360})"),
    async tick() {
      const first = timers.entries().next().value;
      if (first) { timers.delete(first[0]); first[1](); }
      await new Promise(setImmediate);
    },
  };
}
function frame() {
  return { displayWidth: 640, displayHeight: 360, released: 0, close() { this.released++; } };
}

test('a terminal decoder error creates a new decoder and requests an IDR', async () => {
  const h = harness(); await h.configure();
  const failed = h.instances[0]; failed.fail();
  assert.equal(h.run('decoder'), null);
  assert.equal(h.timers.size, 1);
  await h.tick();
  const recovered = h.instances[1];
  assert.equal(recovered.state, 'configured');
  assert.equal(recovered.config.hardwareAcceleration, 'prefer-software');
  assert.ok(h.sent.filter(x => x.op === 'keyframe').length >= 2);
  const stale = frame(), fresh = frame();
  failed.callbacks.output(stale); recovered.callbacks.output(fresh);
  assert.equal(stale.released, 1); assert.equal(fresh.released, 1);
  assert.equal(h.draws.length, 1); assert.equal(h.draws[0], fresh);
});

test('video_stop closes the decoder and ignores already queued output', async () => {
  const h = harness(); await h.configure();
  const old = h.instances[0]; h.run("receive({event:'video_stop'})");
  assert.equal(old.state, 'closed'); assert.equal(h.run('configuration'), null);
  const stale = frame(); old.callbacks.output(stale);
  assert.equal(stale.released, 1); assert.equal(h.draws.length, 0);
  assert.equal(h.elements.get('placeholder').hidden, false);
});

test('stopping invalidates an in-flight asynchronous capability probe', async () => {
  const h = harness(); let finish;
  h.Codec.support = () => new Promise(resolve => { finish = resolve; });
  const pending = h.configure(); h.run('stopVideo()'); finish({ supported: true }); await pending;
  assert.equal(h.instances.length, 0); assert.equal(h.run('decoder'), null);
});

test('a new video config cannot be replaced by an old error callback', async () => {
  const h = harness(); await h.configure(); const old = h.instances[0];
  await h.configure(); const current = h.instances[1];
  old.fail(); assert.equal(h.run('decoder'), current); assert.equal(h.timers.size, 0);
});

test('stopping cancels a scheduled recovery', async () => {
  const h = harness(); await h.configure(); h.instances[0].fail(); h.run('stopVideo()');
  await h.tick(); assert.equal(h.instances.length, 1); assert.equal(h.run('configuration'), null);
});

test('repeated failures are bounded and require explicit reconnect after three retries', async () => {
  const h = harness(); await h.configure();
  for (let i = 0; i < 3; i++) { h.instances.at(-1).fail(); await h.tick(); }
  h.instances.at(-1).fail();
  assert.equal(h.instances.length, 4); assert.equal(h.timers.size, 0);
  assert.match(h.elements.get('diagnostic').textContent, /failed repeatedly/);
});
