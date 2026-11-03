// Exit Monitor web app — live mode.
//
// Two modes (switch at the top): "Exit obstruction" and "Fire extinguisher" (finds
// extinguishers and reads their service / expiry date). The server says in its
// "ready" message how big the frames should be (extinguisher mode needs full
// resolution to read the sticker).
//
// The video (file or camera) plays HERE, in the browser. Frames are grabbed from the
// <video>, shrunk, JPEG-encoded and sent to the backend over one WebSocket
// (/api/live). The backend runs the detection pipeline and returns only JSON —
// exit boxes, statuses, obstructions, events — which are drawn on a canvas over
// the local video. One frame is in flight at a time, so nothing ever queues up.
//
// The web app is hosted separately from the backend (frontend/serve.sh). Enter the
// backend URL (http://<server-ip>:<port>) and token; both are remembered in this
// browser. ?api=…&token=… in the page URL also fills them in.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const store = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

let sendWidth = 960;          // px — set by the server's "ready" message per mode
let jpegQuality = 0.75;
const MAX_FPS = 15;           // upper bound on frames sent per second

const MODES = {
  exits: {
    checklist: ["All exits identified", "Exit paths clear of obstructions", "Exit signs visible",
                "Doors open fully"],
    toast: "EXIT #9 is <strong>BLOCKED</strong> — chair, cardboard box",
  },
  extinguisher: {
    // ticked live from the results (see updateExtPanel); the last one is still a mockup
    checklist: ["Extinguishers present", "Service tag readable", "Service date not overdue",
                "Mounted and unobstructed"],
    toast: "Point the camera at an extinguisher's service tag — results appear here",
  },
};

$("api").value = params.get("api") || store.get("exitmon.api");
$("token").value = params.get("token") || store.get("exitmon.token");

const modeInputs = [...document.querySelectorAll('input[name="mode"]')];
const mode = () => (modeInputs.find((i) => i.checked) || modeInputs[0]).value;
function setMode(m) {
  if (!MODES[m]) return;
  modeInputs.forEach((i) => { i.checked = i.value === m; });
  // right side is a mockup: just show the checklist / pop-up text for this mode
  $("checklist").innerHTML = MODES[m].checklist
    .map((t) => `<li><label><input type="checkbox" tabindex="-1" onclick="return false"> ${t}</label></li>`)
    .join("");                        // read-only (not disabled, so ticks show in colour)
  $("toast-text").innerHTML = MODES[m].toast;
  $("toast").style.removeProperty("--toast");
  const ext = m === "extinguisher";
  $("side-panel").classList.toggle("ext-mode", ext);
  $("ext-list").hidden = !ext;
  $("side-badge").textContent = ext ? "live" : "mockup";
  renderExtCards([]);
}
// ── fire-extinguisher side panel ────────────────────────────────────────────
const EXT_STYLE = {   // status → card colour, badge text
  valid: ["#16a34a", "SAFE TO USE"], expiring_soon: ["#f59e0b", "SAFE · SERVICE DUE SOON"],
  expired: ["#dc2626", "NOT SAFE · OVERDUE"], unclear: ["#f59e0b", "CHECK MANUALLY"],
  unreadable: ["#a855f7", "TAG NOT READ"], reading: ["#9ca3af", "READING…"],
};
const DATE_ROWS = [["LAST_SERVICE", "Last service"], ["NEXT_SERVICE", "Next service"],
                   ["EXPIRY", "Expiry"], ["MANUFACTURED", "Manufactured"], ["UNKNOWN", "Other date"]];
let lastCards = "";

const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function extCard(x) {
  const [color, badge] = EXT_STYLE[x.status] || EXT_STYLE.reading;
  const dark = ["expiring_soon", "unclear", "reading"].includes(x.status) ? " dark" : "";
  const sure = x.due && !x.confirmed ? "?" : "";
  const rows = DATE_ROWS
    .filter(([k]) => x.dates[k] || ["LAST_SERVICE", "NEXT_SERVICE", "EXPIRY"].includes(k))
    .map(([k, name]) => x.dates[k]
      ? `<dt>${name}</dt><dd>${esc(x.dates[k].text)}</dd>`
      : `<dt>${name}</dt><dd class="none">not on tag</dd>`).join("");
  const until = x.due_text ? `<dt>Valid until</dt><dd>end of ${esc(x.due_text)} <span class="none">(${esc(x.basis)})</span></dd>` : "";
  let foot;
  if (x.status === "reading") foot = x.hint ? `Waiting — ${esc(x.hint)}` : "Reading the service tag…";
  else if (x.status === "unreadable") foot = `No date read (${esc(x.reason)}) · ${x.reads} reads — move closer and hold steady`;
  else if (x.due) foot = `${x.confirmed ? "Confirmed" : "Not confirmed yet"} · ${x.agree} of ${x.reads} reads agree`;
  else foot = `${x.reads} reads — the date's meaning is not written on the tag`;
  if (x.reading) foot += " · reading…";
  if (!x.visible) foot += " · out of view";
  const body = x.reads && x.status !== "unreadable" ? `<dl>${rows}${until}</dl>` : "";
  return `<article class="ext-card${x.visible ? "" : " gone"}" style="--st:${color}">
    <header><strong>Extinguisher #${x.id}</strong><span class="safe-badge${dark}">${badge}${sure}</span></header>
    <p class="safe-line">${esc(x.safe_text)}${sure ? " (not confirmed yet)" : ""}</p>
    ${body}<footer>${foot}</footer></article>`;
}

function renderExtCards(inventory) {
  const html = inventory.length
    ? inventory.map(extCard).join("")
    : `<p class="ext-empty">No extinguishers yet — point the camera at one and walk up to its service tag.</p>`;
  if (html !== lastCards) {           // results come ~10×/s: only touch the DOM on change
    $("ext-cards").innerHTML = html;
    lastCards = html;
  }
}

function updateExtPanel(r) {
  const inv = r.inventory || [];
  renderExtCards(inv);
  const dated = inv.filter((x) => x.due);
  const items = [...$("checklist").querySelectorAll("li")];
  const set = (i, ok, fail = false) => {
    items[i].querySelector("input").checked = ok;
    items[i].classList.toggle("fail", fail);
  };
  set(0, inv.length > 0);
  set(1, dated.length > 0);
  set(2, dated.length > 0 && !dated.some((x) => x.status === "expired"),
      dated.some((x) => x.status === "expired"));
}

function showToast(ev) {
  const color = (EXT_STYLE[ev.status] || EXT_STYLE.reading)[0];
  $("toast-text").textContent = ev.text;
  $("toast").style.setProperty("--toast", color);
  $("toast").hidden = false;
}

setMode(params.get("mode") || store.get("exitmon.mode") || "exits");

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
let restartOnClose = false;   // mode switched while detecting → reconnect in the new mode

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
// the src attribute, not currentSrc: currentSrc is filled in a moment after a file is chosen
const hasSource = () => Boolean(video.getAttribute("src") || video.srcObject);

function updateButtons() {
  $("detect").disabled = !connected || !hasSource();
  $("detect").textContent = detecting ? "Stop detection" : "Start detection";
  $("detect").classList.toggle("danger", detecting);
}

// ── backend ──────────────────────────────────────────────────────────────────
async function connect() {
  if (!api()) { setStatus("Enter the backend URL, e.g. http://<server-ip>:8100", true); return; }
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
  setStatus("Starting detection… (the first start loads the models, a few seconds)");
  const m = mode();
  ws.onopen = () => ws.send(JSON.stringify({ type: "start", mode: m }));
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.type === "ready") {
      detecting = true;
      inFlight = false;
      latest = null;
      lastEvent = null;
      if (data.mode === "extinguisher") setMode("extinguisher");   // fresh panel + checklist
      sendWidth = data.send_width || 960;
      jpegQuality = data.jpeg_quality || 0.75;
      const names = { yoloe: "YOLOE", locate_anything: "LocateAnything", qwen: "Qwen" };
      modelsText = Object.entries(data.models).filter(([, on]) => on).map(([k]) => names[k]).join(" + ");
      if (data.skipped && data.skipped.length) modelsText += ` (${data.skipped.join(", ")} down — skipped)`;
      setPill("det-state", "live", "detecting");
      setStatus(`Detection running · ${modelsText}`, Boolean(data.skipped && data.skipped.length));
      if (data.mode === "extinguisher" && !data.models.qwen) {
        setStatus(`Detection running · ${modelsText} — dates cannot be read without Qwen`, true);
      }
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
    if (restartOnClose) {
      restartOnClose = false;
      startDetection();
    }
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
  const scale = Math.min(1, sendWidth / video.videoWidth);
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
  }, "image/jpeg", jpegQuality);
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
  if (r.mode === "extinguisher") {
    updateExtPanel(r);
    (r.events || []).forEach(showToast);
  }

  const rtt = rtts.reduce((a, b) => a + b, 0) / Math.max(1, rtts.length);
  const fps = resultTimes.length / 2;
  const exits = r.mode === "extinguisher"
    ? r.extinguishers.map((x) => `#${x.id} ${x.status_text}${x.due_text ? ` ${x.due_text}` : ""}`
                                 + `${x.due_text && !x.confirmed ? "?" : ""}`).join(", ")
      || "no extinguishers in view"
    : r.exits.map((e) => `#${e.id} ${e.status_text}`).join(", ") || "no exits in view";
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
const EXT_COLORS = { reading: "#9ca3af", valid: "#16a34a", expiring_soon: "#f59e0b",
                     expired: "#dc2626", unclear: "#f59e0b", unreadable: "#a855f7" };

function label(ctx, text, x, y, color, size) {
  ctx.font = `600 ${size}px -apple-system, Segoe UI, sans-serif`;
  const pad = size * 0.4;
  const w = ctx.measureText(text).width + 2 * pad;
  const h = size * 1.5;
  const ty = Math.max(h, y);
  const tx = Math.min(Math.max(0, x), overlay.width - w);
  ctx.fillStyle = color;
  ctx.fillRect(tx, ty - h, w, h);
  ctx.fillStyle = [COLORS.partially_obstructed, COLORS.unknown].includes(color) ? "#111" : "#fff";
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
  if (latest.mode === "extinguisher") {
    for (const x of latest.extinguishers) {
      const [x1, y1, x2, y2] = x.box;
      const color = EXT_COLORS[x.status] || EXT_COLORS.reading;
      octx.strokeStyle = color;
      octx.lineWidth = lw * 1.5;
      octx.strokeRect(x1 * W, y1 * H, (x2 - x1) * W, (y2 - y1) * H);
      let text = `EXT #${x.id} ${x.status_text}`;
      if (x.due_text) text += ` ${x.due_text}${x.confirmed ? "" : "?"}`;
      if (x.reading) text += " …";
      else if (x.hint && ["reading", "unreadable"].includes(x.status)) text += ` (${x.hint})`;
      label(octx, text, x1 * W, y1 * H, color, fs);
    }
    return;
  }
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
  if (mode() === "extinguisher") setMode("extinguisher");      // the server resets too
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
video.addEventListener("loadeddata", updateButtons);   // re-check once the video is really there
modeInputs.forEach((i) => i.addEventListener("change", () => {
  setMode(mode());
  store.set("exitmon.mode", mode());
  latest = null;
  if (ws) {                       // switch while detecting: reconnect in the new mode
    restartOnClose = true;
    stopDetection();
  }
}));
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
