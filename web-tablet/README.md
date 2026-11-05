# Tablet web fallback

A standalone browser version of the Android monitoring screen. It supports
**Pre-recorded video** and **External camera**, with the same full-screen feed,
header controls, detection labels, Qwen status and extinguisher summary. The header
wraps on a phone so controls remain usable. It has no browser/device-camera mode.

All files are in `web-tablet/`. It does not modify the backend, Android app, existing
web frontend, or configuration. No npm install, build, cloud service or internet
connection is required. The backend's existing combined-session APIs provide
annotated video and results.

## Start

Keep your existing backend (and external-camera relay/model services, if used)
running as usual. Then run this from the project root on a laptop or server that
other devices can reach on Wi-Fi:

```bash
python3 web-tablet/serve.py
```

If your Python command is `python`, use that instead. The default web port is 5174;
change it with `--port 8080` if needed. This only serves the web extension; it does
not start or reconfigure detection.

1. On this computer open **http://localhost:5174**.
2. On a phone or another laptop, open **http://HOST_WIFI_IP:5174**, replacing
   `HOST_WIFI_IP` with the Wi-Fi address of the computer running `serve.py`.
3. In **Settings**, enter the existing **backend URL** and **backend token**.
   Example: `http://192.168.1.20:8100`. Use the real backend address/printed port;
   do not enter the web page's port 5174 as the backend port.
4. Tap **Start demo → External camera** or **Pre-recorded video**.

The computer serving the page and the backend can be different computers. From a
phone, `localhost` points to the phone, not either computer. Both addresses must be
reachable from that phone. The frontend makes requests directly to the backend;
video does not pass through the static web server.

On a Windows laptop that runs `serve.py` and the backend in WSL2 (NAT networking),
other devices can't reach ports 5174 and 8100 until Windows forwards them. The laptop's
own browser reaches them at `localhost` without a forward, so the page can work there
and still time out on a phone. The PowerShell block in
[run_demo/running.md, step 2](../run_demo/running.md#2-open-the-laptop-to-the-network-windows-administrator-powershell)
forwards both ports and opens the firewall for the local network.

The existing backend permits browser requests by default (`CORS: *`). If your
administrator has restricted that policy, this extension cannot override it. The
Wi-Fi/firewall must also allow reaching the web-server and backend ports. Nothing
in this extension changes those settings. HTTP works for both supported sources;
there is no browser camera permission or HTTPS requirement. Use an HTTP page with
an HTTP backend so the browser does not block mixed content.

## Resume a demo after an Android connection problem

Choose the **same source** that the Android app was showing (for recordings,
choose the same file). If its combined session is still running, the web interface
attaches as a viewer. It does not stop the Android session or restart detection.
You can also view the same running combined session concurrently.

If that session has ended, the web interface starts a new combined session. If an
unrelated session is active, it explains the conflict. Stop it on its owning device
before choosing a different source. An active tablet-camera session has no
server-side video stream to attach to; stop that source in the Android app first.
The backend's existing single-session limit still applies.

The page can stop and replace only sessions created by this browser's saved client
identity. Closing/reloading the page only disconnects the viewer; backend processing
continues. Transient connection failures retry automatically. HTTP state polling
also works when WebSocket connections are blocked. A browser cannot fix a stopped
backend, an unreachable backend network, or a failed camera stream.

## Settings and video locations

- Backend URL, selected source, and browser client identity are saved locally in
  this browser. Tokens stay in session storage for the current tab, not persistent
  local storage. Settings never prefill the password field; leave it empty to keep
  the tab's existing token for the same backend, or choose **Clear saved token**.
  A new tab may require entering the token again.
- Recordings still come from the backend's configured video folder. This extension
  does not add local phone-file upload/playback.
- External camera still uses the existing `external-camera` source ID and backend
  configuration. No camera URL is entered in this page.
- MJPEG image and WebSocket requests use the backend's existing query-token auth.
  The token is not added to the page's address, shared links, or persistent settings.
- Labels are rendered by the backend's existing Android-style annotation renderer.
  The summary follows Android's Qwen/date-verification rules. No new hints or
  detection behavior are added.

## Checks

Requires Node.js 18+ for the dependency-free frontend contract tests:

```bash
node --test web-tablet/tests/*.test.mjs
```

Tests cover authenticated API requests, session reuse/ownership, source switching,
error handling and Android-compatible summary output. They use fake HTTP responses;
no model downloads or running backend are required.
