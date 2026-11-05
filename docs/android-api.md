# Backend API for the Android app

For the integrated tablet demo, start with [Setup and run the demo](../run_demo/running.md). Authenticated `GET /api/sources` reports the configured external camera; create a combined session with `source: "external-camera"` to use it. This guide also describes the individual detection modes.


This page is for whoever builds the Android tablet app. It covers what the backend can
do for the app today and what it can't yet, how to connect from Android, and the full
message reference. The web app in `frontend/` uses the same API.

## What the app can do today

| The app wants to… | Use | Status |
|---|---|---|
| Detect exits **or** extinguishers on the tablet's own camera | [Live mode](#live-mode-on-android-tablet-camera): `WS /api/live` | available |
| Show the Pocket 3's picture with detections | [Session mode](#session-mode-on-android-pocket-3-picture): `POST /api/sessions`, an MJPEG stream and an events WebSocket | available, including **combined** detection |
| Get exits and extinguishers from one connection | `mode: "combined"` | available for tablet-camera live results and server-side sessions |
| Ask Qwen its own question, about an image or as free text | — | **not yet**: `POST /api/sessions/{id}/command` returns 501 |

- **Qwen already runs inside both modes.** It confirms exits, checks whether an
  obstacle is on the floor, gives verdicts and reads extinguisher dates. What's missing
  is a way for the app to ask its own questions.
- **Don't call the Qwen server (vLLM, port 8000) directly.** It has no authentication,
  and it isn't reachable from the network.
- **One session at a time.** The backend runs one session, live or server-side, at a
  time. A second one is refused with `already running … stop it first` until the
  first ends. To switch between exits and extinguishers, close the live connection and
  open a new one.
- **Camera sessions are recorded on the server.** Live mode, and server sessions from a
  stream URL or `external-camera`, are saved as two videos (original and annotated) in
  `BACKEND_OUT_DIR/recordings/`. The app doesn't need to do anything; `[server] record = false`
  in `config.toml` turns it off.

## Connecting from Android

**Address.** `http://<laptop Wi-Fi IP>:8100`. The IP changes at every venue (the demo
runbook, [running.md step 2](../run_demo/running.md#2-open-the-laptop-to-the-network-windows-administrator-powershell), prints it). Make the URL and the token settings in
the app, not constants. `GET /api/health` needs no token: use it for a "Test
connection" button.

**Token.**

- REST calls and MJPEG streams: header `Authorization: Bearer <token>`, or `?token=<token>`.
- **WebSockets:** use `?token=<token>` in the URL or an `Authorization: Bearer <token>` header.

**Cleartext HTTP.** The backend speaks plain `http://` and `ws://`. Android 9 (API 28)
and later block cleartext traffic by default, so allow it:

```xml
<!-- AndroidManifest.xml -->
<uses-permission android:name="android.permission.INTERNET" />
<application android:networkSecurityConfig="@xml/network_security_config" … >
```

```xml
<!-- res/xml/network_security_config.xml -->
<network-security-config>
    <base-config cleartextTrafficPermitted="true" />
</network-security-config>
```

The token then travels unencrypted over the venue Wi-Fi. That's acceptable for a
demo, not for production.

**Boxes.** Every `box` is `[x1, y1, x2, y2]` as fractions (0–1) of the frame the server
analysed, with the origin top-left. In live mode that frame is the one you sent; in
session mode it is the frame of the MJPEG stream.

## Live mode on Android (tablet camera)

The flow:

1. Open `ws://<host>:8100/api/live?token=…` and send `{"type": "start", "mode": …}`.
2. Wait for `ready`. It tells you `send_width` and `jpeg_quality`.
3. For each camera frame, if no frame is in flight: scale it to `send_width`, encode it
   as JPEG, and send a 12-byte header plus the JPEG.
4. On each `result`: draw the boxes, show the `events`, and allow the next frame.

The full message formats are in [Live mode: `WS /api/live`](#live-mode-ws-apilive)
below.

**CameraX setup.**

- **Resolution:** `ImageAnalysis` delivers 640×480 by default. That's too small for
  extinguisher mode, which wants 1920 px to read the sticker, so ask for 1920×1080.
- **Same picture in preview and analysis:** use the same aspect ratio for `Preview` and
  `ImageAnalysis`. Set the `PreviewView` to `FIT_CENTER`, because the default
  `FILL_CENTER` crops the edges and boxes near them would be off.
- **Upright frames:** rotate each frame by `rotationDegrees` before sending, so the
  server gets an upright picture. Boxes then refer to that upright picture.

```kotlin
val selector = ResolutionSelector.Builder()
    .setAspectRatioStrategy(AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY)
    .setResolutionStrategy(ResolutionStrategy(Size(1920, 1080),
        ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
    .build()
val preview = Preview.Builder().setResolutionSelector(selector).build()
val analysis = ImageAnalysis.Builder()
    .setResolutionSelector(selector)
    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
    .build()
analysis.setAnalyzer(Executors.newSingleThreadExecutor()) { image -> live.offer(image) }
previewView.scaleType = PreviewView.ScaleType.FIT_CENTER
```

**The client.** This is a sketch for OkHttp 4 and CameraX 1.3+. It has not been
compiled here, so treat it as a starting point:

```kotlin
class LiveClient(
    baseUrl: String, token: String,
    private val mode: String,                        // "exits" or "extinguisher"
    private val onMessage: (JSONObject) -> Unit,     // ready / result / error, on OkHttp's thread
) {
    private val client = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()
    private val inFlight = AtomicBoolean(false)      // one frame on the server at a time
    @Volatile private var ready = false
    @Volatile private var sendWidth = 960
    @Volatile private var jpegQuality = 75
    private var seq = 0
    private val t0 = SystemClock.elapsedRealtime()

    private val ws: WebSocket = client.newWebSocket(
        Request.Builder().url(baseUrl.replaceFirst("http", "ws") + "/api/live?token=$token").build(),
        object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("type", "start").put("mode", mode)
                    .put("use_locate", false).toString())
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                val msg = JSONObject(text)
                when (msg.getString("type")) {
                    "ready" -> {
                        sendWidth = msg.getInt("send_width")
                        jpegQuality = (msg.getDouble("jpeg_quality") * 100).toInt()
                        ready = true
                    }
                    "result", "error" -> inFlight.set(false)
                }
                onMessage(msg)
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                ready = false                            // show "disconnected"; reconnect from the UI
            }
        })

    /** CameraX analyzer: sends this frame unless the previous one is still on the server. */
    fun offer(image: ImageProxy) = image.use {
        if (!ready || !inFlight.compareAndSet(false, true)) return@use
        val jpeg = toJpeg(it)
        val msg = ByteBuffer.allocate(12 + jpeg.size).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(seq++).putDouble((SystemClock.elapsedRealtime() - t0) / 1000.0).put(jpeg)
        if (!ws.send(msg.array().toByteString())) inFlight.set(false)
    }

    private fun toJpeg(image: ImageProxy): ByteArray {
        var bmp = image.toBitmap()
        val rotation = image.imageInfo.rotationDegrees
        if (rotation != 0) bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height,
            Matrix().apply { postRotate(rotation.toFloat()) }, true)
        if (bmp.width > sendWidth) bmp = Bitmap.createScaledBitmap(
            bmp, sendWidth, bmp.height * sendWidth / bmp.width, true)
        return ByteArrayOutputStream()
            .also { bmp.compress(Bitmap.CompressFormat.JPEG, jpegQuality, it) }.toByteArray()
    }

    fun close() {
        ws.send("""{"type":"stop"}""")
        ws.close(1000, null)
    }
}
```

**Drawing boxes over a `FIT_CENTER` preview.** The frame is letterboxed inside the view.
With `imgW × imgH` the upright frame size and `viewW × viewH` the view size:

```kotlin
val scale = minOf(viewW / imgW, viewH / imgH)
val dx = (viewW - imgW * scale) / 2f
val dy = (viewH - imgH * scale) / 2f
val rect = RectF(dx + box[0] * imgW * scale, dy + box[1] * imgH * scale,
                 dx + box[2] * imgW * scale, dy + box[3] * imgH * scale)
```

Colours used by the web app: exits green (clear), amber (partial), red (blocked) and
grey (checking), with obstacles in orange. Extinguishers are green (valid), amber (due
soon or check date), red (expired), purple (unreadable) and grey (reading).

## Session mode on Android (Pocket 3 picture)

Here the backend reads the camera stream itself, and the app shows the video and the
events it sends back. A session runs exits only unless the body asks for
`"mode": "combined"` (exits and extinguishers, what the tablet app uses).

1. **Start:** `POST /api/sessions` with this body:

   ```json
   {"source": "rtsp://<windows-host>:8554/live/x2", "loop": false, "use_locate": false}
   ```

   - `<windows-host>` is Windows as seen from the backend in WSL; the runbook
     ([running.md step 3](../run_demo/running.md#3-start-qwen-and-the-backend-wsl-terminal-1)) prints the full address. Make it a setting in the app.
   - `use_locate: false` matters on the laptop, which has no LocateAnything.
   - The reply is the [session object](#session-object). Keep its `id`.
2. **Picture:** read `GET /api/sessions/{id}/stream/annotated` (or `original`,
   `combined`), an MJPEG stream; see below.
3. **Events:** open `WS /api/sessions/{id}/events?token=…`. You get an `event` on each
   exit status change, `state` every 0.5 s, and `end` when the session ends.
4. **Stop:** `DELETE /api/sessions/{id}`. Once it has ended,
   `GET /api/sessions/{id}/report` returns the report.

The session's `status` goes `starting` → `running` while the backend opens the stream.
If the camera isn't streaming, the session ends with `status: "error"` after about 30 s.

### Reading the MJPEG stream

Android has no built-in MJPEG view, but the backend's stream is easy to read. Each part
carries a `Content-Length` header:

```
--frame\r\nContent-Type: image/jpeg\r\nContent-Length: <n>\r\n\r\n<n bytes of JPEG>\r\n
```

A sketch for a background thread (OkHttp 4):

```kotlin
fun readMjpeg(client: OkHttpClient, url: String, token: String, onFrame: (Bitmap) -> Unit) {
    val request = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
    client.newCall(request).execute().use { response ->
        val source = response.body!!.source()
        while (!source.exhausted()) {
            var length = -1
            while (true) {                                   // part headers, up to the blank line
                val line = source.readUtf8LineStrict()
                if (line.isEmpty() && length >= 0) break
                if (line.startsWith("Content-Length:", ignoreCase = true))
                    length = line.substringAfter(':').trim().toInt()
            }
            val jpeg = source.readByteArray(length.toLong())
            BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)?.let(onFrame)
        }
    }
}
```

The stream ends when the session ends. Use a client with a generous read timeout,
e.g. 30 s, so a short stall doesn't cut it.

## Reference

Everything below applies to any client. The web app's [app.js](../frontend/app.js) is a working
example of all of it.

### Overview

The backend offers two ways to work:

| | **Live mode** (recommended) | **Server-video sessions** |
|---|---|---|
| Video comes from | **your app**: camera or file, sent frame by frame | a file or `rtsp://` stream **on the server** |
| You get back | JSON only: boxes, statuses, events | MJPEG video streams + JSON state/events |
| Modes | `exits` and `extinguisher` | `exits` only |
| Endpoint | `WS /api/live` | `/api/sessions…` |
| Latency | ~50–80 ms per frame on a LAN | depends on the link; video lags over slow networks |

A typical live-mode app does this:

```
GET  /api/health                       is the backend there? does it need a token?
GET  /api/info                         which model servers are up, which modes exist
WS   /api/live?token=…                 open one WebSocket
  → {"type":"start","mode":"extinguisher"}
  ← {"type":"ready", "send_width":1920, "jpeg_quality":0.9, …}
  loop while the camera runs:
    → binary frame (12-byte header + JPEG)
    ← {"type":"result", "extinguishers":[…], "events":[…], …}   draw it, then send the next frame
  → {"type":"stop"}  (or just close the socket)
```

### Basics

**Base URL:** `http://<server-ip>:<port>`, or `ws://` for WebSockets. The port is
the one the backend prints when it starts (usually `8100`). The interactive OpenAPI docs
for the REST endpoints are at `/docs`.

**Auth:** if the server has `BACKEND_TOKEN` set (the default after `start_all.sh`),
every call except `/api/health` needs the token:

- REST: header `Authorization: Bearer <token>` (or `?token=<token>`).
- WebSocket: `?token=<token>` in the URL. The server reads a WebSocket's token only
  from the URL, so native clients (OkHttp) must use it too.
- A wrong or missing token gets **401** `{"detail": "missing or wrong token"}`
  (REST). On a WebSocket, the handshake is refused: most clients report **HTTP 403**.

**Errors:** REST errors are JSON `{"detail": "<message>"}` with the usual status
codes (400, 401, 404, 409, 500, 501).

**CORS:** all origins are allowed by default (`BACKEND_CORS_ORIGINS`), so browser apps
on any host can call the API.

**Coordinates:** every `box` is `[x1, y1, x2, y2]` as **fractions of the frame (0–1)**,
origin top-left. To draw one, multiply by the width and height of the image as it is
shown on screen. If you letterbox the video, add the offset.

**One pipeline:** the GPU server runs `BACKEND_MAX_SESSIONS` sessions at a time
(default **1**), and live connections count. A second client is refused until the
first one stops.

### REST endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | no | liveness; whether a token is needed |
| GET | `/api/info` | yes | model servers, live modes, videos, running sessions |
| GET | `/api/videos` | yes | videos a server-video session can play |
| POST | `/api/sessions` | yes | start a server-video session |
| GET | `/api/sessions` | yes | all server-video sessions |
| GET | `/api/sessions/{id}` | yes | one session: status + latest state |
| DELETE | `/api/sessions/{id}` | yes | stop a session (its report is written) |
| GET | `/api/sessions/{id}/report` | yes | the session's report, after it ended |
| GET | `/api/sessions/{id}/stream/{name}` | yes | MJPEG video: `original`, `annotated`, `combined` |
| WS | `/api/sessions/{id}/events` | yes | pushed events + state for a session |
| POST | `/api/sessions/{id}/command` | yes | chat / commands: **not implemented yet (501)** |
| WS | `/api/live` | yes | **live mode** (next section) |

#### `GET /api/health`

```json
{"ok": true, "auth_required": true}
```

#### `GET /api/info`

```json
{
  "models": {
    "locate_anything": {"url_set": true, "up": true},
    "qwen":            {"url_set": true, "up": true}
  },
  "modes": [
    {"id": "exits",        "title": "Exit obstruction"},
    {"id": "extinguisher", "title": "Fire extinguisher"}
  ],
  "videos": ["IMG_0450.MOV", "IMG_0451.MOV"],
  "max_sessions": 1,
  "sessions": [],
  "live_sessions": [{"id": "8eaf56cb", "mode": "exits", "frames": 240}]
}
```

- `models.*.up`: whether that model server answers right now. Live mode skips a model
  that is down rather than failing.
  - Exit mode needs YOLOE, which always runs in the backend; LocateAnything and Qwen
    make it better.
  - Extinguisher mode needs **Qwen** to read dates; without it, extinguishers are only
    detected.
- `modes`: use `id` in the live `start` message and `title` in your UI.
- `sessions`: server-video sessions, in the [session object](#session-object) format.
  `live_sessions`: live connections.

#### `GET /api/videos`

```json
{"videos": ["IMG_0450.MOV", "IMG_0451.MOV", "IMG_0452.MOV"]}
```

These are files in the server's `BACKEND_VIDEO_DIR` (`.mov .mp4 .avi .mkv .m4v`).

#### `POST /api/sessions`: start a server-video session (exit mode)

Request body (JSON):

| Field | Type | Default | |
|---|---|---|---|
| `source` | string | *required* | a name from `/api/videos`, or an `rtsp://` / `rtmp://` / `http(s)://` stream URL |
| `loop` | bool | `true` | restart the file at the end |
| `use_yoloe` | bool | `true` | per-frame detector |
| `use_locate` | bool | `true` | LocateAnything |
| `use_qwen` | bool | `true` | Qwen checks |
| `options` | object | `{}` | `hold`, `la_interval`, `name_every`, `recheck`, `confirm_timeout` (seconds; see `python run.py exits --help`) |

```json
{"source": "IMG_0452.MOV", "loop": false}
```

The response is **201** with the [session object](#session-object). Errors:

- **400:** `{"detail": "video not found: …"}`, or a path outside the video folder.
- **409:** `{"detail": "already running: <id> (max 1) — stop it first"}`.

#### Session object

Returned by `GET /api/sessions/{id}`, `POST`, `DELETE` and in lists:

```json
{
  "id": "f986678d",
  "source": "IMG_0452.MOV",
  "status": "running",
  "error": null,
  "created": 1792210042.55,
  "loop": false,
  "models": {"yoloe": true, "locate_anything": true, "qwen": true},
  "events": 1,
  "state": {
    "video_time_s": 9.93,
    "fps": 16.2,
    "exits": [
      {"id": 73, "exit_sign": true, "status": "partially_obstructed", "status_text": "PARTIAL",
       "since_s": 0.0, "blockers": ["office chair"], "visible": true}
    ],
    "events": [
      {"video_time_s": 9.93, "text": "EXIT #73: CHECKING -> PARTIAL (office chair)",
       "status": "partially_obstructed"}
    ]
  },
  "report_ready": false
}
```

- `status`: `starting` → `running` → `finished` | `stopped` | `error` (then `error`
  has the reason).
- `state`: the latest snapshot (empty `{}` until the first frame). `state.events` is
  the last 20.
- Exit `status` values are listed in [exit statuses](#exit-mode-result).
- `DELETE` asks the session to stop and returns at once. `status` turns `stopped` a
  moment later, and then `report_ready` is `true`.

#### `GET /api/sessions/{id}/report`

You get **409** until the session has ended. Afterwards:

```json
{
  "source": "IMG_0452.MOV", "frames": 243, "dropped": 168, "fast_loop_ms": 77.6,
  "exits": [
    {"id": 58, "final_status": "clear", "blockers": [], "first_seen_s": 5.73, "last_seen_s": 8.07,
     "history": [{"video_time_s": 8.0, "status": "clear", "blockers": []}],
     "snapshot": "exit58_0008.0s_clear.jpg"}
  ],
  "events": [{"video_time_s": 8.0, "event": "door #58: CHECKING -> CLEAR"}]
}
```

#### `GET /api/sessions/{id}/stream/{original|annotated|combined}`

This is an MJPEG stream (`multipart/x-mixed-replace`). In a browser use
`<img src=".../stream/annotated?token=…">`; on Android, see [Reading the MJPEG stream](#reading-the-mjpeg-stream). The
stream ends when the session ends.

#### `WS /api/sessions/{id}/events?token=…`

The server pushes these messages; the client sends nothing:

```json
{"type": "hello", "session": { …session object… }}
{"type": "event", "event": {"video_time_s": 9.93, "exit_id": 73, "exit_sign": true,
                            "from": "unknown", "to": "partially_obstructed", "status_text": "PARTIAL",
                            "blockers": ["office chair"], "text": "EXIT #73: CHECKING -> PARTIAL (office chair)",
                            "seq": 0}}
{"type": "state", "status": "running", "state": { …as in the session object… }}
{"type": "end", "session": { …session object… }}
```

- `event` is sent on every exit status change.
- `state` is sent every 0.5 s.
- `end` is sent once, then the socket closes.
- Close code `4401` means a wrong token; `4404` means an unknown session.

### Live mode: `WS /api/live`

#### Connect and start

```
ws://<server-ip>:<port>/api/live?token=<token>
```

The **first message** must be a text (JSON) `start`:

```json
{"type": "start", "mode": "extinguisher"}
```

| Field | Type | Default | |
|---|---|---|---|
| `type` | `"start"` | *required* | |
| `mode` | `"exits"` \| `"extinguisher"` | `"exits"` | what to detect (ids from `/api/info`) |
| `use_yoloe` | bool | `true` | exits mode only |
| `use_locate` | bool | `true` | exits mode only: LocateAnything |
| `use_qwen` | bool | `true` | both modes: Qwen (extinguisher dates need it) |
| `options` | object | `{}` | per mode, see below |

`options` per mode:

- **`exits`** (floats, seconds): `hold` (1.5), `la_interval` (1.0), `name_every` (3),
  `recheck` (8), `confirm_timeout` (6).
- **`extinguisher`:**
  - `min_px` (120): extinguishers narrower than this in your frame aren't read.
  - `max_reads` (5) and `parallel` (2): how many reads, and how many at once.
  - `soon_days` (30): how near the due date counts as "due soon".
  - `service_months` (12): how long a service lasts when only the last-service date
    is known.
  - `life_years` (10): service life from the manufacture date.
  - `monthfirst` (false): read `05/06/2027` as month/day.

The server replies **`ready`** (the first start takes a few seconds while models load):

```json
{
  "type": "ready",
  "id": "791214b4",
  "mode": "extinguisher",
  "models": {"yoloe": true, "locate_anything": false, "qwen": true},
  "skipped": [],
  "send_width": 1920,
  "jpeg_quality": 0.9
}
```

- `models`: what this session actually uses. `skipped` lists model servers that were
  down and are therefore not used (e.g. `["Qwen"]`). Tell the user.
- **`send_width` / `jpeg_quality`: send frames this wide (scale down, keep the aspect
  ratio, never scale up) at this JPEG quality.**
  - `exits`: 960 px at 0.75, about 50 KB per frame.
  - `extinguisher`: 1920 px at 0.9, about 300 KB per frame. Full resolution is needed
    to read the date on a sticker.

If the start fails, you get `{"type": "error", "detail": "…"}` and the server closes
the socket. Causes:

- an unknown mode (`"ValueError: unknown mode 'x' — one of exits, extinguisher"`);
- another session already running (`"SessionError: already running: <id> (max 1) — stop it first"`);
- `"first message must be {type: start}"`.

#### Send frames (binary messages)

Each frame is **one binary WebSocket message**: a 12-byte little-endian header, then
the JPEG file bytes.

| Offset | Size | Type | Field |
|---|---|---|---|
| 0 | 4 | `uint32` LE | `seq`: your frame counter; echoed back in the result |
| 4 | 8 | `float64` LE | `t`: video time in seconds (camera: seconds since start); echoed back |
| 12 | … | bytes | a JPEG image (any size; colour) |

```python
# Python
msg = struct.pack("<Id", seq, t) + jpeg_bytes
```

```js
// JavaScript (browser)
const msg = new Uint8Array(12 + jpeg.length);
const v = new DataView(msg.buffer);
v.setUint32(0, seq, true); v.setFloat64(4, t, true); msg.set(jpeg, 12);
ws.send(msg.buffer);
```

```kotlin
// Kotlin (Android, OkHttp)
val buf = ByteBuffer.allocate(12 + jpeg.size).order(ByteOrder.LITTLE_ENDIAN)
buf.putInt(seq).putDouble(t).put(jpeg)
webSocket.send(ByteString.of(*buf.array()))
```

**Pacing: keep exactly one frame in flight.** Send a frame, wait for its `result`,
then send the next one, capped at about 15 per second. The server processes frames in
order, one at a time. If you send faster, frames queue up and the boxes fall behind
the video. When the camera is paused, just stop sending.

If the frame size changes (e.g. the camera rotates), the server starts tracking afresh
automatically.

#### Results

Each frame gets exactly one reply:

```json
{"type": "result", "mode": "exits", "seq": 239, "t": 15.93, "proc_ms": 95.7, "yoloe_ms": 93.3,
 "locate_update": false, "events": [ … ], …mode-specific fields… }
```

| Field | |
|---|---|
| `seq`, `t` | echoed from your frame header. Match the result to the frame and measure the round trip |
| `proc_ms` | server processing time for this frame |
| `yoloe_ms` | detector time (part of `proc_ms`) |
| `locate_update` | exits mode: LocateAnything boxes were merged in this frame |
| `events` | **new** events since the previous result: `[{"video_time_s", "text", "status"}]`. Show them as notifications |

A frame that can't be decoded returns `{"type": "error", "detail": "could not decode the JPEG"}`
(or `"frame message too short"`), and **the session stays open**.

#### Exit mode result

```json
{
  "type": "result", "mode": "exits", "seq": 239, "t": 15.93, "proc_ms": 95.7, "yoloe_ms": 93.3,
  "locate_update": false, "events": [],
  "exits": [
    {"id": 125, "exit_sign": false, "confirmed": null, "status": "partially_obstructed",
     "status_text": "PARTIAL", "since_s": 0.5, "blockers": ["office chair x2"],
     "box": [0.0, 0.0, 0.4899, 1.0]}
  ],
  "obstacles": [
    {"id": 89, "label": "office chair", "depth": null, "exit_id": 125, "box": [0.0, 0.4899, 0.0581, 1.0]}
  ]
}
```

`exits[]`: the exits in view.

| Field | |
|---|---|
| `id` | track ID, stable while the exit is followed |
| `exit_sign` | an EXIT sign belongs to this door |
| `confirmed` | Qwen says it is a real exit: `true`, `false`, or `null` (not asked yet / no Qwen) |
| `status` / `status_text` | `unknown`/`CHECKING`, `clear`/`CLEAR`, `partially_obstructed`/`PARTIAL`, `obstructed`/`BLOCKED` |
| `since_s` | seconds in this status |
| `blockers` | names of what obstructs it |
| `box` | 0–1 box |

`obstacles[]`: the objects obstructing the exits in view.

| Field | |
|---|---|
| `id`, `label`, `box` | as above |
| `exit_id` | which exit it obstructs |
| `depth` | `"floor"` if Qwen checked it stands on the floor, else `null` |

The web app colours exits green (clear), amber (partial), red (blocked) and grey
(checking), and draws obstacles in orange.

#### Extinguisher mode result

```json
{
  "type": "result", "mode": "extinguisher", "seq": 176, "t": 7.5, "proc_ms": 61.2, "yoloe_ms": 57.2,
  "locate_update": false,
  "events": [{"video_time_s": 7.5, "text": "EXTINGUISHER #1: VALID — due 06/2027 (next service; 2/2 reads agree)",
              "status": "valid"}],
  "extinguishers": [
    {
      "id": 1, "status": "valid", "status_text": "VALID",
      "safe": true, "safe_text": "Safe to use",
      "confirmed": true, "reading": false, "visible": true,
      "due": "2027-06-30", "due_text": "06/2027", "basis": "next service",
      "dates": {
        "LAST_SERVICE": {"date": "2026-06-30", "text": "06/2026", "raw": "JUNE-2026", "precision": "month"},
        "NEXT_SERVICE": {"date": "2027-06-30", "text": "06/2027", "raw": "JUNE 2027", "precision": "month"}
      },
      "dates_read": ["LAST_SERVICE JUNE-2026", "NEXT_SERVICE JUNE 2027"],
      "reason": null, "reads": 4, "agree": 3, "hint": null,
      "text": "VALID — due 06/2027 (next service; 3/4 reads agree)",
      "box": [0.4135, 0.1009, 0.5606, 0.9054]
    }
  ],
  "inventory": [ { …same fields, without "box"… } ]
}
```

- **`extinguishers`**: the ones **in view**, with a `box`. Draw these on the video.
- **`inventory`**: **every** extinguisher seen in this session, including those out of
  view (`visible: false`). Use it for a list or summary screen.

| Field | |
|---|---|
| `id` | track ID |
| `status` | `reading` → `valid` \| `expiring_soon` \| `expired` \| `unclear` \| `unreadable` |
| `status_text` | `READING`, `VALID`, `DUE SOON`, `EXPIRED`, `CHECK DATE`, `UNREADABLE` |
| `safe` | **`true`** (valid, due soon), **`false`** (expired), **`null`** (unknown: reading, unclear, unreadable) |
| `safe_text` | e.g. `"Safe to use"`, `"Safe to use — service due soon"`, `"Not safe — overdue"`, `"Unknown — tag not read"` |
| `confirmed` | **two reads agree on the due date**. Until then, treat the status as provisional (the web app adds "?") |
| `reading` | a read is in progress now |
| `due` / `due_text` | the date the extinguisher stops being OK (ISO / display), or `null` |
| `basis` | why: `"next service"`, `"expiry date"`, `"last service + 12 months"`, `"manufactured + 10 years"` |
| `dates` | the dates read off the tag, by kind: `LAST_SERVICE`, `NEXT_SERVICE`, `EXPIRY`, `MANUFACTURED`, `UNKNOWN`. A kind that is missing was not on the tag. `date` is ISO; month-only dates count to the month's end. `text` is for display; `raw` is what was written |
| `dates_read` | the same dates as short text, for logs |
| `reason` | when `unreadable`: `no_tag`, `too_small`, `blurry` |
| `reads` / `agree` | reads done / reads agreeing with `due` |
| `hint` | a tip for the user: `"move closer"`, `"hold steady"`, or `null` |
| `text` | one-line summary, ready to show |

How a status develops:

1. A new extinguisher starts as `reading`, with hint `"move closer"` while it's too
   small.
2. Once close enough, it is read, about 1.5 s per read.
3. After the first read with a date, it shows a provisional status (`confirmed: false`).
4. It becomes `confirmed: true` once two reads agree.
5. Events are sent when the status, the due date or the confirmation changes.

Colours used by the web app: green `valid`, amber `expiring_soon` / `unclear`, red
`expired`, purple `unreadable`, grey `reading`.

#### Other messages

| You send (text JSON) | Server replies | |
|---|---|---|
| `{"type": "reset"}` | `{"type": "reset_done"}` | forget everything tracked (new video, new room). Send it when the user switches the source |
| `{"type": "stop"}` | the socket closes | ends the session; the server writes its report and finishes its recording in `BACKEND_OUT_DIR` |

Just closing the socket also ends the session. **To switch mode,** close the socket
and open a new one with the other `mode`.

### Minimal client (Python, tested)

```python
# pip install websockets opencv-python
import asyncio, json, struct, cv2, websockets

URL = "ws://<server-ip>:8100/api/live?token=<token>"

async def main(video="IMG_0454.MOV", mode="extinguisher"):
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "mode": mode}))
        ready = json.loads(await ws.recv())
        if ready["type"] != "ready":
            raise SystemExit(ready)                      # {"type": "error", "detail": …}
        cap, seq = cv2.VideoCapture(video), 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            k = min(1.0, ready["send_width"] / frame.shape[1])
            frame = cv2.resize(frame, None, fx=k, fy=k)
            _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(100 * ready["jpeg_quality"])])
            await ws.send(struct.pack("<Id", seq, seq / fps) + jpg.tobytes())
            r = json.loads(await ws.recv())              # one frame in flight
            for ev in r.get("events", []):
                print(f"{ev['video_time_s']:6.1f}s  {ev['text']}")
            seq += 1
        await ws.send(json.dumps({"type": "stop"}))
        print("final:", [x["text"] for x in r.get("inventory", r.get("exits", []))])

asyncio.run(main())
```
