import { Backend, DemoError, backendURL, delay, sessionPath } from './api.mjs';
import { sessionSummary } from './presentation.mjs';

const $ = id => document.getElementById(id);
const preferencesKey = 'htx-tablet-web.settings';
const tokenKey = 'htx-tablet-web.token';
function read(store, key) { try { return JSON.parse(store.getItem(key) || 'null'); } catch { return null; } }
function write(store, key, value) { try { store.setItem(key, JSON.stringify(value)); } catch { /* Storage may be disabled; this visit still works. */ } }
function clientId() {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(n => n.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
// Do not put backend tokens in persistent preferences or the page URL.
let persistent, tabStorage;
try { persistent = localStorage; tabStorage = sessionStorage; } catch { /* Private/restricted browsing. */ }
const defaults = new URL(location.origin); defaults.port = '8100';
let prefs = { base: defaults.origin, clientId: clientId(), configured: false, source: '', ...read(persistent, preferencesKey) };
let savedToken = read(tabStorage, tokenKey);
let token = savedToken?.base === prefs.base ? savedToken.value : '';
let operation = null;
let listing = null;
let image = $('video');
let started = false;

function persist() { write(persistent, preferencesKey, prefs); }
persist();
function closeDialog(id) { $(id).close(); }
function showDialog(id) {
  for (const dialog of document.querySelectorAll('dialog[open]')) if (dialog.id !== id) dialog.close();
  if (!$(id).open) $(id).showModal();
}
function title(source = prefs.source) {
  $('source-title').textContent = source === 'external-camera' ? 'External camera' : 'Pre-recorded videos';
  $('source-name').hidden = !source || source === 'external-camera';
  $('source-name').textContent = source === 'external-camera' ? '' : source;
  $('choose-source').textContent = source ? 'Change source' : 'Start demo';
}
function state(label, detail, { running = false, busy = false } = {}) {
  $('connection').textContent = label;
  $('connection').classList.toggle('connected', running);
  $('notice').hidden = running;
  $('notice-title').textContent = label;
  $('notice-detail').textContent = detail;
  $('spinner').hidden = !busy;
  $('retry').hidden = busy;
  $('retry').textContent = label === 'Ready' ? 'Start demo' : 'Retry now';
  $('summary').hidden = !running;
}
function renderSummary(value) {
  const summary = sessionSummary(value);
  $('hazards').textContent = summary.heading;
  $('assistance').textContent = summary.assistance;
  $('assistance').hidden = !summary.assistance;
  $('assistance').classList.toggle('unavailable', summary.qwen === false);
  $('counts').textContent = summary.counts;
  const lines = summary.lines.map(text => { const p = document.createElement('p'); p.textContent = text; return p; });
  $('extinguishers').replaceChildren(...lines);
}
function stopViewer() {
  operation?.abort(); operation = null;
  image.removeAttribute('src'); image.hidden = true;
  $('summary').hidden = true;
}
function getBackend() {
  if (location.protocol === 'https:' && new URL(prefs.base).protocol === 'http:') {
    throw new DemoError('mixed', 'This page uses HTTPS, but the backend uses HTTP. Open this web interface over HTTP on your local network.');
  }
  return new Backend(prefs.base, token, prefs.clientId);
}
function openSettings() {
  $('backend-url').value = prefs.base;
  $('backend-token').value = '';
  $('clear-token').checked = false;
  $('clear-token-label').hidden = !token;
  $('token-note').textContent = token ? 'A token is saved for this tab. Leave blank to keep it for this backend.' : 'Your token stays in this browser tab.';
  $('settings-error').hidden = true;
  showDialog('settings-dialog');
}
function chooseSource() {
  if (!prefs.configured) return openSettings();
  showDialog('source-dialog');
}

// JSON pushes match the Android client. Periodic GETs also keep the viewer usable
// when a network blocks WebSockets, and distinguish errors from a frozen stream.
async function watch(api, session, signal, onRunning) {
  let showedFrame = false;
  const path = sessionPath(session.id);
  let streamError = false;
  const replacement = new Image();
  replacement.id = 'video'; replacement.alt = 'Annotated camera feed';
  replacement.referrerPolicy = 'no-referrer';
  replacement.hidden = true;
  image.replaceWith(replacement); image = replacement;
  replacement.onerror = () => { streamError = true; };
  replacement.src = api.url(`${path}/stream/annotated`, true).href;
  let latest = session;
  let pushedFailure = null;
  let socket;
  let socketRetry = null;
  const receive = message => {
    if (message.type === 'hello' || message.type === 'end') latest = message.session;
    else if (message.type === 'state') latest = { ...latest, ...message };
    if (latest && ['error', 'finished', 'stopped'].includes(latest.status)) {
      pushedFailure = new DemoError('ended', latest.status === 'error'
        ? 'The video source could not be processed. Check the camera stream or recording, then retry.'
        : 'The demo has ended. Choose a source or retry.');
    }
    if (latest?.state) renderSummary(latest.state);
  };
  const connectSocket = () => {
    if (signal.aborted) return;
    const url = api.url(`${path}/events`, true);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    try {
      socket = new WebSocket(url);
      socket.onmessage = event => {
        if (signal.aborted) return;
        try { receive(JSON.parse(event.data)); } catch { /* REST state remains available. */ }
      };
      socket.onclose = event => {
        if (signal.aborted) return;
        if (event.code === 4401) pushedFailure = new DemoError('auth', 'Check the backend token in Settings.');
        else socketRetry = setTimeout(connectSocket, 5000);
      };
      socket.onerror = () => {}; // Polling diagnoses errors without exposing transport details.
    } catch { /* Poll over HTTP if WebSocket is unavailable. */ }
  };
  const cleanup = () => {
    clearTimeout(socketRetry);
    if (socket) { socket.onclose = null; socket.close(); }
    replacement.onerror = null; replacement.removeAttribute('src'); replacement.hidden = true;
  };
  signal.addEventListener('abort', cleanup, { once: true });
  connectSocket();
  let nextPoll = 0;
  const begun = Date.now();
  try {
    while (!signal.aborted) {
      if (Date.now() >= nextPoll) {
        receive({ type: 'hello', session: await api.request(path, { signal }) });
        nextPoll = Date.now() + 1500;
      }
      if (pushedFailure) throw pushedFailure;
      if (streamError) throw new DemoError('stream', 'Video connection lost. Retrying automatically.');
      if (replacement.naturalWidth > 0 && replacement.naturalHeight > 0) {
        if (!showedFrame) { showedFrame = true; onRunning(); }
        replacement.hidden = false;
        renderSummary(latest?.state);
        state('Running', 'Exit and extinguisher monitoring', { running: true });
      } else if (Date.now() - begun > 90000) {
        throw new DemoError('source', 'No video is arriving. Check that the external camera is streaming or choose another recording.');
      }
      await delay(250, signal);
    }
  } finally { signal.removeEventListener('abort', cleanup); cleanup(); }
}
async function startSource(source) {
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  listing?.abort();
  stopViewer();
  prefs = { ...prefs, source }; persist(); title();
  started = true;
  const controller = new AbortController(); operation = controller;
  const { signal } = controller;
  let attempt = 0;
  while (!signal.aborted) {
    state(attempt ? 'Reconnecting…' : 'Starting demo…', source === 'external-camera' ? 'Connecting to external camera…' : 'Preparing the selected video…', { busy: true });
    try {
      const api = getBackend();
      const session = await api.openSource(source, signal, detail => {
        if (!signal.aborted) state('Starting demo…', detail, { busy: true });
      });
      if (signal.aborted) return;
      await watch(api, session, signal, () => { attempt = 0; });
    } catch (error) {
      if (signal.aborted || error.name === 'AbortError') return;
      if (!['network', 'busy', 'stream'].includes(error.code)) {
        state('Could not start demo', error.message || 'Choose another source or check Settings.');
        return;
      }
      state(error.code === 'network' ? 'Backend unavailable' : 'Reconnecting…', error.message, { busy: false });
      try { await delay(Math.min(30000, 2000 * 2 ** attempt++), signal); } catch { return; }
    }
  }
}
async function listVideos() {
  listing?.abort(); listing = new AbortController();
  const controller = listing;
  $('videos-list').replaceChildren();
  $('videos-message').textContent = 'Checking demo availability…';
  $('videos-progress').hidden = false; $('reload-videos').disabled = true;
  showDialog('videos-dialog');
  try {
    const { videos = [] } = await getBackend().request('/api/videos', { signal: controller.signal });
    if (controller.signal.aborted) return;
    $('videos-message').textContent = videos.length ? 'Choose a video.' : 'No demo videos are available. Ask the demo administrator.';
    for (const name of videos) {
      const button = document.createElement('button');
      button.className = 'outlined wide'; button.textContent = name;
      button.addEventListener('click', () => startSource(name));
      $('videos-list').append(button);
    }
  } catch (error) {
    if (!controller.signal.aborted) $('videos-message').textContent = error.message;
  } finally {
    if (listing === controller) { $('videos-progress').hidden = true; $('reload-videos').disabled = false; }
  }
}
$('settings-form').addEventListener('submit', event => {
  event.preventDefault();
  let base;
  try { base = backendURL($('backend-url').value); } catch (error) {
    $('settings-error').textContent = error.message; $('settings-error').hidden = false; return;
  }
  stopViewer(); listing?.abort();
  const sameHost = base === prefs.base;
  token = $('clear-token').checked ? '' : $('backend-token').value.trim() || (sameHost ? token : '');
  prefs = { ...prefs, base, configured: true, source: sameHost ? prefs.source : '' };
  write(tabStorage, tokenKey, { base, value: token }); persist();
  $('backend-token').value = '';
  started = false;
  closeDialog('settings-dialog'); title();
  state('Ready', 'Choose a source to start the demo.');
});
$('choose-source').addEventListener('click', chooseSource);
$('settings-button').addEventListener('click', openSettings);
$('retry').addEventListener('click', () => started && prefs.source ? startSource(prefs.source) : chooseSource());
$('recorded-source').addEventListener('click', listVideos);
$('external-source').addEventListener('click', () => startSource('external-camera'));
$('reload-videos').addEventListener('click', listVideos);
$('video-settings').addEventListener('click', openSettings);
for (const button of document.querySelectorAll('[data-close]')) button.addEventListener('click', () => closeDialog(button.dataset.close));
$('videos-dialog').addEventListener('close', () => listing?.abort());
// Close only the viewer on navigation. Backend sessions keep running so either
// the Android app or this page can resume the same feed later.
window.addEventListener('pagehide', () => { listing?.abort(); stopViewer(); });
window.addEventListener('pageshow', event => {
  if (event.persisted && started && prefs.source && prefs.configured) startSource(prefs.source);
});
title();
if (!prefs.configured) openSettings();
