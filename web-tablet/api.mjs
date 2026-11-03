export class DemoError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
export function backendURL(value) {
  let url;
  try { url = new URL(value.trim()); } catch { throw new DemoError('settings', 'Enter an HTTP or HTTPS backend address without a path or credentials.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.pathname !== '/' || url.search || url.hash || url.username || url.password) {
    throw new DemoError('settings', 'Enter an HTTP or HTTPS backend address without a path or credentials.');
  }
  return url.origin;
}
export function sessionPath(id, suffix = '') {
  if (!/^[A-Za-z0-9_-]+$/.test(id)) throw new DemoError('session', 'Invalid session response.');
  return `/api/sessions/${id}${suffix}`;
}
export function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Cancelled', 'AbortError'));
    const aborted = () => { clearTimeout(timer); reject(new DOMException('Cancelled', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', aborted); resolve(); }, ms);
    signal?.addEventListener('abort', aborted, { once: true });
  });
}
export class Backend {
  constructor(base, token, clientId, fetcher = globalThis.fetch.bind(globalThis)) {
    this.base = backendURL(base); this.token = token; this.clientId = clientId; this.fetcher = fetcher;
  }
  url(path, authenticated = false) {
    const url = new URL(path, this.base);
    if (authenticated && this.token) url.searchParams.set('token', this.token);
    return url;
  }
  async request(path, { method = 'GET', body, signal, timeout = 20000 } = {}) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, timeout);
    try {
      const response = await this.fetcher(this.url(path), {
        method, signal: controller.signal, cache: 'no-store', credentials: 'omit',
        headers: { Accept: 'application/json', ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) {
        if ([401, 403].includes(response.status)) throw new DemoError('auth', 'Check the backend token in Settings.');
        if (response.status === 409) throw new DemoError('busy', 'The backend is busy. Wait for the previous session to finish, then retry.');
        if (response.status === 404) throw new DemoError('missing', 'This session or source is no longer available. Retry or choose another source.');
        throw new DemoError('start', 'The demo could not start. Check the selected source and backend.');
      }
      return await response.json();
    } catch (error) {
      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      if (error instanceof DemoError) throw error;
      throw new DemoError('network', 'Cannot reach the backend. Check the connection and Settings. Retrying automatically.');
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
  async validateSource(source, signal) {
    if (source === 'external-camera') {
      const { sources = [] } = await this.request('/api/sources', { signal });
      if (!sources.some(item => item.id === source && item.configured)) throw new DemoError('external', 'External camera is not configured on this backend. Ask the demo administrator to configure it, then retry.');
    } else {
      const { videos = [] } = await this.request('/api/videos', { signal });
      if (!videos.includes(source)) throw new DemoError('video', 'Video is no longer available on the backend. Choose another recording.');
    }
  }
  async openSource(source, signal, progress = () => {}) {
    await this.validateSource(source, signal);
    const { sessions = [] } = await this.request('/api/sessions', { signal });
    const active = sessions.filter(item => ['starting', 'running'].includes(item.status));
    // A fallback may VIEW an Android-owned combined session; it never stops one.
    const matching = active.find(item => item.mode === 'combined' && item.source === source);
    if (matching && active.length === 1) return matching;
    const foreign = active.filter(item => item.demo_client_id !== this.clientId);
    if (foreign.length) throw new DemoError('in_use', 'Another device is running a demo. Choose the same source to view it, or stop that demo on its owning device before changing sources.');
    const schema = await this.request('/openapi.json', { signal });
    const modes = schema.components?.schemas?.SessionRequest?.properties?.mode?.enum || [];
    if (!modes.includes('combined')) throw new DemoError('version', 'This backend does not support combined detection. Connect to the updated tablet backend.');
    for (const session of active) {
      progress('Changing source…');
      await this.request(sessionPath(session.id), { method: 'DELETE', signal });
      let stopped = false;
      for (let attempt = 0; attempt < 60; attempt++) {
        const status = await this.request(sessionPath(session.id), { signal });
        if (['stopped', 'finished', 'error'].includes(status.status)) { stopped = true; break; }
        await delay(500, signal);
      }
      if (!stopped) throw new DemoError('busy', 'Previous session is still stopping. Wait a moment, then retry.');
    }
    progress('Starting demo…');
    return this.request('/api/sessions', {
      method: 'POST', signal, timeout: 180000,
      body: { source, mode: 'combined', loop: source !== 'external-camera', use_yoloe: true,
        use_locate: false, use_qwen: true, require_qwen: false, demo_client_id: this.clientId },
    });
  }
}
