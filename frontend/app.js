// Exit Monitor web app — talks to the FastAPI backend (backend/).
//
// Backend URL and token come from ?api=…&token=… , the inputs (remembered in this
// browser), or — when the page is served by the backend itself — the page's origin.
// Left side: the two live MJPEG streams of the running session.
// Right side: mockup only; live events are already received (see onEvent) for later.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const store = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const sameOrigin = location.protocol.startsWith("http") ? location.origin : "";
$("api").value = params.get("api") || store.get("exitmon.api") || sameOrigin || "http://localhost:8100";
$("token").value = params.get("token") || store.get("exitmon.token");

let session = null;          // current session id
let ws = null;
let lastEvent = null;

// ── helpers ──────────────────────────────────────────────────────────────────
const api = () => $("api").value.trim().replace(/\/$/, "");
const token = () => $("token").value.trim();
const withToken = (url) => (token() ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token())}` : url);

async function call(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const res = await fetch(api() + path, { ...options, headers });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

function setButtons({ connected, running }) {
  $("video").disabled = !connected || running;
  $("start").disabled = !connected || running;
  $("stop").disabled = !running;
}

// ── streams (left side) ─────────────────────────────────────────────────────
function showStream(name, sid) {
  const img = $(`img-${name}`);
  const box = img.parentElement;
  const conn = $(`conn-${name}`);
  const set = (state, text) => {
    conn.className = `conn ${state}`;
    conn.textContent = text;
    box.classList.toggle("live", state === "live");
  };
  if (!sid) {
    img.removeAttribute("src");
    set("", "idle");
    return;
  }
  img.onload = () => set("live", "live");
  img.onerror = () => {
    if (session !== sid) return;
    set("down", "reconnecting…");
    setTimeout(() => session === sid && (img.src = withToken(`${api()}/api/sessions/${sid}/stream/${name}?t=${Date.now()}`)), 2000);
  };
  set("", "connecting…");
  img.src = withToken(`${api()}/api/sessions/${sid}/stream/${name}?t=${Date.now()}`);
}

// ── live events ──────────────────────────────────────────────────────────────
function onEvent(ev) {
  lastEvent = ev;
  console.debug("exit event", ev);          // the right-side panel will use these later
}

function openEvents(sid) {
  if (ws) ws.close();
  const url = withToken(`${api().replace(/^http/, "ws")}/api/sessions/${sid}/events`);
  ws = new WebSocket(url);
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.type === "event") onEvent(data.event);
    if (data.type === "state") {
      const exits = (data.state.exits || []).filter((e) => e.visible);
      const summary = exits.map((e) => `#${e.id} ${e.status_text}`).join(", ") || "no exits in view";
      const last = lastEvent ? ` · last: ${lastEvent.text}` : "";
      setStatus(`Session ${sid} ${data.status} · t=${data.state.video_time_s ?? 0}s · ${data.state.fps ?? 0} fps · ${summary}${last}`);
    }
    if (data.type === "end") {
      const s = data.session;
      setStatus(`Session ${sid} ${s.status}${s.error ? " — " + s.error : ""}`, s.status === "error");
      attach(null);
    }
  };
  ws.onclose = (e) => {
    if (e.code === 4401) setStatus("Wrong or missing token", true);
  };
}

function attach(sid) {
  session = sid;
  showStream("original", sid);
  showStream("annotated", sid);
  if (sid) openEvents(sid);
  else if (ws) { ws.close(); ws = null; }
  setButtons({ connected: true, running: Boolean(sid) });
}

// ── buttons ──────────────────────────────────────────────────────────────────
async function connect() {
  store.set("exitmon.api", api());
  store.set("exitmon.token", token());
  setStatus("Connecting…");
  try {
    const info = await call("/api/info");
    const select = $("video");
    select.innerHTML = info.videos.map((v) => `<option value="${v}">${v}</option>`).join("")
      || "<option value=''>no videos on server</option>";
    const m = info.models;
    setStatus(`Connected · LocateAnything ${m.locate_anything.up ? "up" : "DOWN"} · Qwen ${m.qwen.up ? "up" : "DOWN"} · ${info.videos.length} videos`,
              !(m.locate_anything.up && m.qwen.up));
    const running = info.sessions.find((s) => ["starting", "running"].includes(s.status));
    if (running && info.videos.includes(running.source)) select.value = running.source;
    attach(running ? running.id : null);
  } catch (err) {
    setStatus(`Cannot reach backend: ${err.message}`, true);
    setButtons({ connected: false, running: false });
  }
}

async function start() {
  const source = $("video").value;
  if (!source) return;
  setStatus(`Starting ${source}…`);
  try {
    const s = await call("/api/sessions", { method: "POST", body: JSON.stringify({ source, loop: true }) });
    attach(s.id);
  } catch (err) {
    setStatus(`Start failed: ${err.message}`, true);
  }
}

async function stop() {
  if (!session) return;
  try {
    await call(`/api/sessions/${session}`, { method: "DELETE" });
    setStatus(`Stopping ${session}…`);
  } catch (err) {
    setStatus(`Stop failed: ${err.message}`, true);
  }
}

$("connect").addEventListener("click", connect);
$("start").addEventListener("click", start);
$("stop").addEventListener("click", stop);
if ($("api").value) connect();

// Mockup: dismissable pop-up (not wired to detections yet)
$("toast-close").addEventListener("click", () => { $("toast").hidden = true; });
