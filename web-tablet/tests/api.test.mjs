import test from 'node:test';
import assert from 'node:assert/strict';
import { Backend, DemoError, backendURL, sessionPath, delay } from '../api.mjs';

const client = '00000000-0000-4000-8000-000000000001';
const schema = { components: { schemas: { SessionRequest: { properties: { mode: { enum: ['exits', 'combined'] } } } } } };
const active = { id: 'android', source: 'one.MOV', mode: 'combined', status: 'running', demo_client_id: 'android-owner' };
function fake(...replies) {
  const calls = [];
  const backend = new Backend('http://backend:8100', 'private-token', client, async (url, init) => {
    calls.push({ url: new URL(url), ...init, body: init.body ? JSON.parse(init.body) : undefined });
    const next = replies.shift();
    assert.ok(next, `Unexpected request: ${url.pathname}`);
    if (next instanceof Error) throw next;
    const [status, data] = next;
    return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
  });
  return { backend, calls, remaining: () => replies.length };
}
const videos = [200, { videos: ['one.MOV', 'two.MOV'] }];
const sources = [200, { sources: [{ id: 'external-camera', configured: true }] }];
const signal = () => new AbortController().signal;

test('backend URL permits LAN hosts but refuses paths, credentials, fragments and non-HTTP schemes', () => {
  assert.equal(backendURL(' http://192.168.1.20:8100/ '), 'http://192.168.1.20:8100');
  assert.equal(backendURL('https://demo.example'), 'https://demo.example');
  for (const url of ['http://host/api', 'http://user:secret@host', 'http://host/?token=x', 'http://host/#x', 'file:///tmp', 'bad']) {
    assert.throws(() => backendURL(url), DemoError);
  }
});
test('authenticated JSON requests use a header and do not leak token into their URL', async () => {
  const { backend, calls } = fake(videos);
  await backend.request('/api/videos');
  assert.equal(calls[0].headers.Authorization, 'Bearer private-token');
  assert.equal(calls[0].url.search, '');
  assert.equal(calls[0].credentials, 'omit');
});
test('MJPEG/event URLs use existing query auth; token round-trips with special characters', () => {
  const backend = new Backend('http://backend', 'a&b #?=', client);
  assert.equal(backend.url('/api/sessions/abc/stream/annotated', true).searchParams.get('token'), 'a&b #?=');
  assert.throws(() => sessionPath('../other'), DemoError);
});
test('fallback attaches to an Android recording without POST or DELETE', async () => {
  const { backend, calls } = fake(videos, [200, { sessions: [active] }]);
  assert.equal((await backend.openSource('one.MOV', signal())).id, 'android');
  assert.ok(calls.every(item => item.method === 'GET'));
});
test('fallback attaches to an Android external camera session', async () => {
  const { backend, calls } = fake(sources, [200, { sessions: [{ ...active, source: 'external-camera' }] }]);
  assert.equal((await backend.openSource('external-camera', signal())).id, 'android');
  assert.ok(calls.every(item => item.method === 'GET'));
});
test('a new recording starts combined detection with the Android model/fallback flags', async () => {
  const { backend, calls, remaining } = fake(videos, [200, { sessions: [] }], [200, schema], [201, { id: 'new' }]);
  assert.equal((await backend.openSource('two.MOV', signal())).id, 'new');
  assert.deepEqual(calls.at(-1).body, { source: 'two.MOV', mode: 'combined', loop: true,
    use_yoloe: true, use_locate: false, use_qwen: true, require_qwen: false, demo_client_id: client });
  assert.equal(remaining(), 0);
});
test('external camera creates a non-looping combined session without requiring a video listing', async () => {
  const { backend, calls } = fake(sources, [200, { sessions: [] }], [200, schema], [201, { id: 'external' }]);
  await backend.openSource('external-camera', signal());
  assert.equal(calls.at(-1).body.loop, false);
  assert.ok(calls.every(item => item.url.pathname !== '/api/videos'));
});
test('missing external configuration is explained before stopping a session', async () => {
  const { backend, calls } = fake([200, { sources: [{ id: 'external-camera', configured: false }] }]);
  await assert.rejects(backend.openSource('external-camera', signal()), { code: 'external' });
  assert.equal(calls.length, 1);
});
test('missing recording never stops a running session', async () => {
  const { backend, calls } = fake(videos);
  await assert.rejects(backend.openSource('missing.MOV', signal()), { code: 'video' });
  assert.equal(calls.length, 1);
});
test('selecting another source cannot stop a foreign Android session', async () => {
  const { backend, calls } = fake(videos, [200, { sessions: [active] }]);
  await assert.rejects(backend.openSource('two.MOV', signal()), { code: 'in_use' });
  assert.ok(calls.every(item => item.method === 'GET'));
});
test('multiple active sessions are not mistaken for one safe-to-reuse session', async () => {
  const { backend, calls } = fake(videos, [200, { sessions: [active, { ...active, id: 'second' }] }]);
  await assert.rejects(backend.openSource('one.MOV', signal()), { code: 'in_use' });
  assert.ok(calls.every(item => item.method === 'GET'));
});
test('only this browser’s own session is stopped, and replacement waits for stopped status', async () => {
  const { backend, calls } = fake(videos, [200, { sessions: [{ ...active, demo_client_id: client }] }], [200, schema],
    [200, { status: 'running' }], [200, { status: 'stopped' }], [201, { id: 'replacement' }]);
  await backend.openSource('two.MOV', signal());
  assert.deepEqual(calls.map(item => item.method), ['GET', 'GET', 'GET', 'DELETE', 'GET', 'POST']);
  assert.equal(calls[3].url.pathname, '/api/sessions/android');
});
test('old backend without combined support cannot silently start exit-only detection', async () => {
  const { backend, calls } = fake(videos, [200, { sessions: [] }], [200, { components: {} }]);
  await assert.rejects(backend.openSource('one.MOV', signal()), { code: 'version' });
  assert.ok(calls.every(item => item.method === 'GET'));
});
test('an already-created matching session is recovered after a lost creation response', async () => {
  const { backend, calls } = fake(videos, [200, { sessions: [{ ...active, demo_client_id: client }] }]);
  assert.equal((await backend.openSource('one.MOV', signal())).id, active.id);
  assert.ok(calls.every(item => item.method === 'GET'));
});
test('auth, session conflicts, missing sessions and network errors have actionable categories', async () => {
  for (const [status, code] of [[401, 'auth'], [403, 'auth'], [409, 'busy'], [404, 'missing'], [500, 'start']]) {
    const { backend } = fake([status, {}]);
    await assert.rejects(backend.request('/api/videos'), { code });
  }
  const { backend } = fake(new TypeError('Failed to fetch'));
  await assert.rejects(backend.request('/api/videos'), { code: 'network' });
});
test('cancelling a source selection interrupts its request rather than retrying it', async () => {
  const controller = new AbortController();
  const backend = new Backend('http://backend', '', client, async (_url, { signal }) => {
    controller.abort();
    assert.equal(signal.aborted, true);
    throw new DOMException('Aborted', 'AbortError');
  });
  await assert.rejects(backend.openSource('one.MOV', controller.signal), { name: 'AbortError' });
});
test('retry waits are cancellable', async () => {
  const controller = new AbortController();
  const waiting = delay(5000, controller.signal);
  controller.abort();
  await assert.rejects(waiting, { name: 'AbortError' });
});
