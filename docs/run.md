# Running the demo at a venue

This is the checklist for running the demo on the dev laptop, at a venue with its own
Wi-Fi. It assumes the laptop is already set up: WSL with the `htx` conda env, MediaMTX in
`C:\mediamtx`, the models downloaded, and `BACKEND_TOKEN` in `.env`. Camera and DJI Mimo
details are in [run-2.md](run-2.md). The full camera guide is
[live-camera.md](live-camera.md).

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

---

## Before you leave

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

---

## At the venue

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

Follow the "Each demo" steps in [run-2.md](run-2.md). In short:

1. Join the **phone** to the venue Wi-Fi.
2. In Mimo, go to **LIVESTREAM → RTMP**. Under **Camera Network Status**, connect the
   **camera** to the same Wi-Fi.
3. Set the address to the **DJI Mimo RTMP address** from step 2, for example
   `rtmp://192.168.50.22:1935/live/x2`. There is no stream key.
4. Tap **Start Livestream**. The MediaMTX log should show
   `is publishing to path 'live/x2'`.
5. Keep Mimo open with the phone screen on for the whole demo.

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
  README, *Running*.
- **Fallback without the camera:** `python run.py inspect <video file> --loop`.
- The inspection and one tablet session can run at the same time. Tested on a recorded
  video: the tablet's round trip stayed at about 30 ms, and the inspection ran at about
  40 ms per frame.

### Stopping

1. Press `q` in the inspection window.
2. Press `Ctrl+C` in the backend terminal.
3. Stop the livestream in Mimo, close MediaMTX, and turn off the camera.
4. Free the GPU: `bash server/stop_all.sh qwen`.

The port forward and firewall rules can stay; they only let in devices on the same
network, and only on the demo ports.

---

## If something goes wrong

| Symptom | Cause and fix |
|---|---|
| Tablet: **connection timed out** | (1) The laptop was rebooted, or WSL restarted, since step 2: run the step 2 block again. (2) The tablet is on another network: its IP must start like the laptop's (e.g. `192.168.50.x`). (3) The Wi-Fi blocks devices from reaching each other: switch to your backup network. |
| Tablet: **connection refused** | The backend isn't running, or it runs on another port than 8100 (step 3). |
| Tablet: **401** or WebSocket closed at once | Wrong token. Check it with `grep BACKEND_TOKEN ~/htx-demo/.env`. |
| Tablet: **already running … stop it first** | The backend runs one tablet session at a time. Close the session in the app, or restart the backend. |
| Mimo: **Livestream failed**, or nothing in the MediaMTX log | The RTMP address must be the **Wi-Fi IP printed in step 2** (it changes per venue), not the WSL address. The camera must be on the same Wi-Fi. A login page also blocks the camera: use the backup network. |
| Inspection: **No frames from rtsp://… after 30 s** | MediaMTX isn't running, or the camera isn't publishing (check its log). In WSL, use `$WIN` (step 7), not the Wi-Fi IP. |
| Inspection or backend: **Cannot reach Qwen** | Run `bash server/status.sh`, then `bash server/start_laptop.sh`. |
| **Qwen doesn't come up on a network without internet** | The models are on the laptop, but Qwen may wait on Hugging Face. Start it with `bash server/stop_all.sh qwen` and then `HF_HUB_OFFLINE=1 bash server/start_laptop.sh`. This only works when no other tmux session is open: Qwen runs in tmux, and otherwise it won't see the setting. |
| Frequent "stream lost — reconnecting" | Weak Wi-Fi, the phone went to sleep, or the camera is hot or low on battery. Runs reconnect by themselves and end only after 30 s without frames. |

---

## Notes

- **The two IPs are easy to mix up.** Mimo and the tablet use the laptop's **Wi-Fi IP**
  (printed in step 2). Commands in WSL use **`$WIN`**, Windows as seen from inside WSL.
- **Only YOLOE and Qwen run here.** LocateAnything doesn't fit in the laptop's 8 GB GPU,
  so the demo runs YOLOE + Qwen3-VL-2B.
- **Watch the verdicts.** Qwen-2B is the weakest Qwen model. On a drawn test scene, its
  verdicts flipped between "partly blocked" and "clear", so check them on real footage.
- **The first picture can take a few seconds to appear.**
