# Venue demo: the short version

The demo on the dev laptop, with the Pocket 3 and the tablet, on one page. For every
step in full, the one-time setup and all the fixes, see
[running.md](running.md#the-demo-at-a-venue).

| Script | Run it in | What it does |
|---|---|---|
| `run_windows.cmd` | **Windows**: double-click it in Explorer at `\\wsl.localhost\Ubuntu-24.04\home\peter\htx-demo\run_demo` | forwards ports 8100 + 5174 into WSL, opens the firewall, starts MediaMTX, prints the addresses for Mimo and the tablet |
| `run_wsl.sh` | **WSL**: `bash ~/htx-demo/run_demo/run_wsl.sh` | starts Qwen3-VL-2B, the backend (YOLOE) and both web pages in the background, sets the camera URL, checks everything, prints the addresses and the token |

LocateAnything isn't started: it doesn't fit next to Qwen in the laptop's 8 GB GPU.

## Before you leave

- [ ] Charge the camera, the phone with DJI Mimo, and the tablet. Bring the laptop charger.
- [ ] The latest tablet app is installed.
- [ ] Run `bash ~/htx-demo/run_demo/run_wsl.sh` once with internet (it downloads what is
      missing), then `bash ~/htx-demo/run_demo/run_wsl.sh stop`.
- [ ] One or two videos in `~/htx-demo`: the tablet's **Pre-recorded video** fallback.
- [ ] A backup network: a travel router, or a hotspot on a second phone.

## At the venue

1. **Wi-Fi.** Put the laptop, phone, camera and tablet on one network that has no login
   page. If that fails, use the backup network.
2. **Windows.** Double-click `run_windows.cmd` and allow administrator rights. Keep the
   MediaMTX window open. Run it again after every reboot (WSL's address changes) and
   after switching networks (it prints the new addresses).
3. **WSL.** Run `bash ~/htx-demo/run_demo/run_wsl.sh`; it takes about 1 minute. Every line
   should show ✓, except `camera`, which turns ✓ once the camera streams.
   - Another Qwen model: `GPU_UTIL=0.86 MAX_LEN=2048 bash ~/htx-demo/run_demo/run_wsl.sh 4b-fp8`
4. **Camera, in Mimo.**
   1. Choose **LIVESTREAM** → **RTMP** → **Start Livestreaming Settings**.
   2. Connect the camera to the venue Wi-Fi.
   3. Enter the printed **DJI Mimo RTMP address**. There is no stream key; keep the path
      `live/x2`.
   4. Tap **Start Livestream**.

   The MediaMTX window then shows `is publishing to path 'live/x2'`, and
   `bash ~/htx-demo/run_demo/run_wsl.sh check` shows the `camera` line as streaming.
5. **Tablet.**
   1. In its browser, open the printed **Tablet browser check** URL. It must show
      `{"ok":true,...}`.
   2. In the app, open **Settings**, enter the **Tablet app backend URL** and the token,
      and tap **Save**.
   3. Tap **Start demo**, then **External camera**.

**Fallbacks:** **Pre-recorded video** or **Tablet camera** in the app. If the app fails,
use the tablet web page at the printed **Tablet web page** URL, with the same backend URL
and token in its **Settings**.

## Stop

1. Run `bash ~/htx-demo/run_demo/run_wsl.sh stop`. This also finishes the recordings in
   `results/recordings/`.
2. Stop the livestream in Mimo, close MediaMTX, and turn off the camera.

## If something goes wrong

Start with `bash ~/htx-demo/run_demo/run_wsl.sh check`: each ✗ line says what to do.

| Symptom | Fix |
|---|---|
| Tablet: **connection timed out** | Run `run_windows.cmd` again. Check that the tablet is on the same Wi-Fi. If the venue Wi-Fi blocks devices from reaching each other, use the backup network. |
| Tablet: **401** | Wrong token. `check` prints the right one. |
| `camera ✗ … not reachable` | MediaMTX isn't running: run `run_windows.cmd`. |
| `camera ✗ nothing streams yet`, or Mimo says **Livestream failed** | Mimo needs the Wi-Fi IP printed *now*. It changes with every network, including a hotspot. The camera must be on the same Wi-Fi. |
| **External camera** shows no picture | The session ends after 30 s without frames. Fix the stream, then pick the source again. |
| **Qwen assistance unavailable** | Run `run_wsl.sh` again, then change the source on the tablet. Detection still works without Qwen. |
| A `config.toml` change has no effect | Run `run_wsl.sh` again: it restarts the backend. |
| Anything else in the backend | `tmux attach -t backend` shows its output (`Ctrl+B`, then `D`, to leave), or see `logs/backend.log`. |
