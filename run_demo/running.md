# Running

Everything you can run, in one place:

1. [**What runs where**](#what-runs-where): the backend, and the programs that show its results.
2. [**The demo at a venue**](#the-demo-at-a-venue): step by step, from packing to stopping, on
   the dev laptop with the Pocket 3 and the tablet.
3. [**The backend on a GPU server**](#the-backend-on-a-gpu-server): the same demo, with
   detection on a separate server.
4. [**Settings**](#settings-configtoml): `config.toml`, a settings file per machine, and the
   environment variables.
5. [**One-time setup**](#one-time-setup): the laptop, the tablet app and the camera.
6. [**Other programs**](#other-programs): each monitor on its own, the web app, the tablet web
   page, the simpler runners, the tools and the tests.

At a venue, once you have done the demo before, the one page in [README.md](README.md) is
enough. Its two scripts in this folder, `run_windows.cmd` and `run_wsl.sh`, do steps 2–4
of [the demo at a venue](#the-demo-at-a-venue).

Every program starts from `run.py` in the project root. `python run.py` lists the
commands, and `python run.py <command> -h` shows a command's options. The code is in
`apps/` (`python -m apps.<module>` works too). The old script names also work as
commands, for example `python run.py exit_monitor IMG_0451.MOV`.

Related docs:

- The inspection (`python run.py inspect`) and its settings: [README](../README.md#running).
- The camera: [live-camera.md](../docs/live-camera.md).
- The backend API: [android-api.md](../docs/android-api.md).
- The tablet app, how to build it and how it works inside:
  [android-app/README.md](../android-app/README.md).
- The tablet web page: [web-tablet/README.md](../web-tablet/README.md).

---

## What runs where

Detection runs in one place, the **backend** (`python run.py backend`), on the laptop (in
WSL) or on a GPU server. It runs YOLOE, and uses Qwen when the Qwen server is up. The other
programs show its results:

| Program | Runs on | Sources | Use it for |
|---|---|---|---|
| **Tablet app** (`android-app/`) | the Android tablet | Pre-recorded video, Tablet camera, External camera | the demo: exits and extinguishers on one screen |
| **Tablet web page** (`web-tablet/`) | any browser | Pre-recorded video, External camera | a fallback when the app has trouble; the same screen |
| **Web app** (`frontend/`) | any browser | a video file or camera in the browser | development: exits *or* extinguishers, one at a time |
| **Inspection** (`python run.py inspect`) | the laptop screen | video file, webcam, stream URL | exits and extinguishers with panels. It runs its own models, without the backend. |

**Pre-recorded video** and **External camera** are *server sessions*: the backend reads the
video or the stream and sends the annotated picture back. **Tablet camera** is a *live
session*: the tablet sends its frames and draws the returned boxes over its own preview.
The backend runs one session at a time.

---

## The demo at a venue

This runs the demo on the dev laptop at a venue with its own Wi-Fi. The laptop and the
tablet need the [one-time setup](#one-time-setup) first.

**What runs where:**

```
Pocket 3 ──Wi-Fi, RTMP──► MediaMTX on Windows ──RTSP :8554──┐
                          (:1935 in, :8554 out)             ▼
Tablet app ──Wi-Fi──► Windows :8100 ──port forward──► WSL: backend (python run.py backend)
                                                       └─ YOLOE, and Qwen3-VL-2B in tmux session "qwen"
```

The backend reads the camera stream while the tablet shows **External camera**. The
inspection can show the same stream on the laptop screen
([step 7](#7-optional-the-inspection-on-the-laptop-screen-wsl-terminal-2)).

**Short version**, once you have done it before ([README.md](README.md) has it on one page):

1. Windows: double-click `run_demo\run_windows.cmd` and allow administrator rights. It does
   [step 2](#2-open-the-laptop-to-the-network-windows-administrator-powershell) and
   [step 4](#4-start-mediamtx-windows), and prints the addresses for Mimo and the tablet.
2. WSL: `bash run_demo/run_wsl.sh` (or `GPU_UTIL=0.86 MAX_LEN=2048 bash run_demo/run_wsl.sh 4b-fp8`).
   It does [step 3](#3-start-qwen-and-the-backend-wsl-terminal-1) in the background, then
   prints a status check, the addresses and the token.
3. Phone: start the livestream in Mimo to the printed RTMP address. Then
   `bash run_demo/run_wsl.sh check` shows the camera as streaming.
4. Tablet: enter the backend URL and the token in **Settings**, tap **Start demo** and pick
   a source.
5. Afterwards: `bash run_demo/run_wsl.sh stop`.

**What changes at a new venue:**

- the laptop's Wi-Fi IP, which the camera (in Mimo) and the tablet app both need;
- the camera, the phone and the tablet must all join the venue Wi-Fi;
- Windows treats the new network as **Public**. The firewall rules already allow
  Public networks; step 2 checks this.

Steps 2 and 3 print every address you need, so you don't have to look anything up.

### Before you leave

- [ ] Charge the camera, the phone with DJI Mimo, and the tablet. Bring the laptop
      charger: on battery the GPU runs slower.
- [ ] Install the latest tablet app ([Tablet app](#tablet-app-android)). Older builds have no
      **External camera** option.
- [ ] In WSL, run `cd ~/htx-demo && conda activate htx && python tests/test_core.py`. It
      should print `17/17 passed`.
- [ ] Download every model you plan to show, while you still have fast internet:
  - Qwen: `bash server/start_laptop.sh 2b` (the default). Also run it with `2b-fp8` if
    you want to show that one; each first start downloads the model.
  - YOLOE: `python run.py inspect --list-models` shows which are `downloaded`. Running
    the inspection once with `--yoloe <name>` downloads another one.
- [ ] Note the backend token for the tablet app: `grep BACKEND_TOKEN ~/htx-demo/.env`.
- [ ] Bring a backup network: a travel router, or a hotspot on a second phone. Venue
      Wi-Fi often blocks devices from reaching each other, or needs a login page that
      the camera can't use.
- [ ] Copy one or two recorded videos to `~/htx-demo`, the backend's `[server] video_dir`.
      The tablet lists them under **Pre-recorded video**, the fallback if the camera
      fails.

### 1. Join the Wi-Fi and check it is usable

Connect the laptop to the venue Wi-Fi. The network must meet three conditions:

- **One network for everything:** the laptop, the camera, the phone with Mimo and the
  tablet.
- **No login page.** The camera can't get past a "click to accept" page.
- **Devices can reach each other.** Guest networks usually block this. Step 6 tests it.

If any of these fails, switch everything to your backup network and carry on with step 2.

### 2. Open the laptop to the network (Windows, Administrator PowerShell)

**`run_demo\run_windows.cmd` does this step and [step 4](#4-start-mediamtx-windows).**
Double-click it in Explorer, at `\\wsl.localhost\Ubuntu-24.04\home\peter\htx-demo\run_demo`
(a desktop shortcut to it works too), and allow administrator rights. It prints the same
five lines. Its first line, `UNC paths are not supported`, is harmless.

To do it by hand, open **PowerShell as Administrator** and paste this block. Either way,
it is safe to run again, and you must run it again after every reboot, because WSL's
internal address changes.

```powershell
$wsl = (wsl hostname -I).Trim().Split(' ')[0]           # WSL's internal IP (changes after a reboot)
function Open-WslPort($port, $name) {                     # forward a Wi-Fi port to WSL, let the local subnet in
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port 2>&1 | Out-Null
    netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$port connectaddress=$wsl connectport=$port
    if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port | Out-Null
    }
    Set-NetFirewallRule -DisplayName $name -Profile Private,Public -RemoteAddress LocalSubnet
}
Open-WslPort 8100 "htx backend 8100"                      # the backend
Open-WslPort 5174 "htx web page 5174"                     # the tablet web page (fallback)
if (-not (Get-NetFirewallRule -DisplayName "MediaMTX" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "MediaMTX" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1935,8554 | Out-Null
}
$ip = (Get-NetIPAddress -InterfaceAlias "Wi-Fi" -AddressFamily IPv4).IPAddress
""
"Laptop Wi-Fi IP       : $ip"
"DJI Mimo RTMP address : rtmp://${ip}:1935/live/x2"
"Tablet app backend URL: http://${ip}:8100"
"Tablet browser check  : http://${ip}:8100/api/health"
"Tablet web page       : http://${ip}:5174"
```

Keep the five printed lines visible; the next steps use them. The block:

- forwards two ports on the Wi-Fi into WSL: 8100 to the backend, and 5174 to the
  [tablet web page](#if-the-tablet-app-fails-the-tablet-web-page). WSL is behind NAT, so
  the tablet reaches only the ports Windows forwards. The laptop's own browser reaches
  WSL through `localhost` without a forward, so a page that works on the laptop can
  still fail on the tablet;
- lets devices on the same Wi-Fi, and only them, through the firewall on ports 8100 and
  5174, and on the MediaMTX ports 1935 and 8554.

The backend still needs the token, so others on the venue Wi-Fi can't use it. The web
page is static files and holds no token.

### 3. Start Qwen and the backend (WSL terminal 1)

**`bash run_demo/run_wsl.sh` does this step**, and leaves the terminal free:

- It starts Qwen, sets the camera URL in `config.toml`, and runs the backend and both web
  pages in tmux sessions (`qwen`, `backend`, `webpage`, `webapp`).
- Then it checks everything and prints the addresses and the token.
  `bash run_demo/run_wsl.sh check` checks again.
- `tmux attach -t backend` shows the backend's output (`Ctrl+B`, then `D`, leaves it
  running).
- To run it with another Qwen model, put the model name last:
  `GPU_UTIL=0.86 MAX_LEN=2048 bash run_demo/run_wsl.sh 4b-fp8`.

By hand:

```bash
conda activate htx && cd ~/htx-demo
bash server/start_laptop.sh        # Qwen; wait for "Qwen is up" (about 1 min) or "already running" ... GPU_UTIL=0.86 MAX_LEN=2048 bash server/start_laptop.sh 4b-fp8
echo rtsp://$(ip route show default | awk '{print $3}'):8554/live/x2   # the camera URL as WSL sees it
python run.py backend             # leave it running
```

- **Before you start the backend,** check that `[sources.external_camera] url` in
  `config.toml` is the printed camera URL (`run_wsl.sh` sets it). Its address is Windows as
  seen from WSL, and it can change after a reboot. The backend reads `config.toml` only
  when it starts.
- The backend must say **port 8100**. If it says 8101 or higher, an old backend is
  still running: stop it with `pkill -f "run.py backend"` and start this one again. The
  port forward only covers 8100. (`run_wsl.sh` stops old backends first.)
- Start Qwen before anything else that uses the GPU, because vLLM reserves its memory
  when it starts.

Steps 4 and 5 can be done while Qwen loads.

### 4. Start MediaMTX (Windows)

`run_windows.cmd` starts it. By hand: run `C:\mediamtx\mediamtx.exe` from its own folder.
Its log should say `configuration loaded`. Keep the window open.

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
   - Keep the path exactly `live/x2`, because the camera URL in `config.toml` uses the
     same name.
   - Don't use the camera URL from step 3 here; that address only works inside the
     laptop.
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
3. In the app, tap **Settings**, enter the **Tablet app backend URL** from step 2 and the
   token, and tap **Save**. The app remembers both.
4. Tap **Start demo** (later: **Change source**) and pick a source:

| Source | What the tablet shows | Needs |
|---|---|---|
| **External camera** | the Pocket 3, annotated by the backend | the camera URL in `config.toml` (step 3), MediaMTX (step 4), the camera streaming (step 5) |
| **Tablet camera** | its rear camera, with the boxes drawn over the preview | camera permission, asked the first time |
| **Pre-recorded video** | a video from the backend's `[server] video_dir`, in a loop | a video file in that folder |

All three show exits and extinguishers together, with the same labels and colors. Without
Qwen, detection goes on, and extinguisher dates show as not verified.

- The backend runs one session at a time. Switching source stops this tablet's own
  session first; it never stops another device's.
- The backend records every **External camera** and **Tablet camera** session; see
  [Recordings](#recordings).

### 7. Optional: the inspection on the laptop screen (WSL terminal 2)

The tablet demo doesn't need this. It shows the Pocket 3 on the laptop screen, with an
exit panel and an extinguisher panel:

```bash
conda activate htx && cd ~/htx-demo
WIN=$(ip route show default | awk '{print $3}')                       # Windows, as seen from WSL
python run.py camera-check rtsp://$WIN:8554/live/x2                   # optional: picture arrives? q quits
python run.py inspect rtsp://$WIN:8554/live/x2 --save results/venue_$(date +%H%M).mp4
```

- The window shows the original next to the annotated video. Keys: `q` quits, `space`
  pauses.
- Reports go to `results/fire_safety_live/`.
- Settings such as frame rates, resolution and models are in `config.toml`; see the
  [README](../README.md#running).
- **Fallback without the camera:** `python run.py inspect <video file> --loop`.
- The inspection loads its own YOLOE models next to the backend's. With Qwen-2B, both ran
  at the same time on a recorded video: the tablet's round trip stayed at about 30 ms, and
  the inspection ran at about 40 ms per frame.

### If the tablet app fails: the tablet web page

The [tablet web page](#tablet-web-page-web-tablet) shows the app's screen in a browser, for
**External camera** and **Pre-recorded video** (it has no tablet camera). If the app's
session is still running, the page joins it as a second viewer, so detection doesn't
restart. `run_wsl.sh` starts it. By hand, in WSL terminal 3:

```bash
conda activate htx && cd ~/htx-demo
python web-tablet/serve.py        # port 5174; leave it running
```

- **On the laptop:** open `http://localhost:5174` in a Windows browser. In **Settings**,
  enter `http://localhost:8100` and the token.
- **On the tablet or a phone:** open the **Tablet web page** URL printed in step 2. In
  **Settings**, enter the **Tablet app backend URL** from step 2 and the token. Don't use
  `localhost:8100` here: on the tablet, `localhost` is the tablet itself.
  - Step 2 forwards port 5174 from the Wi-Fi to WSL. If the page opens on the laptop but
    times out on the tablet, run `run_windows.cmd` again (step 2).
- To join the app's session, pick the **same source** (for a video, the same file). While
  that session runs, other sources are refused: stop it in the app first. If the app was
  on **Tablet camera**, stop it there too, because the backend has no picture of it to
  show.
- Closing the page leaves the session running on the backend.

### Stopping

1. Press `q` in the inspection window, if it runs.
2. In WSL, run `bash run_demo/run_wsl.sh stop`. It stops:
   - the web pages;
   - the backend, which finishes the session recordings in `results/recordings/`
     ([Recordings](#recordings));
   - Qwen, which frees the GPU.

   By hand: press `Ctrl+C` in the backend's terminal and in the web page's terminal, then
   run `bash server/stop_all.sh qwen`.
3. Stop the livestream in Mimo, close MediaMTX, and turn off the camera.

The port forward and firewall rules can stay; they only let in devices on the same
network, and only on the demo ports.

### If something goes wrong

| Symptom | Cause and fix |
|---|---|
| Tablet: **connection timed out** | (1) The laptop was rebooted, or WSL restarted, since step 2: run `run_windows.cmd` again (step 2). (2) The tablet is on another network: its IP must start like the laptop's (e.g. `192.168.50.x`). (3) The Wi-Fi blocks devices from reaching each other: switch to your backup network. |
| Tablet: **connection refused** | The backend isn't running, or it runs on another port than 8100 (step 3). |
| Tablet: **401**, or the WebSocket closes at once | Wrong token. Check it with `grep BACKEND_TOKEN ~/htx-demo/.env`. |
| Tablet: **already running**, or the backend is in use by another device | The backend runs one session at a time. Stop it on the device that started it (putting the app in the background doesn't stop a **Pre-recorded video** session), or restart the backend. |
| Tablet: **External camera is not configured** | `[sources.external_camera] url` in `config.toml` is empty. Set it (step 3) and restart the backend. |
| Tablet: **External camera** shows no picture | MediaMTX isn't running, or the camera isn't publishing: the MediaMTX log must show `is publishing to path 'live/x2'`. In WSL, `python run.py camera-check <the URL from config.toml>` must show the picture. The session ends after 30 s without frames (`[input] stream_timeout`); fix the stream, then pick the source again. During a short outage, the last picture stays on screen. |
| Tablet: no **External camera** option | Install the latest app. |
| Web page: opens on the laptop (`localhost:5174`) but times out on the tablet or a phone | Port 5174 isn't forwarded into WSL. The laptop reaches WSL through `localhost`; other devices reach only the ports Windows forwards. Run `run_windows.cmd` again (step 2). To check, run `bash run_demo/run_wsl.sh check` (the `forward` line), or `netsh interface portproxy show v4tov4` in PowerShell: it must list 8100 and 5174, both to the address that `wsl hostname -I` prints. |
| Web page: opens on the tablet, but can't connect to the backend | **Settings** has `http://localhost:8100`, which on the tablet means the tablet. Enter the **Tablet app backend URL** from step 2. |
| Tablet: no videos under **Pre-recorded video** | Put video files (`.mov`, `.mp4`, `.mkv`, …) directly in `[server] video_dir` (`~/htx-demo`); subfolders aren't listed. |
| Tablet: **Qwen assistance unavailable** | Detection goes on without Qwen. Qwen isn't running, or it serves another model than `[models] qwen_model`. Run `bash server/status.sh`, then `bash server/start_laptop.sh`. The tablet checks for Qwen when a session starts, so change source afterwards. |
| A `config.toml` change has no effect | Restart the backend. It reads the file only when it starts. |
| Mimo **can't find the camera** | Check that the phone's Wi-Fi and Bluetooth are both on and that Mimo is up to date. A DJI Mic 2 on 2.4 GHz blocks Mimo: switch the camera to 5.8 GHz under Wireless Connection, or disconnect the mic. |
| Mimo: **Livestream failed**, or nothing in the MediaMTX log | The RTMP address must be the **Wi-Fi IP printed in step 2** (it changes per venue), not the WSL address. The camera must be on the same Wi-Fi. A login page also blocks the camera: use the backup network. If the firewall rule for TCP 1935 is missing, run `run_windows.cmd` again (step 2). |
| Inspection or backend log: **`method DESCRIBE failed: 404 (Not Found)`**, then **No frames from rtsp://… after 30 s** | MediaMTX is reachable, but the camera isn't publishing to `live/x2`; its log shows no `is publishing to path` line. Usually Mimo still has an old address: the Wi-Fi IP changes with every network, including when you switch to a phone hotspot. Run `run_windows.cmd` again (step 2) and put the new **DJI Mimo RTMP address** into Mimo. The stream reader retries for 30 s, so it picks up the stream if you fix it in time. |
| Inspection or backend log: **No frames from rtsp://… after 30 s**, without a 404 | MediaMTX isn't running. In WSL, use the step 3 address (`$WIN` in step 7), not the Wi-Fi IP. |
| Inspection or backend: **Cannot reach Qwen** | Run `bash server/status.sh`, then `bash server/start_laptop.sh`. |
| **Qwen doesn't come up on a network without internet** | The models are on the laptop, but Qwen may wait on Hugging Face. Start it with `bash server/stop_all.sh qwen` and then `HF_HUB_OFFLINE=1 bash server/start_laptop.sh`. This only works when no other tmux session is open: Qwen runs in tmux, and otherwise it won't see the setting. `run_wsl.sh` does this by itself when it can't reach huggingface.co. |
| Frequent "stream lost — reconnecting" | Weak Wi-Fi, the phone went to sleep, or the camera is hot or low on battery. Runs reconnect by themselves and end only after 30 s without frames. |

### Notes

- **The two IPs are easy to mix up.** Mimo and the tablet use the laptop's **Wi-Fi IP**
  (printed in step 2). `config.toml` and the commands in WSL use Windows as seen from
  inside WSL (printed in step 3, `$WIN` in step 7).
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
    [live-camera.md](../docs/live-camera.md)).

---

## The backend on a GPU server

The tablet doesn't care where the backend runs. On a Linux server with a 24 GB-class GPU,
one script sets up and starts Qwen, LocateAnything and the backend:

```bash
bash server/start_all.sh          # each in its own tmux session; prints the backend port and token
bash server/status.sh             # what is running
bash server/stop_all.sh [name]    # stop everything, or one of qwen / locate / backend
```

- The first run creates three Python environments and downloads the weights, which takes
  about 20–30 minutes. Later runs start in 1–2 minutes.
- It serves `[models] qwen_model` from `config.toml`, or `4b` when that is empty.
  `QWEN_MODEL=8b-fp8 bash server/start_all.sh` overrides it. Don't use it on the 8 GB
  laptop, which fits neither 4B nor LocateAnything.
- When the model servers are already running, `bash server/run_backend.sh` starts only the
  backend, in its own environment (`~/exit-monitor`). `python run.py backend` works too, in
  an environment you activated yourself.
- The backend takes port 8100, or the next free one, and prints it. On the tablet, enter
  `http://<server-ip>:<printed port>`.
- **External camera:** the backend reads the stream itself, so
  `[sources.external_camera] url` must be reachable *from the server*. A MediaMTX on the
  laptop usually isn't.
- For model servers on another machine, set their addresses (`[models] qwen_url` /
  `locate_url`, or `.env`) and start them there.

---

## Settings: `config.toml`

The backend and the inspection read the root `config.toml` when they start, so restart the
backend after a change. The [README](../README.md#settings-configtoml) explains the
inspection's settings (frame rates, thresholds, output). For the tablet demo, these matter:

| Setting | What to enter |
|---|---|
| `[sources.external_camera] url` | The camera stream as the **backend** reaches it, e.g. `rtsp://<windows-host>:8554/live/x2` on the laptop ([step 3](#3-start-qwen-and-the-backend-wsl-terminal-1)). Empty: the tablet reports **External camera is not configured**. The tablet never sees this URL. The inspection takes it as source `external-camera` (`python run.py inspect external-camera`). |
| `[models] qwen_url` | `""` (default) uses `QWEN_URL` from `.env`, which is `http://localhost:8000/v1` on the laptop. |
| `[models] qwen_model` | The model the Qwen server serves, e.g. `2b`; `""` accepts whichever it serves. If they differ, the tablet runs without Qwen. Changing it doesn't switch the server: run `bash server/start_laptop.sh` again, which serves the model named here. |
| `[models] yoloe_model` | `11s` (default). Restart the backend after changing it. |
| `[models] locate_anything` | The tablet always asks for it off; it matters for the web app and the inspection. With no URL, it is off anyway. |
| `[processing] process_fps` | Frames per second the backend analyses in each tablet session (`0` = as many as it keeps up with). The inspection uses it too. |
| `[server] port`, `host` | `8100`, on all interfaces. If 8100 is busy, the backend takes the next free port. |
| `[server] video_dir` | The folder listed under **Pre-recorded video**; `.` is the project root. Subfolders aren't listed. |
| `[server] max_sessions` | Sessions at the same time, live ones included: `1` (one GPU pipeline). |
| `[server] record`, `record_fps` | `true` (default) records every camera / stream session ([Recordings](#recordings)). `record_fps` is the videos' frame rate (15). |

`localhost` in these settings means the backend's own computer. Edit the sections that are
already in the file; a second copy of a section makes the file invalid.

### A settings file per machine

To keep settings for several machines, copy the whole `config.example.toml` to
`configs/<machine>.local.toml` (files ending in `.local.toml` are git-ignored), and start
with it:

```bash
python run.py backend --config configs/gpu.local.toml
bash server/run_backend.sh --config configs/gpu.local.toml
python run.py inspect --config configs/gpu.local.toml
export HTX_CONFIG=$PWD/configs/gpu.local.toml      # the same, for everything started from this shell
```

- The file **replaces** `config.toml`: settings it leaves out get the built-in defaults,
  not the values in `config.toml`.
- `--config` wins over `HTX_CONFIG`. `server/start_laptop.sh` and `server/start_all.sh`
  read `HTX_CONFIG` for the Qwen model; a model argument or `QWEN_MODEL` wins over it.
- `--host` and `--port` override `[server]`; `server/run_backend.sh` also takes `PORT`.

### Environment variables

Secrets stay out of `config.toml`: put them in `.env` (git-ignored) or in the environment.
The backend also reads these; the ones that name a setting override it.

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND_TOKEN` | *(none)* | shared secret: `Authorization: Bearer <token>` or `?token=`. **Set it.** |
| `BACKEND_CORS_ORIGINS` | `*` | origins allowed to call the API, e.g. `http://localhost:5173` |
| `BACKEND_VIDEO_DIR` | `[server] video_dir` | where session videos are read from |
| `BACKEND_MAX_SESSIONS` | `[server] max_sessions` | sessions at the same time, live ones included |
| `BACKEND_OUT_DIR` | `[output] out_dir` (`results`) | reports, snapshots and recordings |
| `QWEN_URL`, `LOCATE_URL` | the model servers' port files | model servers, when `[models] qwen_url` / `locate_url` are empty |

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
3. **`.env` and the backend token:** copy `.env.sample` to `.env` if there is none, then run
   `echo "BACKEND_TOKEN=$(python -c 'import secrets; print(secrets.token_hex(16))')" >> .env`.
   The tablet app needs the token.
4. **Port forward and firewall rules:** `run_windows.cmd`, or the block in [step 2](#2-open-the-laptop-to-the-network-windows-administrator-powershell)
   creates them.
5. **Models:** see [Before you leave](#before-you-leave).

### Tablet app (Android)

1. **Build and install the app.** Building needs JDK 17 and the Android SDK; the steps are
   in the app's [developer setup](../android-app/README.md#developer-setup). With a built
   APK and `adb`:

   ```bash
   adb install -r android-app/app/build/outputs/apk/debug/app-debug.apk
   ```

   Install it again after every app update. A backend update only needs a backend restart.
2. **Enter the backend** in the app's **Settings** at the venue
   ([step 6](#6-connect-the-tablet)). The app keeps the URL and the token (encrypted)
   between launches.

For development, the tablet can also reach the backend over USB: run
`adb reverse tcp:8100 tcp:8100` and use `http://127.0.0.1:8100`. The Android emulator
reaches the computer at `http://10.0.2.2:8100`.

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

The backend (`python run.py backend`, the same as `python -m backend`) is a FastAPI service,
API only. Its clients are the tablet app, the [tablet web page](#tablet-web-page-web-tablet)
and the web app in `frontend/`, which this section describes. The web app runs one task at
a time, exits or extinguishers, on a video or a camera in the browser. To run the backend
on a server, see [The backend on a GPU server](#the-backend-on-a-gpu-server); for its
settings, see [Settings](#settings-configtoml).

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

#### Recordings

The backend saves every live session (the tablet camera, and whatever the web app sends),
and every server session from a stream URL or the external camera, as two videos in
`results/recordings/`:

- `<session>_original.mkv`: the frames the backend analysed;
- `<session>_annotated.mkv`: the same frames with the boxes and statuses. The bottom-left
  corner shows when each frame arrived: the clock time (`2026-11-05 14:03:12.345`) and
  `t=`, the session time that `report.json` and the events use. The two videos have the
  same frames at the same positions, so the stamp also dates the original.

Server sessions that play a video file aren't recorded. The videos play in real time at
`[server] record_fps`; `[server] record = false` turns recording off. They play in VLC. To
replay one through the pipeline, run
`python run.py inspect results/recordings/<session>_original.mkv`. Nothing deletes old
recordings.

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

**Server-side sessions.** The tablet uses these for **Pre-recorded video** and **External
camera**. The server reads a video file or camera stream and sends annotated MJPEG frames
back (`/api/sessions…` in the [API reference](../docs/android-api.md#rest-endpoints)). They are
useful for recording and reports, but they lag over a slow link.

**Building your own app** (Android, another web app, a script): the full API, with
request and response examples, is in [Backend API reference](../docs/android-api.md).

`python run.py exits --stream` still works for quick checks: an all-in-one browser view
(`/stream`), plus `/stream/original`, `/stream/annotated` and `/state`.

### Tablet web page (`web-tablet/`)

The tablet app's screen in a browser, for **Pre-recorded video** and **External camera**.
It is static files, with no build step:

```bash
python web-tablet/serve.py [--port 5174]   # open http://localhost:5174, or http://<this computer's IP>:5174
```

In **Settings**, enter the backend's URL (the backend's own port, not 5174) and the token.
The page talks to the backend directly, so the browser must reach both. When `serve.py`
and the backend run in WSL, other devices reach them only through a Windows port forward
for each port; the [step 2](#2-open-the-laptop-to-the-network-windows-administrator-powershell)
block sets up both. Picking the source
that a running session already shows joins that session as a viewer. At a venue, see
[If the tablet app fails](#if-the-tablet-app-fails-the-tablet-web-page). Details:
[web-tablet/README.md](../web-tablet/README.md).

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
and troubleshooting are in **[docs/live-camera.md](../docs/live-camera.md)**.

### Tools

```bash
python run.py camera-check rtsp://<host>:8554/live/x2 --clock   # camera link check, no models
python run.py bench IMG_0450.MOV --provider local --sizes 448 640 --concurrency 1 4 8
python run.py locate-test IMG_0450.MOV --url http://<srv>:8020 --mode fast
bash server/run_realtime_test.sh IMG_0450.MOV   # on the server: benchmark + live run + verdict
bash server/watch_live.sh IMG_0450.MOV          # on the server: live_infer → browser
```

### Tests

None of these needs a GPU, a model server or a camera:

```bash
python tests/test_core.py                               # core logic; expect 17/17 passed
python -m unittest discover -s tests -p 'test_*.py'     # backend: sessions, live mode, Qwen fallback, recordings
node --test web-tablet/tests/*.test.mjs                 # tablet web page (Node.js 18+)
android-app/gradlew -p android-app testDebugUnitTest assembleDebug assembleDebugAndroidTest lintDebug   # tablet app (JDK 17, Android SDK)
```

They don't cover the camera relay, GPU inference, latency or what the tablet shows: check
those with the demo equipment. The app's on-device camera test is in
[android-app/README.md](../android-app/README.md#validation--selectable-sources).
