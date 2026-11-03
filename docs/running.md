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
| `[server] record` | `true` (default) saves every camera / stream session as two videos in `results/recordings/`: `<session>_original.mkv` (the frames analysed) and `<session>_annotated.mkv` (with boxes and statuses). Video-file sessions are not recorded. `record_fps` sets their frame rate (default 15). |

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
# Running

Everything you can run, in one place:

1. [**The demo at a venue**](#the-demo-at-a-venue): step by step, from packing to
   stopping. It covers the camera, the laptop and the tablet.
2. [**One-time setup**](#one-time-setup): what to do once for the laptop and the camera.
3. [**Other programs**](#other-programs): each monitor on its own, the backend and web
   app, the simpler runners, and the tools.

Every program starts from `run.py` in the project root. `python run.py` lists the
commands, and `python run.py <command> -h` shows a command's options. The code is in
`apps/` (`python -m apps.<module>` works too). The old script names also work as
commands, for example `python run.py exit_monitor IMG_0451.MOV`.

Related docs:

- The inspection's settings (`config.toml`, choosing models) are in the
  [README](../README.md#running).
- The full camera guide is [live-camera.md](live-camera.md).
- The API for the tablet app is [android-api.md](android-api.md).

---

## The demo at a venue

This runs the demo on the dev laptop at a venue with its own Wi-Fi. The laptop needs
the [one-time setup](#one-time-setup) first.

**What runs where:**

```
Pocket 3 ──Wi-Fi, RTMP──► MediaMTX (Windows, :1935 in, :8554 out) ──► WSL: python run.py inspect
                                                                         (exits + extinguishers on the laptop screen)
Tablet app ──Wi-Fi──► Windows :8100 ──port forward──► WSL: backend (python run.py backend)
Qwen3-VL-2B runs in WSL (tmux session "qwen"); both the inspection and the backend use it.
```

**What changes at a new venue:**

- the laptop's Wi-Fi IP, which the camera (in Mimo) and the tablet app both need;
- the camera, the phone and the tablet must all join the venue Wi-Fi;
- Windows treats the new network as **Public**. The firewall rules already allow
  Public networks; step 2 checks this.

Step 2 prints every address you need, so you don't have to look anything up.

### Before you leave

- [ ] Charge the camera, the phone with DJI Mimo, and the tablet. Bring the laptop
      charger: on battery the GPU runs slower.
- [ ] In WSL, run `cd ~/htx-demo && conda activate htx && python tests/test_core.py`. It
      should print `16/16 passed`.
- [ ] Download every model you plan to show, while you still have fast internet:
  - Qwen: `bash server/start_laptop.sh 2b` (the default). Also run it with `2b-fp8` if
    you want to show that one; each first start downloads the model.
  - YOLOE: `python run.py inspect --list-models` shows which are `downloaded`. Running
    the inspection once with `--yoloe <name>` downloads another one.
- [ ] Note the backend token for the tablet app: `grep BACKEND_TOKEN ~/htx-demo/.env`.
- [ ] Bring a backup network: a travel router, or a hotspot on a second phone. Venue
      Wi-Fi often blocks devices from reaching each other, or needs a login page that
      the camera can't use.
- [ ] Optional: copy a recorded video to `~/htx-demo` as a fallback if the camera
      fails.

### 1. Join the Wi-Fi and check it is usable

Connect the laptop to the venue Wi-Fi. The network must meet three conditions:

- **One network for everything:** the laptop, the camera, the phone with Mimo and the
  tablet.
- **No login page.** The camera can't get past a "click to accept" page.
- **Devices can reach each other.** Guest networks usually block this. Step 6 tests it.

If any of these fails, switch everything to your backup network and carry on with step 2.

### 2. Open the laptop to the network (Windows, Administrator PowerShell)

Open **PowerShell as Administrator** and paste this block. It is safe to run again, and
you must run it again after every reboot, because WSL's internal address changes.

```powershell
$port = 8100
$wsl  = (wsl hostname -I).Trim().Split(' ')[0]          # WSL's internal IP (changes after a reboot)
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port 2>&1 | Out-Null
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$port connectaddress=$wsl connectport=$port
if (-not (Get-NetFirewallRule -DisplayName "htx backend 8100" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "htx backend 8100" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port | Out-Null
}
Set-NetFirewallRule -DisplayName "htx backend 8100" -Profile Private,Public -RemoteAddress LocalSubnet
if (-not (Get-NetFirewallRule -DisplayName "MediaMTX" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "MediaMTX" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1935,8554 | Out-Null
}
$ip = (Get-NetIPAddress -InterfaceAlias "Wi-Fi" -AddressFamily IPv4).IPAddress
""
"Laptop Wi-Fi IP       : $ip"
"DJI Mimo RTMP address : rtmp://${ip}:1935/live/x2"
"Tablet app backend URL: http://${ip}:${port}"
"Tablet browser check  : http://${ip}:${port}/api/health"
```

Keep the four printed lines visible; the next steps use them. The block:

- forwards port 8100 on the Wi-Fi to the backend in WSL (the tablet can't reach WSL
  directly);
- lets devices on the same Wi-Fi, and only them, through the firewall on port 8100 and
  on the MediaMTX ports 1935 and 8554.

The backend still needs the token, so others on the venue Wi-Fi can't use it.

### 3. Start Qwen and the backend (WSL terminal 1)

```bash
conda activate htx && cd ~/htx-demo
bash server/start_laptop.sh       # Qwen; wait for "Qwen is up" (about 1 min) or "already running"
python run.py backend             # leave it running
```

- The backend must say **port 8100**. If it says 8101 or higher, an old backend is
  still running: stop it with `pkill -f "run.py backend"` and start this one again. The
  port forward only covers 8100.
- Start Qwen before anything else that uses the GPU, because vLLM reserves its memory
  when it starts.

Steps 4 and 5 can be done while Qwen loads.

### 4. Start MediaMTX (Windows)

Run `C:\mediamtx\mediamtx.exe` from its own folder. Its log should say
`configuration loaded`. Keep the window open.

### 5. Start the camera stream (phone with DJI Mimo)

1. Join the **phone** to the venue Wi-Fi. If Android warns that the Wi-Fi has no
   internet, choose to stay connected.
2. Power on the Pocket 3: rotate the touchscreen clockwise, or press the Shutter/Record
   button. Open Mimo and wait until the live view appears.
3. Slide the bottom menu to **LIVESTREAM**, choose **RTMP**, then tap **Start
   Livestreaming Settings**. Wait about 15 seconds on that screen.
4. Under **Camera Network Status**, connect the **camera** to the same Wi-Fi. Enter the
   password if asked.
5. Set the streaming address to the **DJI Mimo RTMP address** printed in step 2, for
   example `rtmp://192.168.50.22:1935/live/x2`.
   - There is no stream key.
   - Keep the path exactly `live/x2`, because the WSL commands use the same name.
   - Don't use the WSL `$WIN` address here.
6. Optionally, swipe for more settings and lower the quality. A lower resolution or
   bitrate can cut the delay.
7. Tap **Start Livestream**. On iPhone, allow Local Network access if asked.
8. **Check the MediaMTX window.** It should show `is publishing to path 'live/x2'`. If
   nothing appears, see [If something goes wrong](#if-something-goes-wrong).
9. Keep Mimo open with the phone screen on for the whole demo.

The menu names come from DJI's setup guide and may differ a little between Mimo
versions.

### 6. Connect the tablet

1. Join the **tablet** to the same Wi-Fi.
2. In the tablet's browser, open the **Tablet browser check** URL from step 2. It must
   show `{"ok":true,"auth_required":true}`. If it times out, see
   [If something goes wrong](#if-something-goes-wrong).
3. In the app, enter the **Tablet app backend URL** and the token.
4. To show the **Pocket 3's** picture on the tablet (session mode), the app also needs
   the camera stream's address as the backend sees it. Print it in WSL with
   `echo rtsp://$(ip route show default | awk '{print $3}'):8554/live/x2`.
   - Session mode shows exits only.
   - With the tablet's own camera (live mode), the app needs no address; it can show
     exits or extinguishers.

The backend runs one tablet session at a time.

### 7. Run the inspection on the laptop screen (WSL terminal 2)

```bash
conda activate htx && cd ~/htx-demo
WIN=$(ip route show default | awk '{print $3}')                       # Windows, as seen from WSL
python run.py camera-check rtsp://$WIN:8554/live/x2                   # optional: picture arrives? q quits
python run.py inspect rtsp://$WIN:8554/live/x2 --save results/venue_$(date +%H%M).mp4
```

- The window shows the original next to the annotated video, with an exit panel and an
  extinguisher panel. Keys: `q` quits, `space` pauses.
- Reports go to `results/fire_safety_live/`.
- Settings such as frame rates, resolution and models are in `config.toml`; see the
  [README](../README.md#running).
- **Fallback without the camera:** `python run.py inspect <video file> --loop`.
- The inspection and one tablet session can run at the same time. Tested on a recorded
  video: the tablet's round trip stayed at about 30 ms, and the inspection ran at about
  40 ms per frame.

### Stopping

1. Press `q` in the inspection window.
2. Press `Ctrl+C` in the backend terminal. This also finishes the session recordings in
   `results/recordings/`.
3. Stop the livestream in Mimo, close MediaMTX, and turn off the camera.
4. Free the GPU: `bash server/stop_all.sh qwen`.

The port forward and firewall rules can stay; they only let in devices on the same
network, and only on the demo ports.

### If something goes wrong

| Symptom | Cause and fix |
|---|---|
| Tablet: **connection timed out** | (1) The laptop was rebooted, or WSL restarted, since step 2: run the step 2 block again. (2) The tablet is on another network: its IP must start like the laptop's (e.g. `192.168.50.x`). (3) The Wi-Fi blocks devices from reaching each other: switch to your backup network. |
| Tablet: **connection refused** | The backend isn't running, or it runs on another port than 8100 (step 3). |
| Tablet: **401** or WebSocket closed at once | Wrong token. Check it with `grep BACKEND_TOKEN ~/htx-demo/.env`. |
| Tablet: **already running … stop it first** | The backend runs one tablet session at a time. Close the session in the app, or restart the backend. |
| Mimo **can't find the camera** | Check that the phone's Wi-Fi and Bluetooth are both on and that Mimo is up to date. A DJI Mic 2 on 2.4 GHz blocks Mimo: switch the camera to 5.8 GHz under Wireless Connection, or disconnect the mic. |
| Mimo: **Livestream failed**, or nothing in the MediaMTX log | The RTMP address must be the **Wi-Fi IP printed in step 2** (it changes per venue), not the WSL address. The camera must be on the same Wi-Fi. A login page also blocks the camera: use the backup network. If the firewall rule for TCP 1935 is missing, run the step 2 block again. |
| Inspection: **`method DESCRIBE failed: 404 (Not Found)`**, then **No frames from rtsp://… after 30 s** | MediaMTX is reachable, but the camera isn't publishing to `live/x2`; its log shows no `is publishing to path` line. Usually Mimo still has an old address: the Wi-Fi IP changes with every network, including when you switch to a phone hotspot. Run the step 2 block again and put the new **DJI Mimo RTMP address** into Mimo. The inspection retries for 30 s, so it picks up the stream if you fix it in time. |
| Inspection: **No frames from rtsp://… after 30 s**, without a 404 | MediaMTX isn't running. In WSL, use `$WIN` (step 7), not the Wi-Fi IP. |
| Inspection or backend: **Cannot reach Qwen** | Run `bash server/status.sh`, then `bash server/start_laptop.sh`. |
| **Qwen doesn't come up on a network without internet** | The models are on the laptop, but Qwen may wait on Hugging Face. Start it with `bash server/stop_all.sh qwen` and then `HF_HUB_OFFLINE=1 bash server/start_laptop.sh`. This only works when no other tmux session is open: Qwen runs in tmux, and otherwise it won't see the setting. |
| Frequent "stream lost — reconnecting" | Weak Wi-Fi, the phone went to sleep, or the camera is hot or low on battery. Runs reconnect by themselves and end only after 30 s without frames. |

### Notes

- **The two IPs are easy to mix up.** Mimo and the tablet use the laptop's **Wi-Fi IP**
  (printed in step 2). Commands in WSL use **`$WIN`**, Windows as seen from inside WSL.
- **Only YOLOE and Qwen run here.** LocateAnything doesn't fit in the laptop's 8 GB GPU,
  so the demo runs YOLOE + Qwen3-VL-2B.
- **Watch the verdicts.** Qwen-2B is the weakest Qwen model. On a drawn test scene, its
  verdicts flipped between "partly blocked" and "clear", so check them on real footage.
- **The first picture can take a few seconds to appear.**
- **What has run with the real camera so far:**
  - The exit monitor ran for about 2 minutes on the Pocket 3 stream: 3,096 frames, 83
    dropped, 22 ms per frame.
  - The combined inspection hasn't run on the camera yet.
  - To measure the camera's delay, use `python run.py camera-check --clock` (see
    [live-camera.md](live-camera.md)).

---

## One-time setup

### Laptop

The dev laptop already has all of this. For another laptop, start with the README's
[Setup](../README.md#setup) (WSL, the Python environment, the Qwen server), then:

1. **MediaMTX:** download MediaMTX for Windows from its GitHub releases page, and unzip
   it to `C:\mediamtx`.
2. **Backend packages**, if `python run.py backend` reports a missing `fastapi`:
   `pip install "fastapi==0.121.3" "uvicorn[standard]==0.38.0"` (both are in
   `requirements.txt`).
3. **Backend token:**
   `echo "BACKEND_TOKEN=$(python -c 'import secrets; print(secrets.token_hex(16))')" >> .env`.
   The tablet app needs it.
4. **Port forward and firewall rules:** the block in [step 2](#2-open-the-laptop-to-the-network-windows-administrator-powershell)
   creates them.
5. **Models:** see [Before you leave](#before-you-leave).

### Camera and DJI Mimo

1. **Charge the camera.** Firmware updates won't run below 15%. Charge it fully before a
   demo.
2. **Install DJI Mimo.**
   - Android 9 or later: download the APK from
     [DJI's Pocket 3 downloads page](https://www.dji.com/osmo-pocket-3/downloads). It
     isn't on Google Play, and you should avoid third-party APK sites.
   - iPhone: get it from the App Store.
3. **Open Mimo and allow its permissions:** Nearby devices, Location, and photos/media.
   Without Nearby devices and Location, the app can't find the camera over Bluetooth.
4. **Android only:**
   - Go to Settings → Apps → DJI Mimo → Battery and choose **Unrestricted**.
   - On Samsung, also turn off *Intelligent Wi-Fi → Switch to mobile data*.
5. **Turn on the phone's Wi-Fi and Bluetooth.** Mimo uses both.
6. **Power on the Pocket 3:** rotate the touchscreen clockwise, or press the
   Shutter/Record button.
7. **Connect in Mimo:**
   - On the home screen, tap the camera icon, choose **Osmo Pocket 3** and follow the
     prompts. Keep the camera next to the phone.
   - Activate it when asked. It may ask you to sign in to a DJI account.
8. **Install a firmware update if Mimo offers one.** It takes about 2 minutes. Don't
   power off the camera while it runs.
9. **Check it works:** Mimo should show the camera's live view.

---

## Other programs

### Exit monitor: `python run.py exits`

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

### Backend + web app (`backend/`, `frontend/`)

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

**Recordings.** The backend saves each camera / stream session (live mode, or a server
session from a stream URL or the external camera) as `results/recordings/<session>_original.mkv`
and `<session>_annotated.mkv`; see `[server] record` in `config.toml`. The videos play in
real time at `record_fps`. Replay the original through the pipeline with
`python run.py inspect results/recordings/<session>_original.mkv`.

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

### Fire extinguishers: `python run.py extinguishers` (first version)

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

### Other runners

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

### Live camera (DJI Osmo Pocket 3)

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

### Tools

```bash
python run.py camera-check rtsp://<host>:8554/live/x2 --clock   # camera link check, no models
python run.py bench IMG_0450.MOV --provider local --sizes 448 640 --concurrency 1 4 8
python run.py locate-test IMG_0450.MOV --url http://<srv>:8020 --mode fast
python tests/test_core.py
bash server/run_realtime_test.sh IMG_0450.MOV   # on the server: benchmark + live run + verdict
bash server/watch_live.sh IMG_0450.MOV          # on the server: live_infer → browser
```
