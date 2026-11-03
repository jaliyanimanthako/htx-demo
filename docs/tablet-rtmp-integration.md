# Tablet demo with the RTMP backend

The Android interface supports three sources: **Pre-recorded video**, **Tablet camera**,
and **External camera**. All three use combined exit/obstruction and extinguisher
monitoring. The existing tablet layout, colors, labels and summary are retained.
External camera uses the server-side session/video path, so no Android camera
permission is needed for it.

## Shared configuration

`config.toml` in the project root is shared by `python run.py inspect` and
`python run.py backend` (also `python -m backend`). The same backend runs on the
Windows/WSL laptop or the GPU server. The tablet only stores the backend address
and token in Settings; camera URLs and model settings stay on that backend.

For separate machines, copy `config.example.toml` into `configs/wsl.local.toml`
or `configs/gpu.local.toml` (create `configs/` first). These local files are ignored
by Git. A selected file replaces the root TOML file; missing values use built-in
defaults, rather than inheriting values from the root file.

Set the external-camera URL reachable **from the Python backend machine**:

```toml
[server]
host = "0.0.0.0"
port = 8100
max_sessions = 1
video_dir = "."

[sources.external_camera]
name = "External camera"
url = "rtsp://<relay-host>:8554/live/x2"

[input]
stream_timeout = 30
camera_size = [1920, 1080]

[models]
yoloe_model = "11s"
qwen = true
qwen_model = "2b"                  # choose what the Qwen server actually serves
qwen_url = "http://localhost:8010/v1"
locate_anything = false
```

Use the actual model endpoint and model choice on each machine; a GPU server can
use a larger Qwen model. `qwen_model = ""` accepts whichever model the endpoint
serves. A model mismatch makes tablet Qwen assistance unavailable rather than
silently using another model. Detection still runs, with dates unverified.

Settings shared by CLI inspection and tablet combined sessions include model
selection, processing width, processing/task frame-rate caps, task enable flags,
monitor thresholds, snapshot policy and input timeout. API inference options can
override supported monitor thresholds. YOLOE weights/input size are chosen when
the backend initializes its shared detectors; restart after changing them.

CLI display/window/save/stream options remain specific to CLI output. The tablet
uses its existing authenticated MJPEG and WebSocket transports. Uploaded tablet
camera frames are never saved as snapshots, even if output.snapshots is true.

Precedence is built-in defaults → selected TOML → explicit CLI or session
options. Credentials remain in `.env`/environment variables. Legacy backend
path/session-count environment variables still override TOML. Model URLs use
explicit TOML values, then `.env`, then backend local port-file discovery.
Restart the backend after changing configuration.

## Start on either backend machine

With the backend Python environment active:

```bash
python run.py backend --config configs/wsl.local.toml
# Or:
python run.py backend --config configs/gpu.local.toml
```

An environment variable is also supported by the inspection runner and backend:

```bash
export HTX_CONFIG=/absolute/path/to/configs/gpu.local.toml
python run.py backend
python run.py inspect external-camera --no-display --stream
```

With the existing managed environment setup, use:

```bash
bash server/run_backend.sh --config configs/gpu.local.toml
```

`--host` and `--port` override the server table. The backend still chooses the next
free port if the requested port is occupied; use the printed port on the tablet.
`PORT` remains an explicit port override for `server/run_backend.sh`.

Start model services separately using the existing setup appropriate to the
machine: `server/start_laptop.sh` for the small-GPU laptop or the GPU-server
service scripts. The laptop script reads `HTX_CONFIG` when choosing a model;
an explicit model argument wins. `server/start_all.sh` also reads the selected
TOML for its default Qwen model and forwards HTX_CONFIG to the backend;
QWEN_MODEL remains an explicit startup override. Selecting a remote URL does not deploy or move
model services. Set `BACKEND_TOKEN` in `.env` or the environment.

## External camera

The RTMP branch's camera path is:

```text
Camera → RTMP → MediaMTX relay → RTSP → Python backend → annotated MJPEG → tablet
```

Follow [live-camera.md](live-camera.md) for the relay/camera setup. The Windows/WSL
instructions apply when the relay runs on Windows and Python runs in WSL. For a
GPU server, use the relay address reachable from that server. Do not use
`localhost` for a relay running on another machine.

1. Start the relay and camera, and check the stream using `python run.py camera-check <URL>`.
2. Configure `sources.external_camera.url`, then start/restart the backend.
3. On the tablet, set the backend address and token.
4. Select **Change source → External camera** (or **Start demo → External camera**).

If the URL is empty, the app explains that external camera is not configured,
without stopping the current session. The backend returns source availability
through authenticated `GET /api/sources`; it does not send stream credentials to
the tablet. Sessions use the stable `external-camera` source ID. Only one session
runs at a time, and source switching waits for the tablet's previous session to
stop. Another device's session is not stopped.

The input reader keeps the newest frame and reconnects after stream loss. If no
frames arrive within input.stream_timeout, the session ends with an error.
During a short interruption the last image may remain visible. Switching sources
can cancel a session waiting for its first frame or a stalled stream.

## Two combined runners

`apps/fire_safety_inspection.py` owns the CLI inspection loop and CLI panels.
`backend/combined.py` owns tablet sessions, API results and tablet-style annotations.
Both use the monitors in `apps/`, the shared TOML loader, and `core/video_io.py`.
Keeping separate output adapters preserves the tablet's existing presentation and
Qwen-unavailable behavior. `config.toml` does not automatically add CLI panels or
extra hints to the tablet interface.

## Verification

```bash
.venv-backend/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv-backend/bin/python tests/test_core.py
android-app/gradlew -p android-app testDebugUnitTest assembleDebug assembleDebugAndroidTest lintDebug
```

Offline checks cover configuration loading, API source discovery/authentication,
combined detection/Qwen fallback, stopping a waiting external stream, source
switching, and saved external-source selection. The camera relay, GPU inference,
latency and on-device display still require testing with the actual equipment.
