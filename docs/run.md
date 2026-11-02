# One-time setup (Windows)

1. Download MediaMTX for Windows from its GitHub releases page and unzip it, for example to C:\mediamtx.
2. In an Administrator PowerShell, open the two ports:

  ```
  New-NetFirewallRule -DisplayName "MediaMTX" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1935,8554
  ```
3. Run ipconfig and note the Wi-Fi IPv4 address; the camera sends to this address.
4. Put the camera, the phone and the laptop on the same Wi-Fi. Guest networks won't work.

# Each demo

1. Windows: run C:\mediamtx\mediamtx.exe from its own folder. Its log should say "configuration loaded".
2. DJI Mimo: go to LIVESTREAM → RTMP and put the camera on the same Wi-Fi. Set the address to rtmp://<Windows Wi-Fi IP>:1935/live/x2 and start streaming. The MediaMTX log should show "is publishing to path 'live/x2'". Keep Mimo open with the phone screen on.
3. WSL:
    ```
    conda activate htx && cd ~/htx-demo
    bash server/start_laptop.sh                          # Qwen3-VL-2B; wait for "Qwen is up" (about 1 min)
    WIN=$(ip route show default | awk '{print $3}')
    python run.py camera-check rtsp://$WIN:8554/live/x2      # optional: confirm the picture arrives (q to quit)
    python run.py exits rtsp://$WIN:8554/live/x2      # the demo
    ```
    When it starts, apps/exit_monitor.py should print `LocateAnything: OFF | Qwen: http://localhost:8000/v1`. Add `--save results/demo.mp4` to record the run.
4. Stop: press q in the window, stop the livestream in Mimo, and close MediaMTX. To free the GPU, also run `bash server/stop_all.sh qwen`.

# Before you try it

- Already set up on this laptop:
  - YOLOE weights (in models/).
  - The Qwen3-VL-2B server (environment in ~/qwen-vllm). .env points QWEN_URL at http://localhost:8000/v1.
- LocateAnything doesn't fit in the laptop's 8 GB GPU, so the demo runs YOLOE + Qwen-2B.
- Start Qwen before apps/exit_monitor.py, because vLLM reserves its GPU memory when it starts. If apps/exit_monitor.py fails at start with a connection error to localhost:8000, Qwen isn't running. Check with `bash server/status.sh`, then run `bash server/start_laptop.sh`.
- Qwen-2B is the weakest Qwen model. On a drawn test scene its verdicts flipped between "partly blocked" and "clear", so watch the verdicts on real footage.
- WSL must use the gateway IP ($WIN), but Mimo uses the Windows Wi-Fi IP. Mixing them up is the most likely mistake.
- The first picture can take a few seconds to appear.
- If the stream drops, the run reconnects by itself and ends only after 30 s without frames.
