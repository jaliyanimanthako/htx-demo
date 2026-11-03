# DJI Osmo Pocket 3 → Exit Monitor: live input

How to get the Pocket 3's live picture into the detection pipeline on the WSL2 dev
machine, how the code handles a live camera, and what to do when it doesn't work.

Camera basics (DJI Mimo, webcam mode, firmware) are in the separate setup guide,
[DJI Pocket 3 to Python: Setup Guide](https://claude.ai/code/artifact/96e2ddae-4f7f-4e8b-b638-8c828ada9691).
This guide doesn't repeat them.

**Status (2026-11-01):** the code is done and covered by offline tests. Nothing has
been run with the real camera yet, so latency figures below are typical values, not
measurements.

## Contents

1. [What you get](#1-what-you-get)
2. [Choose a path](#2-choose-a-path)
3. [Wi-Fi RTMP on this WSL2 machine (main path)](#3-wi-fi-rtmp-on-this-wsl2-machine-main-path)
4. [USB webcam](#4-usb-webcam)
5. [How live input works in the code](#5-how-live-input-works-in-the-code)
6. [Latency](#6-latency)
7. [Troubleshooting](#7-troubleshooting)
8. [Reference](#8-reference)
9. [Appendix: Insta360 ONE X2 (paused)](#appendix-insta360-one-x2-paused)

## 1. What you get

```
DJI Osmo Pocket 3
  │ Wi-Fi RTMP (set up in DJI Mimo)                │ USB-C, Webcam mode
  ▼                                                ▼
MediaMTX on Windows                              /dev/video0 in WSL (usbipd-win)
  RTMP in :1935 → RTSP out :8554                 or the web app in a Windows browser
  │ rtsp://<windows-host>:8554/live/x2             │ 0
  ▼                                                ▼
core/video_io.py: LiveSource  ── background thread: newest frame only, reconnects
  ▼
python run.py <command> → apps/: fire_safety_inspection · exit_monitor · extinguisher_monitor · …
```

Every runner reads its input through `LiveSource`. The camera is just the `source`
argument: a stream URL, or a webcam index such as `0`. No runner has camera-specific code.
All of them start from `run.py` in the project root (the code is in `apps/`).

| Command (`python run.py …`) | Use it for | Needs |
|---|---|---|
| `camera-check` | Is the picture arriving? Shows resolution, fps, drops, reconnects and latency | nothing (no models) |
| `yoloe` | First detection test, local GPU only | YOLOE weights (see README *Setup*) |
| `exits` | **The main pipeline:** exits and what blocks them | YOLOE, plus LocateAnything and Qwen from `.env`. On the 8 GB dev laptop, it runs YOLOE + Qwen3-VL-2B only (see 3.2, step 4). |
| `extinguishers` | Extinguisher expiry and service dates | YOLOE + Qwen |
| `inspect` | Both of the above on the same frames; frame rates, resolution and the rest in `config.toml` | YOLOE + Qwen |
| `live` | Qwen-only live detection: simpler, but results are slower | a Qwen endpoint (`--provider`) |
| `locate-live` | LocateAnything-only live detection | LocateAnything server |

## 2. Choose a path

|  | **Wi-Fi RTMP** (main path) | **USB webcam into WSL** | **Web app camera button** |
|---|---|---|---|
| Route | camera → Wi-Fi → MediaMTX on Windows → WSL | USB-C → usbipd-win → WSL | USB-C → Windows browser → backend |
| Added latency | typically 1–3 s for RTMP (not yet measured on the Pocket 3) | lowest | lowest, plus about 50–70 ms network round trip |
| Camera position | anywhere on the same Wi-Fi | on a cable to the laptop | on a cable to the laptop |
| Setup on this machine | MediaMTX + one firewall rule | usbipd-win, attach after each replug | none |
| Resolution | up to about 1080p30 (community figure; DJI doesn't document it) | 1080p, or 4K with firmware v01.04.08.02+ | the browser asks for about 1280 px wide; 960 px JPEGs are sent |
| Which code | any runner in WSL | any runner in WSL | backend live mode only |

Start with **Wi-Fi RTMP**. It works with WSL2 today, and the camera stays free to move,
which matters because the demo is handheld. If the measured latency
([section 6](#6-latency)) is too high, switch to a USB route; the code doesn't change.

## 3. Wi-Fi RTMP on this WSL2 machine (main path)

WSL2 runs in **NAT** mode here (`wslinfo --networking-mode` prints `nat`). Devices on
the LAN can't reach a server inside WSL, so MediaMTX runs on **Windows**, and WSL reads
the stream from Windows.

```
Pocket 3 ──Wi-Fi──► Windows: mediamtx.exe ──RTSP over TCP──► WSL: python … rtsp://<windows-host>:8554/live/x2
   ▲                  :1935 in, :8554 out
   └─ DJI Mimo on the phone only sets up the stream; the video does not pass through the phone
```

### 3.1 One-time setup (Windows)

1. **Install MediaMTX.** Download `mediamtx_v…_windows_amd64.zip` from
   <https://github.com/bluenviron/mediamtx/releases> and unzip it, for example to
   `C:\mediamtx`. The folder contains `mediamtx.exe` and its config `mediamtx.yml`.
2. **Open the firewall.** In an **Administrator** PowerShell:

   ```powershell
   New-NetFirewallRule -DisplayName "MediaMTX" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1935,8554
   ```

   Port 1935 is the camera pushing RTMP over Wi-Fi. Port 8554 is WSL reading RTSP;
   Windows Firewall treats WSL's virtual network adapter as a separate network. Only
   TCP is needed, because the code asks for RTSP over TCP. To remove the rule later, run
   `Remove-NetFirewallRule -DisplayName "MediaMTX"`.
3. **Note the Windows Wi-Fi IP**, the address the camera pushes to. Run `ipconfig` and
   look under *Wireless LAN adapter Wi-Fi* for the *IPv4 Address*, for example
   `192.168.1.23`. If it changes, you must re-enter it in Mimo. A DHCP reservation in
   the router keeps it fixed.
4. **Use one Wi-Fi network.** The camera, the phone and the Windows laptop must all be
   on the same network. Guest networks and "client isolation" block traffic between
   devices.

### 3.2 Each session

**1. Start MediaMTX on Windows.** Double-click `C:\mediamtx\mediamtx.exe`, or run it
from a terminal *in that folder*, so it finds `mediamtx.yml`. The first log lines should
say `configuration loaded from …mediamtx.yml`, followed by the RTMP listener on `:1935`
and the RTSP listener on `:8554`.

**2. Start the livestream in DJI Mimo.**
- Connect Mimo to the camera, then open **LIVESTREAM → RTMP → Start Livestreaming Settings**.
- Under **Camera Network Status**, put the camera on the same Wi-Fi as the laptop.
- Set the push address to `rtmp://<Windows Wi-Fi IP>:1935/live/x2`. There is no stream key.
- Start streaming. The MediaMTX log should now show `is publishing to path 'live/x2'`.
- Keep Mimo open with the phone screen on. On Android, set Mimo's battery usage to
  *Unrestricted*. On Samsung, turn off *Intelligent Wi-Fi → Switch to mobile data*.

**3. Check the picture in WSL** (no models involved):

```bash
conda activate htx
cd ~/htx-demo
WIN=$(ip route show default | awk '{print $3}')   # Windows, as seen from WSL
python run.py camera-check rtsp://$WIN:8554/live/x2
```

You should see `Source: rtsp://… | 1920x1080 @ 30.0 fps (reported) | live device`, and
a window opens through WSLg. The first picture can take a few seconds, because decoding
starts at the next keyframe. The caption shows the resolution, the frame rate at
the source, dropped frames and reconnects. Press `q` to quit. Use the gateway IP
(`$WIN`) here, not the Windows Wi-Fi IP, and re-run the `WIN=` line after a reboot,
because it can change.

**4. Run detection.** On this laptop, start Qwen first. It is the only model server
that fits in the 8 GB GPU (Qwen3-VL-2B, no LocateAnything), and it must start before
YOLOE takes its share of GPU memory:

```bash
bash server/start_laptop.sh        # about 1 min; "Qwen is up … QWEN_URL=http://localhost:8000/v1"
```

`apps/exit_monitor.py` should then print `LocateAnything: OFF | Qwen: http://localhost:8000/v1`
when it starts. Qwen keeps running in tmux session `qwen` until you run
`bash server/stop_all.sh qwen` or shut WSL down.

Pass the same URL to any runner:

```bash
python run.py exits rtsp://$WIN:8554/live/x2                          # main pipeline
python run.py exits rtsp://$WIN:8554/live/x2 --save results/pocket3.mp4
python run.py exits rtsp://$WIN:8554/live/x2 --no-display --stream    # view at http://localhost:8090
python run.py extinguishers rtsp://$WIN:8554/live/x2
python run.py yoloe rtsp://$WIN:8554/live/x2                           # YOLOE only, no servers
python run.py live rtsp://$WIN:8554/live/x2 --provider local --base-url http://<srv>:8010/v1
```

How these runners behave with a live source:
- Output goes to `results/exit_monitor_live/` (`report.json`, snapshots). The next live
  run overwrites it, so use `--out-dir` to keep runs apart.
- Times in the log and report are **seconds since the first frame** (wall-clock time).
- `--loop` has no effect on a live source.
- `space` pauses the display, but the camera keeps streaming. When you resume, the
  runner jumps to the newest frame.
- If the stream drops, the runner keeps going: it prints `stream lost — reconnecting…`
  and then `stream back after X s`. After **30 s** with no frames it ends normally and
  writes its report.

**5. Stop:** press `q` in the runner, stop the livestream in Mimo, and close MediaMTX.
To free the GPU, also run `bash server/stop_all.sh qwen`.

### 3.3 Alternative: mirrored networking

In mirrored mode, WSL shares Windows' network interfaces, so MediaMTX can run inside
WSL and be read at `rtsp://localhost:8554/live/x2`. To switch:
1. Set `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`.
2. Run `wsl --shutdown`.
3. Inbound traffic from the LAN may also need a Hyper-V firewall rule, for example
   `New-NetFirewallHyperVRule -Name MediaMTX -DisplayName MediaMTX -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 1935`.
   See Microsoft's *WSL networking* docs.

This changes networking for all of WSL. The Windows MediaMTX setup above is less invasive.

## 4. USB webcam

On the Pocket 3, connect the **USB-C cable from the box** (a charge-only cable carries
no video) and tap **Webcam** on the camera screen. To choose 25 or 30 fps, swipe up in
Webcam mode.

### 4.1 Web app: no WSL work

The web app's **Camera** button sends browser camera frames to the backend over
`WS /api/live`. This runs the same detection code (see README *Backend + web app*).

1. Start the backend on the GPU server with `bash server/start_all.sh`, and note the port and token.
2. In WSL, run `bash frontend/serve.sh`. Then open `http://localhost:5173` in Chrome or
   Edge **on Windows**; WSL forwards `localhost`.
3. Enter the backend URL and token, click **Connect**, pick a mode, then click **Camera**.
   The browser uses its default camera. To switch to the Pocket 3, click the camera
   icon in the address bar (or open the site's camera settings), choose it, and click
   **Camera** again.

### 4.2 Into WSL with usbipd-win

This WSL kernel ships the UVC webcam driver as a module (`uvcvideo`) and includes USB/IP
support. It hasn't been tried with the Pocket 3 yet.

Admin PowerShell on Windows:

```powershell
winget install usbipd
usbipd list                        # find the Pocket 3's BUSID, e.g. 2-3
usbipd bind --busid 2-3            # once
usbipd attach --wsl --busid 2-3    # after every replug or reboot
```

In WSL:

```bash
sudo modprobe uvcvideo             # if /dev/video* doesn't appear by itself
ls -l /dev/video*                  # expect /dev/video0 (often a second node for metadata)
python run.py camera-check 0           # should report 1920x1080
python run.py exits 0
```

Things to know about this route:
- While the camera is attached, Windows can't use it. Run `usbipd detach --busid 2-3` to give it back.
- If opening fails with a permission error, add yourself to the `video` group with
  `sudo usermod -aG video $USER`, then reopen the WSL shell.
- `LiveSource` asks a webcam for **1920×1080 MJPG** (`CAMERA_SIZE` in
  `core/video_io.py`). Without that request, OpenCV often opens at 640×480. For 4K,
  change `CAMERA_SIZE` to `(3840, 2160)`.

## 5. How live input works in the code

All of this lives in `core/video_io.py:LiveSource`, which keeps the contract every runner
uses: `read() → (frame_idx, frame, n_skipped)`, or `(None, None, 0)` at the end of the
stream.

| Source | How it's read |
|---|---|
| Video file | Unchanged. Paced to real time; frames are skipped when processing falls behind, so a file behaves like a camera. |
| Webcam index (`0`) or URL (`rtsp://`, `rtmp://`, `http(s)://`, …) | A background thread reads continuously and keeps only the newest frame. |

**Why newest-frame-only matters.** `apps/exit_monitor.py` takes about 70 ms per frame, while
the camera sends a frame every 33 ms (30 fps). Reading frames one by one, as the old code
did, means about 16 frames per second pile up inside FFmpeg. The picture then falls about
0.5 s further behind *every second*: half a minute behind after one minute. Taking the
newest frame keeps the delay at one frame plus processing time, however long the run.

**What happens, step by step:**
1. **Open.** The constructor starts the reader thread and waits up to `STREAM_TIMEOUT`
   (30 s) for the first frame. You can start the runner before the livestream. Width
   and height come from the first frame. FPS comes from the stream if it is plausible
   (1–120); otherwise it is 30, because RTSP sometimes reports values like 90000.
   Without a frame in time, the constructor raises:
   `RuntimeError: No frames from <source> after 30 s — is the camera streaming?`
2. **Read.** The thread calls `cap.read()` in a loop and stores each frame with its
   arrival time. `read()` returns the newest frame not yet returned; if there isn't one,
   it waits. `n_skipped` counts frames that arrived in between and adds to
   `src.dropped`. Runners that save video (`--save`) write the frame `n_skipped + 1`
   times, so the recording keeps real-time length.
3. **Reconnect.** On a failed read, the thread releases the capture, prints
   `stream lost — reconnecting (<source>)...` and retries every `RECONNECT_DELAY` (2 s).
   When frames return, it prints `stream back after X s`. After `STREAM_TIMEOUT` with no
   frames, it prints `no frames for 30s — giving up`, and `read()` reports the end of
   the stream.
4. **Timeouts.** FFmpeg's open and read calls time out after `STREAM_IO_MS` (5 s), so a
   dead network connection can't hang the thread.
5. **Time.** For live sources, `video_time()` is the arrival time of the returned
   frame, in wall-clock seconds since the first frame. It stays correct across dropped
   frames and reconnect gaps.
6. **Stop.** `LiveSource(..., halt=event)` makes `read()` return end-of-stream as soon
   as the event is set, even while it is waiting for a frame. `apps/exit_monitor.py` passes
   its stop event, so stopping a backend session takes effect immediately, even when the
   stream has stalled. Command-line runners stop with `q` or Ctrl+C.

**FFmpeg options.** Before opening a URL, `LiveSource` sets
`OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp|fflags;nobuffer|flags;low_delay`,
unless that variable is already set:
- `nobuffer` and `low_delay` reduce input buffering.
- `rtsp_transport;tcp` keeps RTSP on TCP. When this variable is unset, OpenCV prefers TCP
  by itself; setting the variable drops that default, so TCP has to be asked for
  explicitly. UDP through WSL's NAT tends to lose packets, which shows up as smeared or
  green frames.

OpenCV reads the variable when a capture is **opened**, not at `import cv2`. To try
other options, set the variable before starting. It replaces the defaults completely,
so keep `rtsp_transport;tcp` in it:

```bash
OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp|fflags;nobuffer" python run.py camera-check rtsp://$WIN:8554/live/x2
```

You can also read `rtmp://$WIN:1935/live/x2` directly from MediaMTX. RTSP is
recommended because it usually has lower latency in OpenCV.

## 6. Latency

Where the delay comes from on the Wi-Fi path:

| Stage | Typical | Notes |
|---|---|---|
| Camera encoding, then RTMP push over Wi-Fi | most of the 1–3 s | depends on the camera and the Wi-Fi signal |
| MediaMTX relay (RTMP → RTSP) | small | it repackages the stream; it doesn't re-encode |
| Decode start | a few s, **once** | the first picture needs a keyframe, at start and after each reconnect |
| `LiveSource` + processing | 1 frame + about 70 ms | bounded by newest-frame-only |
| LocateAnything / Qwen results | 1.5–2 s and on events | separate from camera latency: arrive late and are motion-compensated onto the current frame |

**Measure it (glass-to-glass):**
1. Run `python run.py camera-check rtsp://$WIN:8554/live/x2 --clock`. A large clock appears
   in the top-left of the window.
2. Maximise the window and point the camera at the screen. The window now shows the
   filmed clock as well as the drawn one.
3. The filmed clock lags the drawn clock by the end-to-end latency. Take a few readings
   (screenshots help) and note the median.
4. For the full pipeline, compare the left (original) half of the `apps/exit_monitor.py`
   window with a phone stopwatch in view. That adds processing time on top.

If the latency is too high:
- Move closer to the router, and prefer a 5 GHz network.
- Lower the livestream resolution or bitrate in Mimo, if it offers those settings.
- Read the RTSP URL, not RTMP.
- Switch to a USB route ([section 4](#4-usb-webcam)).

For the record: the latency question is still open in CLAUDE.md. Under 500 ms likely
rules out Wi-Fi RTMP.

## 7. Troubleshooting

Two quick checks from WSL:

```bash
WIN=$(ip route show default | awk '{print $3}'); echo $WIN
timeout 3 bash -c "</dev/tcp/$WIN/8554" && echo "8554 reachable" || echo "8554 NOT reachable"
```

| Symptom | Cause / fix |
|---|---|
| MediaMTX logs `path 'live/x2' is not configured` | MediaMTX started without its config. On Windows, run `mediamtx.exe` from its own folder. On macOS, pass the config path (see the setup guide). |
| Nothing appears in the MediaMTX log when you start the livestream | The camera can't reach Windows. Check: the IP in Mimo (the Windows **Wi-Fi** IP), the same non-guest Wi-Fi, and the firewall rule for TCP 1935. |
| `No frames from rtsp://… after 30 s` | Nothing is publishing yet: start the livestream within 30 s of the runner. Or WSL can't reach port 8554: run the reachability check above, check the firewall rule, and use the **gateway** IP from WSL, not the Wi-Fi IP. |
| `Connection to tcp://…:8554 failed: Connection refused` | MediaMTX isn't running, or the firewall rejects the connection. |
| `[ WARN … ] … can't be used to capture by name`, repeated | OpenCV prints this for each failed open attempt during reconnects. Harmless on its own; look at the cause above it. |
| The first picture takes several seconds | Decoding waits for a keyframe. Normal at start and after reconnects. |
| `stream lost — reconnecting` happens often | Usually weak Wi-Fi. Or the phone slept (keep Mimo open, battery *Unrestricted*), Samsung *Intelligent Wi-Fi* switched networks, or the camera is hot or low on battery. |
| Green, grey or smeared frames | Packet loss. RTSP over TCP is the default here; if you set `OPENCV_FFMPEG_CAPTURE_OPTIONS` yourself, include `rtsp_transport;tcp`. |
| Delay grows over time | If `python run.py camera-check --clock` also grows, the cause is on the camera or Wi-Fi side. If only a runner falls behind, check that it is using the updated `core/video_io.py`: its `dropped` count should rise when processing is slower than the camera. |
| Worked yesterday, not today | The Windows Wi-Fi IP changed (update Mimo), or the WSL gateway IP changed (re-run the `WIN=` line). |
| `python run.py camera-check 0` → `No frames from 0` | No `/dev/video0`: attach with usbipd, `sudo modprobe uvcvideo`, and check the `video` group. |
| The webcam opens below 1920×1080 | The camera rejected the request. List its modes with `v4l2-ctl --list-formats-ext` (package `v4l-utils`). |
| A backend session with the RTSP URL fails | The backend runs on the GPU server, which usually can't reach this laptop's port 8554. Run the runner in WSL instead, or use the web app route. |

## 8. Reference

**Settings** (module constants in `core/video_io.py`):

| Name | Value | Meaning |
|---|---|---|
| `STREAM_TIMEOUT` | 30.0 s | wait this long for the first frame, and give up after this long without frames |
| `RECONNECT_DELAY` | 2.0 s | pause between reconnect attempts |
| `STREAM_IO_MS` | 5000 | FFmpeg open and read timeout (ms) |
| `CAMERA_SIZE` | (1920, 1080) | resolution requested from a webcam |
| `FFMPEG_OPTIONS` | `rtsp_transport;tcp\|fflags;nobuffer\|flags;low_delay` | default for `OPENCV_FFMPEG_CAPTURE_OPTIONS` |

`LiveSource(source, loop=False, halt=None, timeout=STREAM_TIMEOUT)`. Attributes for live
sources: `width`, `height`, `fps`, `idx`, `dropped`, `reconnects`, `ended`.

**Files involved:**
- `core/video_io.py`: `LiveSource`, with the live mode described in section 5.
- `apps/camera_check.py`: link check, `--clock` latency test, `--no-display --seconds N` for headless checks.
- `apps/exit_monitor.py`: passes its stop event to `LiveSource`.
- `backend/sessions.py`: a session stopped while waiting for a stream shows as `stopped`, not `error`.
- `tests/test_core.py`: `test_live_source_stream_mode` and `test_live_source_no_stream`.
  They run the live code path offline by opening a synthetic video through a `file:`
  URL, which isn't a local path, so it takes the stream route. At the end of the file
  the reader sees a dropped stream and reconnects.

**Commands:**

```bash
python tests/test_core.py                                   # 17/17 passed
python run.py camera-check rtsp://$WIN:8554/live/x2 [--clock] [--no-display --seconds 30]
python run.py exits rtsp://$WIN:8554/live/x2
```

## Appendix: Insta360 ONE X2 (paused)

The project first used an Insta360 ONE X2. The prototype reader written for it
(`rtmp/rtmp_reader.py`, `LatestFrameReader`) is now part of `LiveSource`.

- **Streaming path.** The X2 can't push RTMP itself; the Insta360 phone app does
  (camera → phone → MediaMTX). The camera connects to the phone by USB cable, so the
  phone's Wi-Fi stays free to reach the PC.
- **Android only.** On the camera, choose *Switch USB Mode → Android* and use the
  original cable. On iPhone, the stream goes out over mobile data and can't reach a
  local server.
- **Failed test.** A Samsung S25 Ultra with a generic USB-C to USB-C cable gave no
  wired link: the phone stayed the follower, and *USB controlled by → This phone*
  failed with "couldn't switch". The next idea was a USB-A step in the chain (an OTG
  adapter or a hub), which forces the phone to be the host.
- **Limits.** Streaming tops out at about 1280p for the whole 360° frame, so small
  objects get few pixels. Equirectangular distortion hurts YOLO-style detectors, so
  frames would need reprojecting to cubemap faces or perspective crops first. The
  X2's webcam mode gives a split dual-lens view, not stitched 360°.

Sources:
- [Insta360 App: RTMP Streaming](https://onlinemanual.insta360.com/app/en-us/operation-tutorial/live/rtmp-publish-streaming-facebook-youtube)
- [How to live stream on YouTube with Insta360 ONE X2 (NotEnoughTech)](https://notenoughtech.com/featured/how-to-live-stream-on-youtube-with-insta360-one-x2/)
- [Insta360 forum: 360 livestream with OBS](https://forums.insta360.com/section/16/post/10339)
