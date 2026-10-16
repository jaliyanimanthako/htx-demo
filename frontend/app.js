// Exit Monitor web app — live mode.
//
// The video (file or camera) plays HERE, in the browser. Frames are grabbed from the
// <video>, shrunk, JPEG-encoded and sent to the backend over one WebSocket
// (/api/live). The backend runs the detection pipeline and returns only JSON —
// exit boxes, statuses, obstructions, events — which are drawn on a canvas over
// the local video. One frame is in flight at a time, so nothing ever queues up.
//
// Backend URL / token: ?api=…&token=… , the inputs (remembered in this browser),
// or the page's origin when the backend serves the page.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const store = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const SEND_WIDTH = 960;       // px — the pipeline's processing width
const JPEG_QUALITY = 0.75;
const MAX_FPS = 15;           // upper bound on frames sent per second

const sameOrigin = location.protocol.startsWith("http") ? location.origin : "";
$("api").value = params.get("api") || store.get("exitmon.api") || sameOrigin || "http://localhost:8100";
$("token").value = params.get("token") || store.get("exitmon.token");

const video = $("video");
const overlay = $("overlay");
const octx = overlay.getContext("2d");
const grab = document.createElement("canvas");
const gctx = grab.getContext("2d");

let connected = false;
let ws = null;
let detecting = false;
let inFlight = false;
let seq = 0;
let sentAt = new Map();
let latest = null;            // last result from the server
let lastSend = 0;
const rtts = [];
const resultTimes = [];
let lastEvent = null;
let modelsText = "";

// ── helpers ──────────────────────────────────────────────────────────────────
const api = () => $("api").value.trim().replace(/\/$/, "");
const token = () => $("token").value.trim();
const withToken = (url) => (token() ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token())}` : url);

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}
function setPill(id, state, text) {
  $(id).className = `conn ${state}`;
  $(id).textContent = text;
}
const hasSource = () => Boolean(video.currentSrc || video.srcObject);

function updateButtons() {
  $("detect").disabled = !connected || !hasSource();
  $("detect").textContent = detecting ? "Stop detection" : "Start detection";
  $("detect").classList.toggle("danger", detecting);
}

// ── backend ──────────────────────────────────────────────────────────────────
async function connect() {
  store.set("exitmon.api", api());
  store.set("exitmon.token", token());
  setStatus("Connecting…");
  try {
    const headers = token() ? { Authorization: `Bearer ${token()}` } : {};
    const res = await fetch(`${api()}/api/info`, { headers });
    const info = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(info.detail || `${res.status} ${res.statusText}`);
    const m = info.models;
    connected = true;
    setStatus(`Connected · LocateAnything ${m.locate_anything.up ? "up" : "DOWN"} · Qwen ${m.qwen.up ? "up" : "DOWN"}`
              + (hasSource() ? "" : " · open a video or the camera"), !(m.locate_anything.up && m.qwen.up));
  } catch (err) {
    connected = false;
    setStatus(`Cannot reach backend: ${err.message}`, true);
  }
  updateButtons();
}

function startDetection() {
  const url = withToken(`${api().replace(/^http/, "ws")}/api/live`);
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  setPill("det-state", "", "starting…");
  ws.onopen = () => ws.send(JSON.stringify({ type: "start" }));
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.type === "ready") {
      detecting = true;
      inFlight = false;
      const names = { yoloe: "YOLOE", locate_anything: "LocateAnything", qwen: "Qwen" };
      modelsText = Object.entries(data.models).filter(([, on]) => on).map(([k]) => names[k]).join(" + ");
      if (data.skipped && data.skipped.length) modelsText += ` (${data.skipped.join(", ")} down — skipped)`;
      setPill("det-state", "live", "detecting");
      setStatus(`Detection running · ${modelsText}`, Boolean(data.skipped && data.skipped.length));
      updateButtons();
      sendFrame();
    } else if (data.type === "result") {
      onResult(data);
    } else if (data.type === "error") {
      setStatus(`Detection error: ${data.detail}`, true);
      inFlight = false;
    }
  };
  ws.onclose = (e) => {
    const was = detecting;
    detecting = false;
    inFlight = false;
    ws = null;
    setPill("det-state", "", "detection off");
    if (e.code === 4401 || e.code === 1008) setStatus("Wrong or missing token", true);
    else if (was) setStatus("Detection stopped");
    updateButtons();
  };
}

function stopDetection() {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  if (ws) ws.close();
}

// ── frames → server ─────────────────────────────────────────────────────────
function sendFrame() {
  if (!detecting || inFlight || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (video.paused || video.ended || video.readyState < 2 || !video.videoWidth) return;
  const now = performance.now();
  if (now - lastSend < 1000 / MAX_FPS) {
    setTimeout(sendFrame, 1000 / MAX_FPS - (now - lastSend));
    return;
  }
  const scale = Math.min(1, SEND_WIDTH / video.videoWidth);
  grab.width = Math.round(video.videoWidth * scale);
  grab.height = Math.round(video.videoHeight * scale);
  gctx.drawImage(video, 0, 0, grab.width, grab.height);
  const t = video.currentTime;
  inFlight = true;
  lastSend = now;
  grab.toBlob(async (blob) => {
    if (!blob || !ws || ws.readyState !== WebSocket.OPEN) { inFlight = false; return; }
    const jpeg = new Uint8Array(await blob.arrayBuffer());
    const msg = new Uint8Array(12 + jpeg.length);
    const view = new DataView(msg.buffer);
    view.setUint32(0, seq, true);          // uint32 seq
    view.setFloat64(4, t, true);           // float64 video time
    msg.set(jpeg, 12);
    sentAt.set(seq, performance.now());
    seq += 1;
    ws.send(msg.buffer);
  }, "image/jpeg", JPEG_QUALITY);
}

function onResult(r) {
  inFlight = false;
  latest = r;
  const t0 = sentAt.get(r.seq);
  sentAt.delete(r.seq);
  if (t0) rtts.push(performance.now() - t0);
  if (rtts.length > 30) rtts.shift();
  resultTimes.push(performance.now());
  while (resultTimes.length && performance.now() - resultTimes[0] > 2000) resultTimes.shift();
  (r.events || []).forEach(onEvent);

  const rtt = rtts.reduce((a, b) => a + b, 0) / Math.max(1, rtts.length);
  const fps = resultTimes.length / 2;
  const exits = r.exits.map((e) => `#${e.id} ${e.status_text}`).join(", ") || "no exits in view";
  setStatus(`${modelsText} · ${fps.toFixed(1)} results/s · round trip ${rtt.toFixed(0)} ms (server ${r.proc_ms} ms) · ${exits}`
            + (lastEvent ? ` · last: ${lastEvent.text}` : ""));
  sendFrame();
}

function onEvent(ev) {
  lastEvent = ev;
  console.debug("exit event", ev);        // the right-side panel will use these later
}

// keep sending while the video plays (e.g. after a pause / seek)
setInterval(sendFrame, 250);

// ── drawing (every animation frame, locally) ────────────────────────────────
const COLORS = { clear: "#16a34a", partially_obstructed: "#f59e0b", obstructed: "#dc2626", unknown: "#9ca3af" };
const OBSTACLE = "#f97316";

function label(ctx, text, x, y, color, size) {
  ctx.font = `600 ${size}px -apple-system, Segoe UI, sans-serif`;
  const pad = size * 0.4;
  const w = ctx.measureText(text).width + 2 * pad;
  const h = size * 1.5;
  const ty = Math.max(h, y);
  const tx = Math.min(Math.max(0, x), overlay.width - w);
  ctx.fillStyle = color;
  ctx.fillRect(tx, ty - h, w, h);
  ctx.fillStyle = color === COLORS.partially_obstructed ? "#111" : "#fff";
  ctx.fillText(text, tx + pad, ty - size * 0.4);
}

function draw() {
  requestAnimationFrame(draw);
  if (!video.videoWidth) return;
  if (overlay.width !== video.videoWidth || overlay.height !== video.videoHeight) {
    overlay.width = video.videoWidth;
    overlay.height = video.videoHeight;
  }
  const W = overlay.width, H = overlay.height;
  octx.drawImage(video, 0, 0, W, H);
  if (!detecting || !latest) return;
  // sizes in screen pixels, whatever the video resolution / panel size
  const shown = Math.min(overlay.clientWidth / W, overlay.clientHeight / H) || 1;
  const lw = 2 / shown;
  const fs = 13 / shown;
  for (const o of latest.obstacles) {
    const [x1, y1, x2, y2] = o.box;
    octx.strokeStyle = OBSTACLE;
    octx.lineWidth = lw;
    octx.strokeRect(x1 * W, y1 * H, (x2 - x1) * W, (y2 - y1) * H);
    label(octx, o.label + (o.depth === "floor" ? " ✓" : ""), x1 * W, y1 * H, OBSTACLE, fs * 0.85);
  }
  for (const e of latest.exits) {
    const [x1, y1, x2, y2] = e.box;
    const color = COLORS[e.status] || COLORS.unknown;
    octx.strokeStyle = color;
    octx.lineWidth = lw * 1.5;
    octx.strokeRect(x1 * W, y1 * H, (x2 - x1) * W, (y2 - y1) * H);
    const name = `${e.exit_sign ? "EXIT" : "door"} #${e.id}${e.confirmed === null ? " ?" : ""}`;
    label(octx, `${name} ${e.status_text} ${Math.round(e.since_s)}s`, x1 * W, y1 * H, color, fs);
  }
}
requestAnimationFrame(draw);

// ── sources ──────────────────────────────────────────────────────────────────
function sourceChanged(name) {
  latest = null;
  $("box-original").classList.add("live");
  $("box-annotated").classList.add("live");
  setPill("src-state", "live", name);
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "reset" }));
  updateButtons();
}

$("file").addEventListener("change", () => {
  const f = $("file").files[0];
  if (!f) return;
  if (video.srcObject) { video.srcObject.getTracks().forEach((t) => t.stop()); video.srcObject = null; }
  video.src = URL.createObjectURL(f);
  video.loop = true;
  video.play().catch(() => {});
  video.onerror = () => setStatus(`This browser cannot play ${f.name} — try Chrome/Safari, or convert it to MP4 (H.264)`, true);
  sourceChanged(f.name);
});

$("camera").addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 } }, audio: false });
    video.removeAttribute("src");
    video.srcObject = stream;
    await video.play();
    sourceChanged("camera");
  } catch (err) {
    setStatus(`Camera not available: ${err.message} (needs https or localhost)`, true);
  }
});

// optional: ?video=<url> loads a video directly (handy for demos / tests)
if (params.get("video")) {
  video.src = params.get("video");
  video.loop = true;
  video.play().catch(() => {});
  sourceChanged(params.get("video").split("/").pop());
}

$("connect").addEventListener("click", connect);
$("detect").addEventListener("click", () => (detecting ? stopDetection() : startDetection()));
if ($("api").value) connect();

// Mockup: dismissable pop-up (not wired to detections yet)
$("toast-close").addEventListener("click", () => { $("toast").hidden = true; });

// optional: ?autostart=1 starts detection once connected and the video plays (tests)
if (params.get("autostart")) {
  const tryStart = setInterval(() => {
    if (connected && hasSource() && video.readyState >= 2 && !detecting && !ws) {
      clearInterval(tryStart);
      startDetection();
    }
  }, 300);
}
