Here's what to do on the camera and in DJI Mimo, in order. I took it from [docs/run.md](docs/run.md), [docs/live-camera.md](docs/live-camera.md) and the "DJI Pocket 3 to Python: Setup Guide" doc. The menu names come from that guide and may differ a little between Mimo versions.

**Before you touch the camera:** start MediaMTX on Windows (run.md step 1). Then run `ipconfig` and write down the **Windows Wi-Fi IPv4 address** (under "Wireless LAN adapter Wi-Fi", for example `192.168.1.23`). Mimo needs this address. Don't use the WSL `$WIN` address here.

## First time only

1. **Charge the camera.** Firmware updates won't run below 15%. Charge it fully before a demo.
2. **Install DJI Mimo.**
   - Android 9 or later: download the APK from [DJI's Pocket 3 downloads page](https://www.dji.com/osmo-pocket-3/downloads). It isn't on Google Play, and you should avoid third-party APK sites.
   - iPhone: get it from the App Store.
3. **Open Mimo and allow its permissions:** Nearby devices, Location, and photos/media. Without Nearby devices and Location, the app can't find the camera over Bluetooth.
4. **Android only:**
   - Go to Settings → Apps → DJI Mimo → Battery and choose **Unrestricted**.
   - On Samsung, also turn off *Intelligent Wi-Fi → Switch to mobile data*.
5. **Turn on the phone's Wi-Fi and Bluetooth.** Mimo uses both.
6. **Power on the Pocket 3:** rotate the touchscreen clockwise, or press the Shutter/Record button.
7. **Connect in Mimo:**
   - On the home screen, tap the camera icon, choose **Osmo Pocket 3** and follow the prompts. Keep the camera next to the phone.
   - Activate it when asked. It may ask you to sign in to a DJI account.
8. **Install a firmware update if Mimo offers one.** It takes about 2 minutes. Don't power off the camera while it runs.
9. **Check it works:** Mimo should show the camera's live view.

## Each demo

1. Make sure MediaMTX is running on Windows. Its log should say "configuration loaded".
2. Put the phone on the **same Wi-Fi as the laptop**. A guest network won't work.
3. Power on the camera, open Mimo, and wait until the live view appears.
4. Slide the bottom menu to **LIVESTREAM**, choose **RTMP**, then tap **Start Livestreaming Settings**. Wait about 15 seconds on that screen.
5. Under **Camera Network Status**, connect the Pocket 3 to that same Wi-Fi. Enter the password if asked.
6. Set the streaming address to:
   ```
   rtmp://<Windows Wi-Fi IP>:1935/live/x2
   ```
   There is no stream key. Keep the path exactly `live/x2`, because the WSL command uses the same name.
7. Optionally, swipe for more settings and lower the quality. A lower resolution or bitrate can cut the delay.
8. Tap **Start Livestream**. On iPhone, allow Local Network access if asked.
9. **Check the MediaMTX window.** It should show `is publishing to path 'live/x2'`. If nothing appears, the camera can't reach Windows (see below).
10. Keep Mimo open with the phone screen on for the whole demo, and point the camera at the exit.
11. Now run the WSL commands (run.md step 3: `start_laptop.sh`, then `apps/camera_check.py` or `apps/exit_monitor.py`).

## Stopping

Press `q` in the Python window, tap stop in Mimo's livestream screen, close MediaMTX, then turn off the camera. To free the GPU, also run `bash server/stop_all.sh qwen`.

## If something goes wrong on the camera side

- **Mimo can't find the camera:**
  - Check that Wi-Fi and Bluetooth are both on and Mimo is up to date.
  - A DJI Mic 2 on 2.4 GHz blocks Mimo. Switch the camera to 5.8 GHz under Wireless Connection, or disconnect the mic.
- **Android warns that the Wi-Fi has no internet:** choose to stay connected.
- **"Livestream failed", or nothing in the MediaMTX log:**
  - Check the IP in Mimo. It must be the Windows **Wi-Fi** IP, and it may have changed since last time, so re-run `ipconfig`.
  - Make sure everything is on the same non-guest Wi-Fi.
  - Check that the firewall rule for TCP 1935 exists.
- **Frequent "stream lost — reconnecting":** the usual causes are weak Wi-Fi, the phone going to sleep, or the camera being hot or low on battery.

This path hasn't been tried with the real camera yet, so your first run is also the test. To measure the delay, use `python run.py camera-check --clock`, which is described in [docs/live-camera.md](docs/live-camera.md).