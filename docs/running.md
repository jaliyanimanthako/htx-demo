# Setup and run the demo

Use this guide to run the tablet demo with the backend on either the Windows/WSL
laptop or the GPU server. The tablet offers **Pre-recorded video**, **Tablet camera**,
and **External camera**, with exit/obstruction and extinguisher detection together.

The tablet displays results; the backend computer runs detection. Qwen adds exit
checks and extinguisher date reading when available. The tablet does not run models
or start model services.

For normal use, edit the **root `config.toml` on the backend computer**. You do not
need separate configuration files for different machines to get started.

## Daily startup — if everything is already installed

Before these steps, start the model services. If using an external camera, also
start MediaMTX and the camera's livestream.

1. Set the camera URL and model settings in `config.toml`.
2. Activate the backend's Python environment and run `python run.py backend`.
3. In the tablet's **Settings**, enter the backend computer's address and token.
4. Choose **External camera**, **Tablet camera**, or **Pre-recorded video**.

Install the updated tablet app to get the External camera option. Restart the
backend whenever you change `config.toml`.

For first-time setup, follow the numbered sections below. The
[command reference](#command-reference) at the bottom covers the individual monitors,
web interface and other tools.

## 1. First-time setup

On the computer that will run detection:

- Get this repository, including the integrated tablet/RTMP changes.
- Prepare the Python environment and model services using the
  [repository setup instructions](../README.md#setup).
- Prepare `.env` from `.env.sample` if `.env` does not already exist. Set
  `BACKEND_TOKEN` to the token you want the tablet to use. Keep it in `.env` or an
  environment variable; do not put it in `config.toml`.
- If using an external camera, set up MediaMTX and the camera using the
  [camera setup guide](live-camera.md). For the existing venue laptop, the
  [venue checklist](run.md) has the Windows firewall and WSL forwarding steps,
  and [camera/Mimo steps](run-2.md) cover starting the Pocket 3 livestream.

Install the tablet APK on the Android device. Build/install instructions are in the
[Android developer setup](../android-app/README.md#developer-setup). If you already
have the APK and Android platform tools, install it with:

```bash
adb install -r android-app/app/build/outputs/apk/debug/app-debug.apk
```

The tablet must be able to reach the backend computer over the network. When using
an external camera, the backend must also be able to reach its video relay.

## 2. Edit the root config.toml

Open `config.toml` in the project folder **on the backend computer**. Edit the
existing sections; do not paste a second copy of a table into the file.

The most relevant settings are:

| Setting | What to enter |
|---|---|
| `[sources.external_camera] url` | Camera stream address reachable from the backend, e.g. `rtsp://<relay-host>:8554/live/x2`. Leave empty if not using it. |
| `[models] qwen_url` | Address of the running Qwen service, e.g. `http://localhost:8010/v1`. |
| `[models] qwen_model` | The model that service actually serves, e.g. `2b` on the small-GPU laptop or `4b` on a suitable GPU server. |
| `[models] yoloe_model` | Detection model; `11s` is the starting choice. |
| `[models] locate_anything` | Set `false` when no LocateAnything service is being used. The current tablet requests it off. |
| `[server] port` | Backend port; normally `8100`. |
| `[server] video_dir` | Folder containing recordings available to the tablet; `.` means the project root. |

For example, these are the relevant parts of a laptop configuration:

```toml
[models]
yoloe_model = "11s"
qwen = true
qwen_model = "2b"
qwen_url = "http://localhost:8010/v1"
locate_anything = false

[sources.external_camera]
name = "External camera"
url = "rtsp://<relay-host>:8554/live/x2"
```

Replace `<relay-host>` with the address of the machine running MediaMTX. In the
Windows/WSL setup, that is the Windows host address reachable from WSL. On a GPU
server, use the relay address reachable from that server.

`localhost` means **this backend computer**. Use another machine's address when the
relay or model service is elsewhere. The tablet's backend address is a different
setting: you enter that in the tablet app, not in the camera URL.

Changing a model name in TOML does not start or switch the model service. Start
the matching service in the next step. `qwen_model = ""` accepts whichever model
the Qwen endpoint serves. If a specified model does not match, tablet detection
continues with Qwen assistance unavailable and dates unverified.

Keep the other defaults initially. Resolution, frame-rate limits and detection
thresholds can be adjusted later. See [Advanced configuration](#advanced-configuration).

## 3. Start the services and backend

Run commands from the project root. Choose the instructions for the computer
running your backend.

### Windows/WSL laptop

In WSL, activate your prepared Python environment. The existing venue laptop uses
`conda activate htx`; another installation may use a virtual environment instead.

Start Qwen using the model selected in `config.toml`:

```bash
bash server/start_laptop.sh
```

Then start the backend in the prepared Python environment:

```bash
python run.py backend
```

Keep that terminal running. For an external camera, also start MediaMTX on Windows
and start the camera livestream. Use the [venue checklist](run.md) for the Windows
firewall and WSL port forwarding needed to reach the backend from the tablet.

### GPU server

With the NVIDIA driver available, the existing full-service launcher can prepare
and start Qwen, LocateAnything and the backend:

```bash
bash server/start_all.sh
```

It uses the Qwen model selected in `config.toml` by default and prints the backend
port and token. It starts both model services, so use the laptop route for the
small-GPU laptop. The first run may take time to install environments and download
weights.

If model services are already running and you only need the backend, use:

```bash
bash server/run_backend.sh
```

Or, with a backend Python environment already active:

```bash
python run.py backend
```

The server scripts manage the Python environment for you; the plain `python`
command assumes you have activated it yourself. If using model services on another
machine, configure their addresses and start those services there separately.

The backend prints its port at startup. If `8100` is occupied, it may choose the
next free port. Use the actual printed port on the tablet.

## 4. Connect the tablet

1. Open the updated tablet app and tap **Settings**.
2. Enter **Backend URL** as `http://<backend-computer-address>:<printed-port>`.
3. Enter the **Backend token** from the backend's `.env` or startup output.
4. Tap **Save**.

For example, if the backend is reachable at `192.168.1.20` on port `8100`, enter
`http://192.168.1.20:8100`. A physical tablet needs a reachable network address;
`localhost` on the tablet points to the tablet itself. The Android emulator can use
`http://10.0.2.2:8100` to reach the development computer.

The tablet needs only the backend address and token. It does not need the external
camera URL or the Qwen address.

## 5. Choose a source on the tablet

Tap **Start demo**, or **Change source** if a source was previously selected:

| Source | What you need |
|---|---|
| **Pre-recorded video** | Choose a file from the backend's video list. Put recordings in `[server] video_dir`. |
| **Tablet camera** | Grant Android camera permission when asked, then use the rear camera. |
| **External camera** | A configured stream URL, the relay running, and the camera actively streaming. No tablet camera permission is needed. |

The external-camera path is:

```text
Camera → RTMP → MediaMTX relay → RTSP → backend detection → tablet display
```

For a quick stream check on the backend machine, before opening the tablet feed:

```bash
python run.py camera-check rtsp://<relay-host>:8554/live/x2
```

The interface, box labels, colors and bottom summary stay the same across sources.
When Qwen is unavailable, detection continues and extinguisher dates are marked
unverified. One detection session runs at a time by default. Source switching waits
for this tablet's previous session to stop; it does not stop another device's session.

## 6. Troubleshooting

| Problem | What to check |
|---|---|
| **External camera is not configured** | Set `[sources.external_camera] url` in `config.toml`, then restart the backend. |
| External camera has no picture | Start MediaMTX and the camera livestream. Run `camera-check` on the backend computer using the same URL. See [camera troubleshooting](live-camera.md). |
| **Backend unavailable** | Check the tablet's URL, printed port, network connection, firewall and WSL forwarding. |
| Token/authentication failure | Enter the backend's current token in the tablet's Settings. |
| **Qwen assistance unavailable** | Check the Qwen service, its URL and the selected model name. Detection can continue without it. |
| No recordings listed | Check `[server] video_dir` and put supported video files there. |
| New source option is missing | Install the newly built tablet APK. |
| Settings changes have no effect | Restart the backend. Configuration is loaded at startup. |
| Another demo is already running | Stop that demo on its owning device before starting another one. |

The stream reader reconnects after camera interruptions. During a short outage the
last picture may remain visible. If no frames arrive within `[input] stream_timeout`
(default 30 seconds), the session ends with an error. Restore the stream and retry.
Source switching can cancel a session waiting for its first frame or a stalled stream.

## Advanced configuration

This section is optional. The root `config.toml` is enough for normal use.

To keep different saved settings for different machines, create a `configs/` folder
and copy the complete `config.example.toml` into `configs/wsl.local.toml` or
`configs/gpu.local.toml`. Edit the copy for that machine. Files ending in
`.local.toml` are Git-ignored, so they are not committed or pushed.

Choose a file explicitly when starting the backend:

```bash
python run.py backend --config configs/gpu.local.toml
# Or use the managed backend environment:
bash server/run_backend.sh --config configs/gpu.local.toml
```

A selected file replaces root `config.toml`. Missing settings use Python's built-in
defaults; they do not inherit values from the root file. Copy the complete example
if you want to start with all its settings.

`HTX_CONFIG` is another way to select a file for the backend and CLI inspection:

```bash
export HTX_CONFIG=/absolute/path/to/configs/gpu.local.toml
python run.py backend
# Optional CLI inspection instead of the tablet:
python run.py inspect external-camera --no-display --stream
```

Use `--config` to override `HTX_CONFIG`. Explicit CLI or session options override
supported TOML settings. Legacy backend path/session-count environment variables
still override TOML. Model URLs use explicit TOML values, then `.env`, then backend
local port-file discovery. Authentication tokens stay in `.env` or the environment.

The laptop launcher reads `HTX_CONFIG` for its model choice; an explicit model
argument wins. The GPU full-service launcher also reads it and forwards it to the
backend. `QWEN_MODEL` remains an explicit launcher override: keep it consistent with
`config.toml`. `--host` and `--port` override the backend's server table; `PORT` is
also supported by `server/run_backend.sh`.

Shared CLI inspection/tablet combined settings include model selection, processing
width, frame-rate caps, task enable flags, monitor thresholds, snapshot policy and
stream timeout. YOLOE weights/input size are chosen when the backend initializes
its shared detectors; restart after changing them. CLI window/save/stream output
settings apply to CLI output. The tablet retains its authenticated video/result
transports. Uploaded tablet-camera frames are never saved as snapshots, even when
`output.snapshots` is true.

## Developer notes and verification

The CLI inspection runner (`apps/fire_safety_inspection.py`) and tablet session
runner (`backend/combined.py`) share the monitors in `apps/`, the TOML loader and
video input code. They keep separate output adapters so the tablet retains its
current annotations, API results and Qwen fallback behavior. TOML configuration
does not automatically add CLI panels or extra labels to the tablet.

Authenticated `GET /api/sources` reports external-camera availability without
returning the stream URL or credentials. The tablet creates a combined session
using the stable `external-camera` source ID. See the
[API reference](android-api.md) for request and response details and the
[Android README](../android-app/README.md) for application development.

```bash
.venv-backend/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv-backend/bin/python tests/test_core.py
android-app/gradlew -p android-app testDebugUnitTest assembleDebug assembleDebugAndroidTest lintDebug
```

Offline checks cover configuration, source discovery/authentication, combined
results, Qwen fallback, stream cancellation and tablet source switching. Actual
camera relay operation, GPU inference, latency and device display still need
verification with the demo equipment.

## Command reference

The remaining sections describe the individual programs and the separate browser
interface. You do not need these commands to operate the tablet demo. The combined
CLI inspection (`python run.py inspect`) is covered in the
[README](../README.md#running). `python run.py` lists all commands, and
`python run.py <command> -h` shows a command's options. Code lives in `apps/`;
`python -m apps.<module>` also works. Old script names work as command aliases,
for example `python run.py exit_monitor IMG_0451.MOV`.

- [Exit monitor](#exit-monitor-python-runpy-exits)
- [Backend and web app](#backend--web-app-backend-frontend)
- [Extinguisher monitor](#fire-extinguishers-python-runpy-extinguishers-first-version)
- [Other runners](#other-runners)
- [Live camera](#live-camera-dji-osmo-pocket-3)
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

The detection pipeline runs as a **FastAPI service on the WSL laptop or GPU server**
(API only). The web app is hosted **separately** and connects with the backend address
and token. The Android tablet uses the same backend and supports combined exit and
extinguisher detection; see [Choose a source](#5-choose-a-source-on-the-tablet).
The browser controls below describe the separate web interface.

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

**Server-side sessions.** This is the path used by the tablet for recorded videos
and the external camera. The server reads a video file or camera stream and sends
annotated MJPEG frames back (`/api/sessions…` in the [API reference](android-api.md#rest-endpoints)). It is useful for
recording and reports, but it lags over a slow link.

**Building your own app** (Android, another web app, a script): the full API, with
request and response examples, is in [Backend API reference](android-api.md).

| Legacy environment setting | Default if not configured in TOML | Meaning |
|---|---|---|
| `BACKEND_TOKEN` | *(none)* | shared secret: `Authorization: Bearer <token>` or `?token=` — **set it** |
| `BACKEND_CORS_ORIGINS` | `*` | origins allowed to call the API, e.g. `http://localhost:5173` |
| `BACKEND_VIDEO_DIR` | repo root | where session videos are read from |
| `BACKEND_MAX_SESSIONS` | `1` | concurrent sessions, live ones included (one GPU pipeline) |
| `LOCATE_URL`, `QWEN_URL` | from the port files | model endpoints when explicit TOML URLs are empty |

For the tablet backend and combined CLI inspection, use root `config.toml` as
described above. These environment variables remain supported; see
[Advanced configuration](#advanced-configuration).

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
