# Exit Obstruction Monitor

Detects **exits** (doors, glass doors, doorways) in video from a handheld camera and
decides, in near real time, whether each one is **clear**, **partially obstructed** or
**obstructed**, and by what (boxes, chairs, trolleys, …).

Detection runs on a GPU server (2× RTX 4090) with three models working at different
speeds. The client — your laptop, or the server itself — plays the video, tracks
objects every frame and shows the result live.

| Model | Role | Runs |
|---|---|---|
| **YOLOE-11s** (Ultralytics, open-vocabulary) | Fast boxes + tracking | every frame (client GPU) |
| **LocateAnything-3B** (NVIDIA) | Accurate boxes, finds what YOLOE misses | ~1 per 1.5–2 s (server GPU 1) |
| **Qwen3-VL-4B-Instruct** (vLLM) | Scene understanding: is it an exit? on the floor? blocked? | only on events (server GPU 0) |

![exit_monitor.py on a recorded walkthrough](docs/demo.gif)

*`exit_monitor.py` on `IMG_0450.MOV`: original (left), annotated (right), exit panel and
event log (bottom). The exit is confirmed by Qwen and turns **BLOCKED** (chairs, cardboard
boxes, bin); items on desks are dropped by the floor check.*

> The recorded test videos (`IMG_04xx.MOV`) stand in for the live camera. Any script
> also accepts a webcam index (`0`) or an RTSP URL, so switching to the real camera
> needs no code changes.

---

## Contents

1. [Architecture](#architecture)
2. [Why both YOLOE and LocateAnything?](#why-both-yoloe-and-locateanything)
3. [Repository layout](#repository-layout)
4. [Setup](#setup) — client, GPU server, `.env`
5. [Running](#running) — the monitors, backend + web app, other runners
6. [Backend API reference](#backend-api-reference) — for building your own app on the backend
7. [How decisions are made](#how-decisions-are-made)
8. [Measured performance](#measured-performance)
9. [Troubleshooting](#troubleshooting)
10. [Limitations, reproducibility, licences](#limitations)

---

## Architecture

`exit_monitor.py` combines fast detection, object tracking and background model
checks to show whether each exit is clear or obstructed.

![Horizontal architecture diagram of the exit obstruction monitor](docs/architecture.svg)

**Dashed arrows** show background model work. YOLOE and tracking run on the client;
LocateAnything and Qwen run on the GPU server without blocking the live view.
Qwen receives video images; tracking selects the crops and triggers scene checks.
Exit memory stabilizes each status before it is displayed and logged.

Simpler variants are also included (see [Other runners](#other-runners)).

---

## Why both YOLOE and LocateAnything?

They are complementary: **YOLOE is fast, LocateAnything is accurate.** Neither alone gives
a live view that is also correct.

| | YOLOE-11s | LocateAnything-3B |
|---|---|---|
| Time per frame | 45–75 ms on the Mac (expected ~10–20 ms on a 4090) | 1.5–1.9 s model time, 2–2.5 s round trip |
| Frames per second | ~13–20 | ~0.5 (one frame at a time) |
| Accuracy | weaker — misses doors, sometimes boxes pillars / fridges as doors | strong — finds chairs, stools, boxes and glass doors; labels are richer |
| Categories | fixed text prompts | prompts plus names supplied by Qwen (open-world) |

**Why the per-frame layer is YOLOE.**
1. *Tracking needs frequent detections.* A tracker links an object between frames by
   small movements. With detections ~2 s apart, a handheld camera moves too far in
   between: links break, objects lose their IDs and with them their stored Qwen
   verdicts (this broke an earlier position-based cache).
2. *New objects appear sooner.* YOLOE boxes something that enters the frame within a
   frame or two; LocateAnything alone takes up to ~2 s.
3. *Boxes stay accurate when the camera jerks.* Between LocateAnything results boxes
   move only by estimated camera motion, which drifts on fast pans and blur. YOLOE
   corrects them every frame.
4. *GPU headroom.* LocateAnything alone would keep a whole GPU busy; YOLOE uses a small
   fraction of one, leaving room for Qwen.

**Why LocateAnything is still needed.** YOLOE alone misses doors and objects, and its
false doors would raise false alarms. About once every 1.5–2 s LocateAnything's boxes
are mapped to the current frame and merged into the tracks: they fix labels, add
what YOLOE missed, and the tracker then follows them frame by frame. Names that Qwen
finds at the door are added to LocateAnything's categories, so objects outside the
fixed list can be found too.

**So:** YOLOE keeps the view *continuous*, LocateAnything keeps it *correct*, and Qwen
decides what it *means* (is it an exit, is it on the floor, is it blocked).

**A simpler option.** `--no-yoloe` runs LocateAnything + Qwen only
(`python exit_monitor.py <video> --no-yoloe`). Results are more accurate per update
but ~1–2 s old, and IDs are less stable on fast camera moves. It is a good starting
point when a 2 s update interval is acceptable (a walkthrough inspection, not alarms)
or when you do not want to install YOLOE. Compare the two on the same video.

---

## Repository layout

```
exit_monitor.py      MAIN: YOLOE + LocateAnything + Qwen, exit memory, panel, report
extinguisher_monitor.py  fire extinguishers: YOLOE + Qwen reads the expiry / service date (CLI)
locate_live.py       LocateAnything (+ optional Qwen assist) live, position rule
live_infer.py        Qwen-only live (prompt → boxes + status); --delay buffered mode
video_infer.py       Qwen-only offline: process a recorded video, side-by-side output
yoloe_infer.py       YOLOE-only live (local, no server)
bench_latency.py     Benchmark a VLM endpoint (TTFT, latency, throughput)
locate_test.py       Run LocateAnything on sampled frames, save an annotated grid

core/                shared library (no entry points)
  config.py          .env loading, VLM provider presets
  prompts.py         every prompt in one place
  parsing.py         VLM answer → detections (compact E/O lines or JSON) + sanity checks
  rules.py           geometric obstruction rule (clearance zone, occlusion, double doors)
  expiry.py          extinguisher dates: parse Qwen's DATE lines, due date, status
  depth_check.py     close-up "floor or furniture?" check with a position cache
  qwen_detect.py     one full-frame Qwen detection call
  qwen_assist.py     Qwen naming objects for LocateAnything + depth check
  labels.py          LocateAnything categories
  video_io.py        LiveSource (file paced as live / webcam / RTSP), camera motion
  drawing.py         boxes, banner, captions
  stream_view.py     MJPEG browser view (--stream)
engines/
  qwen_engine.py     OpenAI-compatible VLM client (vLLM, OpenRouter, DashScope, OpenAI)
  locate_engine.py   LocateAnything server client + answer parser
server/              GPU-server scripts: start_all.sh / status.sh / stop_all.sh, setup, serve
tests/test_core.py   offline tests for parsing / rules / LocateAnything output
backend/             FastAPI service: live mode (frames in, boxes out), sessions, token auth
frontend/            web app (index.html, style.css, app.js) — talks to backend/
```

Outputs go to `results/` (git-ignored); model weights to `models/` (git-ignored).

---

## Setup

### 1. Client (any laptop / desktop)

Needed only for the command-line runners; the web app needs nothing but Python 3 to
host it (`bash frontend/serve.sh`). Tested with Python 3.14 on macOS (Apple Silicon;
YOLOE runs on the MPS GPU).

```bash
cd htx_demo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.sample .env          # then fill in the server addresses
.venv/bin/python tests/test_core.py    # offline sanity check: should print 13/13 passed
```

The first YOLOE run downloads `yoloe-11s-seg.pt` and the MobileCLIP text encoder
(~630 MB) into `models/`.

### 2. GPU server (Linux, NVIDIA driver)

Clone the repository on the server (and `git pull` there to pick up later changes):

```bash
git clone <repo-url> ~/qwe
cd ~/qwe
# later, after pushing changes from your computer:
git pull
```

The repo contains code only. Test videos, model weights and `.env` are git-ignored, so
copy a video over once (`scp IMG_0451.MOV <user>@<server>:~/qwe/`) — the weights
download automatically on first use.

**Quick start — one command** (recommended):

```bash
cd ~/qwe
bash server/start_all.sh          # QWEN_MODEL=8b-fp8 bash server/start_all.sh for another model
```

It does everything a new user would otherwise do by hand:
- creates the three Python environments on first run (~20–30 min: PyTorch + model
  weights) and installs anything missing later — broken envs are moved aside and rebuilt;
- starts Qwen, LocateAnything and the backend, each in a tmux session (`qwen`, `locate`,
  `backend`) — or as background processes if tmux is not installed — logging to `logs/`;
- creates a backend token in `.env` if there is none;
- waits until all three answer and prints the **backend port and token**. The backend
  only hosts the API; enter `http://<server-ip>:<port>` and the token in the web app,
  which is hosted separately (see *Backend + web app*).

Services that are already running are left alone, so it is safe to run again.

```bash
bash server/status.sh             # what is up, ports, GPU memory
bash server/stop_all.sh           # stop everything   (or: stop_all.sh backend)
tmux attach -t backend            # watch a service (Ctrl+B then D to leave)
```

The sections below are the same steps done manually.

Each model gets its **own Python environment** (their pinned packages conflict).
Start servers inside **tmux** so they survive logging out
(`Ctrl+B` then `D` to detach, `tmux attach -t <name>` to look again).

**Qwen (vLLM, OpenAI-compatible API)**

```bash
cd ~/qwe
bash server/setup_vllm.sh                 # once: ~/qwen-vllm/.venv with vllm==0.31.0
tmux new -s qwen
bash server/serve_qwen.sh 4b              # Qwen/Qwen3-VL-4B-Instruct on port 8000+ (first free)
```

`serve_qwen.sh` also accepts `2b`, `8b-fp8`, `8b`. It picks the first free port from
8000 upward, lowers `gpu_memory_utilization` to what is free on a shared GPU, and
disables the FlashInfer sampler (see [Troubleshooting](#troubleshooting)).
Choose the GPU with `CUDA_VISIBLE_DEVICES=1 bash server/serve_qwen.sh 4b`.

**LocateAnything-3B (transformers, small FastAPI server)**

```bash
bash server/setup_locate.sh               # once: ~/locate-anything/.venv (Python 3.11) + weights
tmux new -s locate
bash server/serve_locate.sh               # GPU 1 by default, port 8020+ (first free)
```

Endpoints: `GET /health`, `POST /detect {image, categories, mode}`,
`POST /ground {image, phrase}`. Boxes come back on a 0–1000 scale.

**Backend for the web app** — see [Backend + web app](#backend--web-app-backend-frontend):
`BACKEND_TOKEN=<secret> bash server/run_backend.sh` (in tmux, after `setup_monitor.sh`).

**Optional — run the monitor on the server too**

```bash
bash server/setup_monitor.sh              # once: ~/exit-monitor/.venv (torch, ultralytics, …)
bash server/run_monitor.sh IMG_0451.MOV   # finds both servers, loops the video,
                                          # browser view on http://<server>:8090
```

### 3. `.env`

All scripts load `.env` from the project root. The important entries:

```bash
LOCATE_URL=http://<server-ip>:8020        # LocateAnything
QWEN_URL=http://<server-ip>:8010/v1       # Qwen vLLM (port printed by serve_qwen.sh)
LOCAL_VLM_URL=http://<server-ip>:8010/v1  # same server, for --provider local
LOCAL_VLM_MODEL=Qwen/Qwen3-VL-4B-Instruct
OPENROUTER_API_KEY=...                    # only for the hosted-API option
```

Check that both servers answer before running:

```bash
curl http://<server-ip>:8010/v1/models
curl http://<server-ip>:8020/health
```

If the ports are not reachable from the client, tunnel them:
`ssh -L 8010:localhost:8010 -L 8020:localhost:8020 <user>@<server>` and use `localhost`.

---

## Running

### Main: `exit_monitor.py`

```bash
.venv/bin/python exit_monitor.py IMG_0451.MOV            # URLs from .env
.venv/bin/python exit_monitor.py IMG_0451.MOV --loop --save results/run.mp4
.venv/bin/python exit_monitor.py IMG_0451.MOV --no-yoloe # LocateAnything + Qwen only
.venv/bin/python exit_monitor.py IMG_0451.MOV --no-display --stream   # browser :8090
```

Window: original | annotated video, and a panel listing each exit (status, for how
long, blockers) plus the latest events. Keys: `q`/`Esc` quit, `space` pause.

| Option | Default | Meaning |
|---|---|---|
| `--locate-url`, `--qwen-url` | from `.env` | model servers (either can be omitted) |
| `--no-yoloe` | off | no per-frame detector; tracks come from LocateAnything only |
| `--la-interval` | 1.0 s | minimum gap between LocateAnything calls |
| `--name-every` | 3.0 s | how often Qwen names objects at the door |
| `--hold` | 1.5 s | a new status must hold this long before it is shown |
| `--recheck` | 8.0 s | re-ask Qwen for a verdict at least this often per exit |
| `--verdict-ttl` | 6.0 s | how long a Qwen verdict counts |
| `--confirm-timeout` | 6.0 s | show a door's status without Qwen's exit confirmation after this |
| `--proc-width` | 960 | processing width in px |
| `--quiet` | off | no per-frame log line |
| `--stream [PORT]`, `--save FILE`, `--loop`, `--no-display`, `--out-dir` | | output options |

**Terminal log** – one line per processed frame, plus events:

```
f   98 t=  3.27s   76ms | YOLOE  8 det  54ms | LA: result back: 21 boxes (2.2s); sent frame; merged 21 boxes | Qwen: -> depth#24 -> verdict#9 | tracks: 2 door, 16 obstacle
f  137 t=  4.57s   73ms | YOLOE  7 det  69ms | LA: - | Qwen: <- confirm#9=yes | tracks: 3 door, 12 obstacle
[   4.8s] EXIT #9: CHECKING -> BLOCKED (chair, stool, cardboard box x4)
```

`->` = a Qwen job started (`confirm` = is it an exit, `depth` = floor or raised,
`verdict` = C/P/B), `<-` = its answer. LocateAnything runs back to back: it sends the
newest frame as soon as the previous answer is back (and `--la-interval` has passed).

**Output** – `results/exit_monitor_<video>/report.json` (status history, blockers and a
snapshot per exit, all events) and `exit<id>_<time>_<status>.jpg` snapshots.

### Backend + web app (`backend/`, `frontend/`)

The detection pipeline runs as a **FastAPI service on the GPU server** (API only). The
web app is hosted **separately**, on whatever machine you like, and connects to the
backend with its address and token. The detection code is the same as `exit_monitor.py`.

**Live mode (default).** The video plays **in the browser**, from a local file or the
camera. The web app sends small JPEG frames (960 px wide) to the server over one
WebSocket; the server sends back **only JSON**: exit and obstruction boxes, statuses and
events. The browser draws the boxes over its own video, so playback never waits for the
network. It keeps one frame in flight at a time (at most 15 per second), so frames never
queue up. On the same network, expect a round trip of about 50–70 ms, or about 13–15
results per second.

```
Browser:     video ─► JPEG frames ──WS /api/live──►  GPU server: backend (FastAPI, :8100)
             boxes ◄── JSON (boxes, statuses) ◄──     └─ YOLOE + LocateAnything + Qwen
```

**On the server:** run `bash server/start_all.sh`. It starts Qwen, LocateAnything and
the backend, and prints the backend port and token. To start only the backend, run
`bash server/run_backend.sh`.

**Host the web app** on any machine with Python 3 (it is static files only):

```bash
bash frontend/serve.sh                  # http://localhost:5173
# no bash (e.g. Windows): python -m http.server 5173 --bind localhost -d frontend
```

Open `http://localhost:5173`, enter the **Backend URL** (`http://<server-ip>:<port>`) and
the **Token**, then click **Connect**. Pick a **mode**, then **Open video** (or
**Camera**), then **Start detection**:

| Mode | What it does | Models | Frames sent |
|---|---|---|---|
| **Exit obstruction** | exits and what blocks them (`exit_monitor.py`) | YOLOE + LocateAnything + Qwen | 960 px, JPEG 0.75 |
| **Fire extinguisher** | extinguishers and their expiry or service date (`extinguisher_monitor.py`) | YOLOE + Qwen | 1920 px, JPEG 0.9 |

The server tells the browser which frame size to send. Extinguisher mode needs full
resolution to read the sticker, so it uses about 4× the bandwidth (fine on a LAN). If
you switch mode while detecting, the app reconnects in the new mode.

**Right side in Fire extinguisher mode (live):**

- **A card per extinguisher seen:**
  - last service, next service and expiry ("not on tag" when missing);
  - valid until, and why;
  - a **SAFE TO USE / SAFE · SERVICE DUE SOON / NOT SAFE · OVERDUE / CHECK MANUALLY /
    TAG NOT READ** badge;
  - how many reads agree. A "?" means it is not confirmed yet.
- **The checklist ticks itself:** extinguishers present, service tag readable, service
  date not overdue (red if one is overdue). "Mounted and unobstructed" is still a mockup.
- **The pop-up shows each status change.**

In exit mode, the right side is still a mockup.

- **Status line:** which models are in use, results per second, round-trip time
  (and the server's share of it), exit or extinguisher statuses, and the last event.
- **Down model servers:** a server that is down is skipped and reported, rather than
  failing the session.
- **New video:** opening another video resets the exit memory on the server.
- **Left side:** the original video, and the video with bounding boxes.
- **Right side:** the checklist, pop-up and chat are a *mockup only* for now. Live events
  already arrive (`onEvent` in `frontend/app.js`), ready to wire up.

Notes:

- **Playing `.MOV` files:** iPhone `.MOV` files (HEVC) play in Safari and Chrome on
  macOS; other browsers / systems may not. If a browser can't play a file, convert it first:
  `ffmpeg -i in.MOV -c:v libx264 -pix_fmt yuv420p out.mp4`.
- **Camera:** the browser allows camera access only on `https://` or `localhost`. Use
  the web app on `localhost` (`frontend/serve.sh` does by default), or serve it over https.
- **Unreachable port:** if the server port can't be reached from your computer, tunnel it with
  `ssh -L 8100:localhost:8100 <user>@<server>` and use `http://localhost:8100`.

**Server-video sessions (older mode).** In this mode the server reads a video from its
own disk and streams MJPEG frames back (`/api/sessions…` below). It is useful for
recording and reports, but it lags over a slow link.

**Building your own app** (Android, another web app, a script): the full API, with
request and response examples, is in [Backend API reference](#backend-api-reference).

| Setting (env / `.env` on the server) | Default | |
|---|---|---|
| `BACKEND_TOKEN` | *(none)* | shared secret: `Authorization: Bearer <token>` or `?token=` — **set it** |
| `BACKEND_CORS_ORIGINS` | `*` | origins allowed to call the API, e.g. `http://localhost:5173` |
| `BACKEND_VIDEO_DIR` | repo root | where session videos are read from |
| `BACKEND_MAX_SESSIONS` | `1` | concurrent sessions, live ones included (one GPU pipeline) |
| `LOCATE_URL`, `QWEN_URL` | from the port files | model servers (localhost on the server) |

`exit_monitor.py --stream` still works for quick checks: an all-in-one browser view
(`/stream`), plus `/stream/original`, `/stream/annotated` and `/state`.

### Fire extinguishers: `extinguisher_monitor.py` (CLI, first version)

This tool finds fire extinguishers and reads the expiry or service date on their label
or inspection tag. It needs only YOLOE and the Qwen server; LocateAnything is not used.

```bash
.venv/bin/python extinguisher_monitor.py IMG_0454.MOV            # video (QWEN_URL from .env)
.venv/bin/python extinguisher_monitor.py tag.jpg                 # a photo, or a folder of photos
.venv/bin/python extinguisher_monitor.py IMG_0454.MOV --no-qwen  # detection only
```

How it works:

1. **Detect and track.** YOLOE finds each extinguisher and the tracker gives it an ID
   that stays stable, even while you walk up to it.
2. **Keep the best view.** The tool keeps the best crop of each extinguisher, taken
   from the full-resolution frame and scored on size × sharpness. Extinguishers
   narrower than `--min-px` (default 120 px) are not read; the label says "move closer".
3. **Read in two steps,** in the background:
   - **Locate:** Qwen finds the dated sticker or tag on the crop.
   - **Read:** a tight crop of the sticker, upscaled to about 1000 px, is read. Qwen
     only copies each date and the words printed next to it
     (`DATE JUNE-2027 | NEXT RECOMMENDED SERVICING`).

   The code then decides what kind of date it is from those words: next service,
   last service, expiry, manufacture date or unknown. A 4B model mixes up "next" and
   "last" when asked to decide, and misreads digits when the sticker is only about
   100 px wide.
4. **Vote.** While the extinguisher is close, it keeps reading new views, 2 at a time
   (`--parallel`). It stops once two reads give the same due date; until then the
   status is shown with a "?" and marked "unconfirmed". The usual misread digits
   (2026 → 2024) get outvoted.

In photo mode, if no extinguisher is detected, the whole photo is read as a close-up of
the tag.

| Status | When |
|---|---|
| **VALID** | due date later than `--soon-days` (30) from today |
| **DUE SOON** | due within `--soon-days` |
| **EXPIRED** | due date passed |
| **CHECK DATE** | dates read, but none says what it means |
| **UNREADABLE** | no date readable (`too_small`, `blurry`, `no_tag`) |

How the due date is worked out:

- An expiry or next-service date is used as written.
- A last-service date alone gets `--service-months` (12) added.
- A manufacture date gets `--life-years` (10) added, and the earliest limit wins.
- A "last service" date in the future must be the next-service date (the words were
  attached to the wrong date), so it is treated as one.
- A date with only a month counts until the end of that month.
- Dates are read day-first unless you pass `--monthfirst`.
- `--today YYYY-MM-DD` sets "today", for testing.

Each run writes `results/extinguisher_<video>/report.json`. It holds every read (Qwen's
raw answer, the dates and the crop file) and the final status of each extinguisher. The
run also saves each extinguisher crop, the sticker crops Qwen read (`*_tag1.jpg`) and a
scene snapshot.

Results with Qwen3-VL-4B on the walk-by videos, at about 1.5 s per read:

| Video | Result |
|---|---|
| `IMG_0454`, `IMG_0456` | **VALID**, due 06/2027 (next service), confirmed |
| `IMG_0453` | **UNREADABLE**: the sticker never faces the camera |

A date is read only when the sticker is close (the extinguisher fills about a third or
more of the frame height). A second or two of steady view is enough.

### Other runners

| Script | What it does | Example |
|---|---|---|
| `locate_live.py` | LocateAnything every ~1.5–2 s + position rule; `--qwen-url` adds Qwen naming + depth check | `python locate_live.py IMG_0451.MOV --url http://<srv>:8020 --qwen-url http://<srv>:8010/v1` |
| `live_infer.py` | Qwen detects exits + obstructions from the prompt; results shown live, boxes motion-compensated | `python live_infer.py IMG_0451.MOV --provider local --interval 0.2 --workers 4` |
| `live_infer.py --delay N` | buffered: plays N s behind live so every frame has a fresh result | `python live_infer.py IMG_0451.MOV --provider local --delay 4` |
| `video_infer.py` | offline: parallel calls over a recorded video → side-by-side mp4 + JSON | `python video_infer.py IMG_0450.MOV --provider local --play` |
| `yoloe_infer.py` | YOLOE + position rule only, no server | `python yoloe_infer.py IMG_0450.MOV` |

`--provider` for the Qwen runners: `local` (your vLLM server, from `LOCAL_VLM_URL`),
`openrouter` (default, `qwen/qwen3-vl-8b-instruct`), `qwen` (DashScope), `openai`.
All live runners accept `--stream`, `--save`, `--loop`, `--no-display`.

### Tools

```bash
python bench_latency.py IMG_0450.MOV --provider local --sizes 448 640 --concurrency 1 4 8
python locate_test.py IMG_0450.MOV --url http://<srv>:8020 --mode fast
python tests/test_core.py
bash server/run_realtime_test.sh IMG_0450.MOV   # on the server: benchmark + live run + verdict
bash server/watch_live.sh IMG_0450.MOV          # on the server: live_infer → browser
```

---

## Backend API reference

This section is for anyone building their own client on the backend: a web app, an
Android or iOS app, a script. The web app in `frontend/` is one such client; its
[app.js](frontend/app.js) is a working example of everything below.

### Overview

The backend offers two ways to work:

| | **Live mode** (recommended) | **Server-video sessions** |
|---|---|---|
| Video comes from | **your app**: camera or file, sent frame by frame | a file or `rtsp://` stream **on the server** |
| You get back | JSON only: boxes, statuses, events | MJPEG video streams + JSON state/events |
| Modes | `exits` and `extinguisher` | `exits` only |
| Endpoint | `WS /api/live` | `/api/sessions…` |
| Latency | ~50–80 ms per frame on a LAN | depends on the link; video lags over slow networks |

A typical live-mode app does this:

```
GET  /api/health                       is the backend there? does it need a token?
GET  /api/info                         which model servers are up, which modes exist
WS   /api/live?token=…                 open one WebSocket
  → {"type":"start","mode":"extinguisher"}
  ← {"type":"ready", "send_width":1920, "jpeg_quality":0.9, …}
  loop while the camera runs:
    → binary frame (12-byte header + JPEG)
    ← {"type":"result", "extinguishers":[…], "events":[…], …}   draw it, then send the next frame
  → {"type":"stop"}  (or just close the socket)
```

### Basics

**Base URL:** `http://<server-ip>:<port>`, or `ws://` for WebSockets. The port is
the one `server/start_all.sh` prints (usually `8100`). The interactive OpenAPI docs
for the REST endpoints are at `/docs`.

**Auth:** if the server has `BACKEND_TOKEN` set (the default after `start_all.sh`),
every call except `/api/health` needs the token:

- REST: header `Authorization: Bearer <token>` (or `?token=<token>`).
- WebSocket: `?token=<token>` in the URL. Browsers can't set headers on WebSockets,
  so only the query form works.
- A wrong or missing token gets **401** `{"detail": "missing or wrong token"}`
  (REST). On a WebSocket, the handshake is refused: most clients report **HTTP 403**.

**Errors:** REST errors are JSON `{"detail": "<message>"}` with the usual status
codes (400, 401, 404, 409, 500, 501).

**CORS:** all origins are allowed by default (`BACKEND_CORS_ORIGINS`), so browser apps
on any host can call the API.

**Coordinates:** every `box` is `[x1, y1, x2, y2]` as **fractions of the frame (0–1)**,
origin top-left. To draw one, multiply by the width and height of the image as it is
shown on screen. If you letterbox the video, add the offset.

**One pipeline:** the GPU server runs `BACKEND_MAX_SESSIONS` sessions at a time
(default **1**), and live connections count. A second client is refused until the
first one stops.

### REST endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | no | liveness; whether a token is needed |
| GET | `/api/info` | yes | model servers, live modes, videos, running sessions |
| GET | `/api/videos` | yes | videos a server-video session can play |
| POST | `/api/sessions` | yes | start a server-video session |
| GET | `/api/sessions` | yes | all server-video sessions |
| GET | `/api/sessions/{id}` | yes | one session: status + latest state |
| DELETE | `/api/sessions/{id}` | yes | stop a session (its report is written) |
| GET | `/api/sessions/{id}/report` | yes | the session's report, after it ended |
| GET | `/api/sessions/{id}/stream/{name}` | yes | MJPEG video: `original`, `annotated`, `combined` |
| WS | `/api/sessions/{id}/events` | yes | pushed events + state for a session |
| POST | `/api/sessions/{id}/command` | yes | chat / commands: **not implemented yet (501)** |
| WS | `/api/live` | yes | **live mode** (next section) |

#### `GET /api/health`

```json
{"ok": true, "auth_required": true}
```

#### `GET /api/info`

```json
{
  "models": {
    "locate_anything": {"url_set": true, "up": true},
    "qwen":            {"url_set": true, "up": true}
  },
  "modes": [
    {"id": "exits",        "title": "Exit obstruction"},
    {"id": "extinguisher", "title": "Fire extinguisher"}
  ],
  "videos": ["IMG_0450.MOV", "IMG_0451.MOV"],
  "max_sessions": 1,
  "sessions": [],
  "live_sessions": [{"id": "8eaf56cb", "mode": "exits", "frames": 240}]
}
```

- `models.*.up`: whether that model server answers right now. Live mode skips a model
  that is down rather than failing.
  - Exit mode needs YOLOE, which always runs in the backend; LocateAnything and Qwen
    make it better.
  - Extinguisher mode needs **Qwen** to read dates; without it, extinguishers are only
    detected.
- `modes`: use `id` in the live `start` message and `title` in your UI.
- `sessions`: server-video sessions, in the [session object](#session-object) format.
  `live_sessions`: live connections.

#### `GET /api/videos`

```json
{"videos": ["IMG_0450.MOV", "IMG_0451.MOV", "IMG_0452.MOV"]}
```

These are files in the server's `BACKEND_VIDEO_DIR` (`.mov .mp4 .avi .mkv .m4v`).

#### `POST /api/sessions`: start a server-video session (exit mode)

Request body (JSON):

| Field | Type | Default | |
|---|---|---|---|
| `source` | string | *required* | a name from `/api/videos`, or an `rtsp://` / `rtmp://` / `http(s)://` stream URL |
| `loop` | bool | `true` | restart the file at the end |
| `use_yoloe` | bool | `true` | per-frame detector |
| `use_locate` | bool | `true` | LocateAnything |
| `use_qwen` | bool | `true` | Qwen checks |
| `options` | object | `{}` | `hold`, `la_interval`, `name_every`, `recheck`, `confirm_timeout` (seconds; see `exit_monitor.py --help`) |

```json
{"source": "IMG_0452.MOV", "loop": false}
```

The response is **201** with the [session object](#session-object). Errors:

- **400:** `{"detail": "video not found: …"}`, or a path outside the video folder.
- **409:** `{"detail": "already running: <id> (max 1) — stop it first"}`.

#### Session object

Returned by `GET /api/sessions/{id}`, `POST`, `DELETE` and in lists:

```json
{
  "id": "f986678d",
  "source": "IMG_0452.MOV",
  "status": "running",
  "error": null,
  "created": 1792210042.55,
  "loop": false,
  "models": {"yoloe": true, "locate_anything": true, "qwen": true},
  "events": 1,
  "state": {
    "video_time_s": 9.93,
    "fps": 16.2,
    "exits": [
      {"id": 73, "exit_sign": true, "status": "partially_obstructed", "status_text": "PARTIAL",
       "since_s": 0.0, "blockers": ["office chair"], "visible": true}
    ],
    "events": [
      {"video_time_s": 9.93, "text": "EXIT #73: CHECKING -> PARTIAL (office chair)",
       "status": "partially_obstructed"}
    ]
  },
  "report_ready": false
}
```

- `status`: `starting` → `running` → `finished` | `stopped` | `error` (then `error`
  has the reason).
- `state`: the latest snapshot (empty `{}` until the first frame). `state.events` is
  the last 20.
- Exit `status` values are listed in [exit statuses](#exit-mode-result).
- `DELETE` asks the session to stop and returns at once. `status` turns `stopped` a
  moment later, and then `report_ready` is `true`.

#### `GET /api/sessions/{id}/report`

You get **409** until the session has ended. Afterwards:

```json
{
  "source": "IMG_0452.MOV", "frames": 243, "dropped": 168, "fast_loop_ms": 77.6,
  "exits": [
    {"id": 58, "final_status": "clear", "blockers": [], "first_seen_s": 5.73, "last_seen_s": 8.07,
     "history": [{"video_time_s": 8.0, "status": "clear", "blockers": []}],
     "snapshot": "exit58_0008.0s_clear.jpg"}
  ],
  "events": [{"video_time_s": 8.0, "event": "door #58: CHECKING -> CLEAR"}]
}
```

#### `GET /api/sessions/{id}/stream/{original|annotated|combined}`

This is an MJPEG stream (`multipart/x-mixed-replace`). In a browser use
`<img src=".../stream/annotated?token=…">`; on Android, use any MJPEG viewer. The
stream ends when the session ends.

#### `WS /api/sessions/{id}/events?token=…`

The server pushes these messages; the client sends nothing:

```json
{"type": "hello", "session": { …session object… }}
{"type": "event", "event": {"video_time_s": 9.93, "exit_id": 73, "exit_sign": true,
                            "from": "unknown", "to": "partially_obstructed", "status_text": "PARTIAL",
                            "blockers": ["office chair"], "text": "EXIT #73: CHECKING -> PARTIAL (office chair)",
                            "seq": 0}}
{"type": "state", "status": "running", "state": { …as in the session object… }}
{"type": "end", "session": { …session object… }}
```

- `event` is sent on every exit status change.
- `state` is sent every 0.5 s.
- `end` is sent once, then the socket closes.
- Close code `4401` means a wrong token; `4404` means an unknown session.

### Live mode: `WS /api/live`

#### Connect and start

```
ws://<server-ip>:<port>/api/live?token=<token>
```

The **first message** must be a text (JSON) `start`:

```json
{"type": "start", "mode": "extinguisher"}
```

| Field | Type | Default | |
|---|---|---|---|
| `type` | `"start"` | *required* | |
| `mode` | `"exits"` \| `"extinguisher"` | `"exits"` | what to detect (ids from `/api/info`) |
| `use_yoloe` | bool | `true` | exits mode only |
| `use_locate` | bool | `true` | exits mode only: LocateAnything |
| `use_qwen` | bool | `true` | both modes: Qwen (extinguisher dates need it) |
| `options` | object | `{}` | per mode, see below |

`options` per mode:

- **`exits`** (floats, seconds): `hold` (1.5), `la_interval` (1.0), `name_every` (3),
  `recheck` (8), `confirm_timeout` (6).
- **`extinguisher`:**
  - `min_px` (120): extinguishers narrower than this in your frame aren't read.
  - `max_reads` (5) and `parallel` (2): how many reads, and how many at once.
  - `soon_days` (30): how near the due date counts as "due soon".
  - `service_months` (12): how long a service lasts when only the last-service date
    is known.
  - `life_years` (10): service life from the manufacture date.
  - `monthfirst` (false): read `05/06/2027` as month/day.

The server replies **`ready`** (the first start takes a few seconds while models load):

```json
{
  "type": "ready",
  "id": "791214b4",
  "mode": "extinguisher",
  "models": {"yoloe": true, "locate_anything": false, "qwen": true},
  "skipped": [],
  "send_width": 1920,
  "jpeg_quality": 0.9
}
```

- `models`: what this session actually uses. `skipped` lists model servers that were
  down and are therefore not used (e.g. `["Qwen"]`). Tell the user.
- **`send_width` / `jpeg_quality`: send frames this wide (scale down, keep the aspect
  ratio, never scale up) at this JPEG quality.**
  - `exits`: 960 px at 0.75, about 50 KB per frame.
  - `extinguisher`: 1920 px at 0.9, about 300 KB per frame. Full resolution is needed
    to read the date on a sticker.

If the start fails, you get `{"type": "error", "detail": "…"}` and the server closes
the socket. Causes:

- an unknown mode (`"ValueError: unknown mode 'x' — one of exits, extinguisher"`);
- another session already running (`"SessionError: already running: <id> (max 1) — stop it first"`);
- `"first message must be {type: start}"`.

#### Send frames (binary messages)

Each frame is **one binary WebSocket message**: a 12-byte little-endian header, then
the JPEG file bytes.

| Offset | Size | Type | Field |
|---|---|---|---|
| 0 | 4 | `uint32` LE | `seq`: your frame counter; echoed back in the result |
| 4 | 8 | `float64` LE | `t`: video time in seconds (camera: seconds since start); echoed back |
| 12 | … | bytes | a JPEG image (any size; colour) |

```python
# Python
msg = struct.pack("<Id", seq, t) + jpeg_bytes
```

```js
// JavaScript (browser)
const msg = new Uint8Array(12 + jpeg.length);
const v = new DataView(msg.buffer);
v.setUint32(0, seq, true); v.setFloat64(4, t, true); msg.set(jpeg, 12);
ws.send(msg.buffer);
```

```kotlin
// Kotlin (Android, OkHttp)
val buf = ByteBuffer.allocate(12 + jpeg.size).order(ByteOrder.LITTLE_ENDIAN)
buf.putInt(seq).putDouble(t).put(jpeg)
webSocket.send(ByteString.of(*buf.array()))
```

**Pacing: keep exactly one frame in flight.** Send a frame, wait for its `result`,
then send the next one, capped at about 15 per second. The server processes frames in
order, one at a time. If you send faster, frames queue up and the boxes fall behind
the video. When the camera is paused, just stop sending.

If the frame size changes (e.g. the camera rotates), the server starts tracking afresh
automatically.

#### Results

Each frame gets exactly one reply:

```json
{"type": "result", "mode": "exits", "seq": 239, "t": 15.93, "proc_ms": 95.7, "yoloe_ms": 93.3,
 "locate_update": false, "events": [ … ], …mode-specific fields… }
```

| Field | |
|---|---|
| `seq`, `t` | echoed from your frame header. Match the result to the frame and measure the round trip |
| `proc_ms` | server processing time for this frame |
| `yoloe_ms` | detector time (part of `proc_ms`) |
| `locate_update` | exits mode: LocateAnything boxes were merged in this frame |
| `events` | **new** events since the previous result: `[{"video_time_s", "text", "status"}]`. Show them as notifications |

A frame that can't be decoded returns `{"type": "error", "detail": "could not decode the JPEG"}`
(or `"frame message too short"`), and **the session stays open**.

#### Exit mode result

```json
{
  "type": "result", "mode": "exits", "seq": 239, "t": 15.93, "proc_ms": 95.7, "yoloe_ms": 93.3,
  "locate_update": false, "events": [],
  "exits": [
    {"id": 125, "exit_sign": false, "confirmed": null, "status": "partially_obstructed",
     "status_text": "PARTIAL", "since_s": 0.5, "blockers": ["office chair x2"],
     "box": [0.0, 0.0, 0.4899, 1.0]}
  ],
  "obstacles": [
    {"id": 89, "label": "office chair", "depth": null, "exit_id": 125, "box": [0.0, 0.4899, 0.0581, 1.0]}
  ]
}
```

`exits[]`: the exits in view.

| Field | |
|---|---|
| `id` | track ID, stable while the exit is followed |
| `exit_sign` | an EXIT sign belongs to this door |
| `confirmed` | Qwen says it is a real exit: `true`, `false`, or `null` (not asked yet / no Qwen) |
| `status` / `status_text` | `unknown`/`CHECKING`, `clear`/`CLEAR`, `partially_obstructed`/`PARTIAL`, `obstructed`/`BLOCKED` |
| `since_s` | seconds in this status |
| `blockers` | names of what obstructs it |
| `box` | 0–1 box |

`obstacles[]`: the objects obstructing the exits in view.

| Field | |
|---|---|
| `id`, `label`, `box` | as above |
| `exit_id` | which exit it obstructs |
| `depth` | `"floor"` if Qwen checked it stands on the floor, else `null` |

The web app colours exits green (clear), amber (partial), red (blocked) and grey
(checking), and draws obstacles in orange.

#### Extinguisher mode result

```json
{
  "type": "result", "mode": "extinguisher", "seq": 176, "t": 7.5, "proc_ms": 61.2, "yoloe_ms": 57.2,
  "locate_update": false,
  "events": [{"video_time_s": 7.5, "text": "EXTINGUISHER #1: VALID — due 06/2027 (next service; 2/2 reads agree)",
              "status": "valid"}],
  "extinguishers": [
    {
      "id": 1, "status": "valid", "status_text": "VALID",
      "safe": true, "safe_text": "Safe to use",
      "confirmed": true, "reading": false, "visible": true,
      "due": "2027-06-30", "due_text": "06/2027", "basis": "next service",
      "dates": {
        "LAST_SERVICE": {"date": "2026-06-30", "text": "06/2026", "raw": "JUNE-2026", "precision": "month"},
        "NEXT_SERVICE": {"date": "2027-06-30", "text": "06/2027", "raw": "JUNE 2027", "precision": "month"}
      },
      "dates_read": ["LAST_SERVICE JUNE-2026", "NEXT_SERVICE JUNE 2027"],
      "reason": null, "reads": 4, "agree": 3, "hint": null,
      "text": "VALID — due 06/2027 (next service; 3/4 reads agree)",
      "box": [0.4135, 0.1009, 0.5606, 0.9054]
    }
  ],
  "inventory": [ { …same fields, without "box"… } ]
}
```

- **`extinguishers`**: the ones **in view**, with a `box`. Draw these on the video.
- **`inventory`**: **every** extinguisher seen in this session, including those out of
  view (`visible: false`). Use it for a list or summary screen.

| Field | |
|---|---|
| `id` | track ID |
| `status` | `reading` → `valid` \| `expiring_soon` \| `expired` \| `unclear` \| `unreadable` |
| `status_text` | `READING`, `VALID`, `DUE SOON`, `EXPIRED`, `CHECK DATE`, `UNREADABLE` |
| `safe` | **`true`** (valid, due soon), **`false`** (expired), **`null`** (unknown: reading, unclear, unreadable) |
| `safe_text` | e.g. `"Safe to use"`, `"Safe to use — service due soon"`, `"Not safe — overdue"`, `"Unknown — tag not read"` |
| `confirmed` | **two reads agree on the due date**. Until then, treat the status as provisional (the web app adds "?") |
| `reading` | a read is in progress now |
| `due` / `due_text` | the date the extinguisher stops being OK (ISO / display), or `null` |
| `basis` | why: `"next service"`, `"expiry date"`, `"last service + 12 months"`, `"manufactured + 10 years"` |
| `dates` | the dates read off the tag, by kind: `LAST_SERVICE`, `NEXT_SERVICE`, `EXPIRY`, `MANUFACTURED`, `UNKNOWN`. A kind that is missing was not on the tag. `date` is ISO; month-only dates count to the month's end. `text` is for display; `raw` is what was written |
| `dates_read` | the same dates as short text, for logs |
| `reason` | when `unreadable`: `no_tag`, `too_small`, `blurry` |
| `reads` / `agree` | reads done / reads agreeing with `due` |
| `hint` | a tip for the user: `"move closer"`, `"hold steady"`, or `null` |
| `text` | one-line summary, ready to show |

How a status develops:

1. A new extinguisher starts as `reading`, with hint `"move closer"` while it's too
   small.
2. Once close enough, it is read, about 1.5 s per read.
3. After the first read with a date, it shows a provisional status (`confirmed: false`).
4. It becomes `confirmed: true` once two reads agree.
5. Events are sent when the status, the due date or the confirmation changes.

Colours used by the web app: green `valid`, amber `expiring_soon` / `unclear`, red
`expired`, purple `unreadable`, grey `reading`.

#### Other messages

| You send (text JSON) | Server replies | |
|---|---|---|
| `{"type": "reset"}` | `{"type": "reset_done"}` | forget everything tracked (new video, new room). Send it when the user switches the source |
| `{"type": "stop"}` | the socket closes | ends the session; the server writes its report into `BACKEND_OUT_DIR` |

Just closing the socket also ends the session. **To switch mode,** close the socket
and open a new one with the other `mode`.

### Minimal client (Python, tested)

```python
# pip install websockets opencv-python
import asyncio, json, struct, cv2, websockets

URL = "ws://<server-ip>:8100/api/live?token=<token>"

async def main(video="IMG_0454.MOV", mode="extinguisher"):
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "mode": mode}))
        ready = json.loads(await ws.recv())
        if ready["type"] != "ready":
            raise SystemExit(ready)                      # {"type": "error", "detail": …}
        cap, seq = cv2.VideoCapture(video), 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            k = min(1.0, ready["send_width"] / frame.shape[1])
            frame = cv2.resize(frame, None, fx=k, fy=k)
            _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(100 * ready["jpeg_quality"])])
            await ws.send(struct.pack("<Id", seq, seq / fps) + jpg.tobytes())
            r = json.loads(await ws.recv())              # one frame in flight
            for ev in r.get("events", []):
                print(f"{ev['video_time_s']:6.1f}s  {ev['text']}")
            seq += 1
        await ws.send(json.dumps({"type": "stop"}))
        print("final:", [x["text"] for x in r.get("inventory", r.get("exits", []))])

asyncio.run(main())
```

---

## How decisions are made

**Exits.** Doors come from YOLOE / LocateAnything (`door`, `glass door`, `doorway`).
Side-by-side leaves are merged into one door; a door with an EXIT sign just above it is
labelled `EXIT`. Qwen confirms each new door is a way out (rejects TVs, cupboards,
glass partitions) before any status is shown.

**Obstructions** (`core/rules.py`). For each door, a *clearance zone* covers the lower
part of the door and the floor in front of it (a bit wider than the door). An object
obstructs the door if
- it stands on the floor inside that zone (its bottom edge is in the zone), or
- it hides the bottom edge of the door (occlusion rule — needed because detectors box
  only the visible part of a door partly hidden by boxes).

Status from how much of the door width the obstructions cover:
≥ 60 % → **obstructed**, any → **partially obstructed**, none → **clear**.
Qwen's verdict (C/P/B on a close-up of the door), when recent, is combined with the
rule — the more severe one wins.

**Depth check.** A 2D image can't tell a plant on a desk in front of the door from a
plant at the door. Each new obstruction gets a close-up of its base and the question
"floor or raised surface?" (`DEPTH_PROMPT` in `core/prompts.py`); objects on raised
surfaces are ignored for as long as they are tracked.

**Prompts.** All prompts live in `core/prompts.py`. The full-frame detection prompt
asks for a compact line format (`E P x1 y1 x2 y2`, `O 1 floor chair x1 y1 x2 y2`) —
about 3× shorter than JSON, which matters because answer length dominates latency on a
self-hosted GPU.

---

## Measured performance

Measured during development (RTX 4090 server shared with another user; Mac = Apple
Silicon client over the LAN). Treat as indicative.

| Component | Measured |
|---|---|
| Qwen3-VL-4B, full-frame detection (compact format) | 0.8–1.3 s per call |
| Qwen3-VL-4B, short answers (name objects / floor check / yes-no) | 0.2–0.7 s |
| Qwen3-VL-8B (bf16), full-frame detection | 2–3 s (~44 tokens/s) |
| Qwen3-VL-8B via OpenRouter | 2.3–4 s, occasional 10 s+ outliers |
| LocateAnything-3B, `hybrid`, 960 px | 1.5–1.9 s model time, 2–2.5 s round trip |
| YOLOE-11s on the Mac (MPS) | 45–75 ms per frame |
| `exit_monitor.py` fast loop on the Mac | ~75 ms per frame (~13 fps) |

YOLOE on the 4090 (`server/run_monitor.sh`) is expected to be several times faster but
has not been benchmarked yet.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Connection refused` on 8010 / 8020 | Server not running (often stopped when its SSH session closed) → start it in **tmux**. `run_monitor.sh` prints the exact command. |
| `Address already in use` | Port taken on the shared server → scripts move to the next free port automatically; read the printed URL. |
| `Free memory on device … less than desired GPU memory utilization` | GPU shared → `serve_qwen.sh` lowers it automatically; or `GPU_UTIL=0.5`, or use the other GPU with `CUDA_VISIBLE_DEVICES=1`. |
| `nvcc fatal: Unknown option '--compress-mode=size'` | FlashInfer JIT with an old system CUDA → `serve_qwen.sh` sets `VLLM_USE_FLASHINFER_SAMPLER=0`. |
| All results `exits=0` with Qwen-4B | The model answered in its own JSON layout → handled by `core/parsing.py`; make sure the client code is up to date. |
| `Qwen assist: OFF` | `--qwen-url` not given and `QWEN_URL` not in `.env`. |
| Web app: video doesn't play | Browser can't decode the file (often HEVC outside macOS) → `ffmpeg -i in.MOV -c:v libx264 -pix_fmt yuv420p out.mp4` |
| Web app: "Qwen down — skipped" | Live mode carries on without that model; start it (`bash server/start_all.sh`) and press Start detection again. |
| Browser view not reachable | `ssh -L 8090:localhost:8090 <user>@<server>` → `http://localhost:8090`. |
| LocateAnything setup fails at the PyTorch step | `TORCH_INDEX=https://download.pytorch.org/whl/cu121 bash server/setup_locate.sh` |

---

## Limitations

- **Depth from 2D.** The floor-vs-raised check is a VLM judgement on a crop; it can
  drop a real chair standing next to a cabinet, or keep a desk item. A depth model or
  RGB-D camera would make this geometric.
- **Fixed furniture.** Cabinets/desks near a door may be labelled `table` and counted
  as obstructions.
- **Double doors** are sometimes tracked as one leaf.
- **Handheld motion.** Fast pans can briefly mis-place boxes until the next detection.
- **Status lag.** A change is shown ~1.5 s (hold) after it is detected; LocateAnything
  results are 1.5–2.5 s old when they arrive (boxes are motion-compensated).

## Reproducibility

- Client packages are pinned in `requirements.txt`; server environments are created by
  `server/setup_*.sh` (vLLM 0.31.0, transformers 4.57.1 for LocateAnything,
  ultralytics 8.4.176).
- Models: `Qwen/Qwen3-VL-4B-Instruct` (also tested: 2B, 8B, 8B-FP8),
  `nvidia/LocateAnything-3B`, `yoloe-11s-seg.pt` — downloaded automatically.
- Qwen calls use `temperature=0`, so the same frame gives the same answer.
  LocateAnything uses the sampling settings from its model card (temperature 0.7), so
  its boxes can vary slightly between runs.
- Live runs pace the video to wall-clock time and drop frames when processing is slow,
  so frame-level results depend on hardware speed; `video_infer.py` (offline) does not
  drop frames.

## Licences

- **LocateAnything-3B** – NVIDIA licence, **non-commercial research use only**.
- **YOLOE / Ultralytics** – AGPL-3.0 (commercial use needs an Ultralytics licence).
- **Qwen3-VL** – Apache-2.0.

Fine for research; check before any product use.
