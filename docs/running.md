# Running the other programs

The [README](../README.md#running) covers the main program, the **fire safety
inspection** (`python run.py inspect`). This page covers the others: each monitor on its
own, the backend and web app, the simpler runners, the live camera and the tools.

Every program starts from `run.py` in the project root: `python run.py` lists the
commands, and `python run.py <command> -h` shows a command's options. The code is in
`apps/` (`python -m apps.<module>` works too). The old script names also work as
commands, for example `python run.py exit_monitor IMG_0451.MOV`.

- [Exit monitor: python run.py exits](#exit-monitor-python-runpy-exits)
- [Backend + web app (backend/, frontend/)](#backend--web-app-backend-frontend)
- [Fire extinguishers: python run.py extinguishers (first version)](#fire-extinguishers-python-runpy-extinguishers-first-version)
- [Other runners](#other-runners)
- [Live camera (DJI Osmo Pocket 3)](#live-camera-dji-osmo-pocket-3)
- [Tools](#tools)

## Exit monitor: `python run.py exits`

```bash
.venv/bin/python run.py exits IMG_0451.MOV            # URLs from .env
.venv/bin/python run.py exits IMG_0451.MOV --loop --save results/run.mp4
.venv/bin/python run.py exits IMG_0451.MOV --no-yoloe # LocateAnything + Qwen only
.venv/bin/python run.py exits IMG_0451.MOV --no-display --stream   # browser :8090
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

## Backend + web app (`backend/`, `frontend/`)

The detection pipeline runs as a **FastAPI service on the GPU server** (API only). The
web app is hosted **separately**, on whatever machine you like, and connects to the
backend with its address and token. The detection code is the same as `apps/exit_monitor.py`.

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
| **Exit obstruction** | exits and what blocks them (`apps/exit_monitor.py`) | YOLOE + LocateAnything + Qwen | 960 px, JPEG 0.75 |
| **Fire extinguisher** | extinguishers and their expiry or service date (`apps/extinguisher_monitor.py`) | YOLOE + Qwen | 1920 px, JPEG 0.9 |

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
own disk and streams MJPEG frames back (`/api/sessions…` in the [API reference](android-api.md#rest-endpoints)). It is useful for
recording and reports, but it lags over a slow link.

**Building your own app** (Android, another web app, a script): the full API, with
request and response examples, is in [Backend API reference](android-api.md).

| Setting (env / `.env` on the server) | Default | |
|---|---|---|
| `BACKEND_TOKEN` | *(none)* | shared secret: `Authorization: Bearer <token>` or `?token=` — **set it** |
| `BACKEND_CORS_ORIGINS` | `*` | origins allowed to call the API, e.g. `http://localhost:5173` |
| `BACKEND_VIDEO_DIR` | repo root | where session videos are read from |
| `BACKEND_MAX_SESSIONS` | `1` | concurrent sessions, live ones included (one GPU pipeline) |
| `LOCATE_URL`, `QWEN_URL` | from the port files | model servers (localhost on the server) |

`python run.py exits --stream` still works for quick checks: an all-in-one browser view
(`/stream`), plus `/stream/original`, `/stream/annotated` and `/state`.

## Fire extinguishers: `python run.py extinguishers` (first version)

This tool finds fire extinguishers and reads the expiry or service date on their label
or inspection tag. It needs only YOLOE and the Qwen server; LocateAnything is not used.

```bash
.venv/bin/python run.py extinguishers IMG_0454.MOV            # video (QWEN_URL from .env)
.venv/bin/python run.py extinguishers tag.jpg                 # a photo, or a folder of photos
.venv/bin/python run.py extinguishers IMG_0454.MOV --no-qwen  # detection only
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

## Other runners

| Script | What it does | Example |
|---|---|---|
| `apps/locate_live.py` | LocateAnything every ~1.5–2 s + position rule; `--qwen-url` adds Qwen naming + depth check | `python run.py locate-live IMG_0451.MOV --url http://<srv>:8020 --qwen-url http://<srv>:8010/v1` |
| `apps/live_infer.py` | Qwen detects exits + obstructions from the prompt; results shown live, boxes motion-compensated | `python run.py live IMG_0451.MOV --provider local --interval 0.2 --workers 4` |
| `apps/live_infer.py` with `--delay N` | buffered: plays N s behind live so every frame has a fresh result | `python run.py live IMG_0451.MOV --provider local --delay 4` |
| `apps/video_infer.py` | offline: parallel calls over a recorded video → side-by-side mp4 + JSON | `python run.py video IMG_0450.MOV --provider local --play` |
| `apps/yoloe_infer.py` | YOLOE + position rule only, no server | `python run.py yoloe IMG_0450.MOV` |

`--provider` for the Qwen runners: `local` (your vLLM server, from `LOCAL_VLM_URL`),
`openrouter` (default, `qwen/qwen3-vl-8b-instruct`), `qwen` (DashScope), `openai`.
All live runners accept `--stream`, `--save`, `--loop`, `--no-display`.

## Live camera (DJI Osmo Pocket 3)

Every runner takes a live camera as its source: a stream URL or a webcam index. For the
Pocket 3 over Wi-Fi, the camera pushes RTMP to a MediaMTX server, and the runners read
it back as RTSP:

```bash
python run.py camera-check rtsp://<host>:8554/live/x2           # picture, fps, drops, reconnects; no models
python run.py camera-check rtsp://<host>:8554/live/x2 --clock   # film the window to measure latency
python run.py exits rtsp://<host>:8554/live/x2
python run.py exits 0                                    # USB webcam mode
```

For webcams and streams, `core/video_io.py:LiveSource` always processes the newest frame,
so delay can't build up when processing is slower than the camera. It reconnects when the
stream drops and ends the run after 30 s without frames. Times in logs and reports are
seconds since the first frame. Setup (MediaMTX, DJI Mimo, WSL2 networking, USB), latency
and troubleshooting are in **[docs/live-camera.md](live-camera.md)**.

## Tools

```bash
python run.py camera-check rtsp://<host>:8554/live/x2 --clock   # camera link check, no models
python run.py bench IMG_0450.MOV --provider local --sizes 448 640 --concurrency 1 4 8
python run.py locate-test IMG_0450.MOV --url http://<srv>:8020 --mode fast
python tests/test_core.py
bash server/run_realtime_test.sh IMG_0450.MOV   # on the server: benchmark + live run + verdict
bash server/watch_live.sh IMG_0450.MOV          # on the server: live_infer → browser
```
