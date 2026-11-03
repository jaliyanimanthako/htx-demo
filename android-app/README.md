# Fire Safety Demo for Android tablets

The source picker also supports **External camera** using the shared root `config.toml`. See [RTMP backend integration](../docs/tablet-rtmp-integration.md) for setup on WSL or the GPU server. The tablet interface and detection labels are retained.


Native Kotlin / Jetpack Compose landscape client for combined **exit-obstruction and fire-extinguisher monitoring**, with optional Qwen-assisted analysis. Local MacBook development works without a Qwen/GPU server. The Python backend runs both detection pipelines. The tablet displays backend-annotated recordings or uses its rear camera with local preview and backend detection overlays. It does not run models locally or start Qwen. Flame/smoke detection is not implemented.

## Operator workflow

1. Have the demo administrator start the Python backend. Qwen is optional and requires no tablet configuration.
2. Open the app. On first setup, enter the **Backend URL** and **Backend token**, then tap **Save**.
3. Tap **Start demo** and choose a source:
   - **Pre-recorded video**: choose a backend demo video.
   - **Tablet camera**: grant camera permission when asked, then use the rear camera for live monitoring.
4. View the feed, exit-hazard count, visible extinguishers, and date/due-date status when available.
5. Use **Change source** to switch between recordings and the tablet camera without restarting.

If camera permission is denied, **Choose another source** returns to the recorded-video workflow. **Grant camera permission** requests permission again; **Open app settings** provides a path when Android no longer shows the permission prompt. Camera access is optional for using recorded videos.

There is one demo configuration: exit and extinguisher detection with YOLOE. The backend automatically adds Qwen assistance when its configured service is available; otherwise detection continues without it. No mode selection, model toggle, session ID or command-line action is needed on the tablet. Offline/mock controls are not exposed.

The address, source choice, last video selection and internal recorded-session reference persist across launches. Camera connections are recreated rather than persisting a live-session ID. The token is encrypted with AES-GCM using a device-bound Android Keystore key, and backups are disabled. **Settings** allows editing the address or replacing the token. The saved token is never prefilled in the editor; leave the field blank to keep it on the same backend, or select **Clear saved token**. Changing the backend clears the previous video/session reference and does not carry its token to the new backend.

Returning from the background safely restores the selected source. Backgrounding releases CameraX capture and analysis, stops uploads, closes the camera WebSocket and its backend live session, and clears pending frames/results. Recorded-video mode releases its viewer connections but leaves backend processing running so it can resume. Temporary dropped connections retry automatically with delays starting at 2 seconds and increasing to 30 seconds; a successful connection resets the delay. **Retry now** can request an immediate retry.

## Visible states

- **Ready**: configured and waiting for a video selection.
- **Starting demo…**: checking readiness, starting processing or waiting for video.
- **Running**: connected video and session updates.
- **Reconnecting…**: an established viewer connection dropped; reconnection is automatic.
- **Qwen assistance unavailable — date verification is disabled. Detection continues normally.**: a non-blocking running status. Exit/obstruction rules and extinguisher detection continue; date/tag reading and Qwen confirmation are unavailable.
- **Backend unavailable**: the tablet cannot communicate with the configured backend. Check connectivity and Settings.
- **Camera permission required**: camera mode cannot start until access is granted; recordings remain available.
- **Camera initialization failed**: the rear camera could not open. Retry, check Android camera access, or choose a recording.
- **Live session failed**: the backend was reachable but could not initialize/process the live camera session.
- **Could not start demo**: another startup problem, such as an invalid token, unavailable video or backend processing error. The app shows a short explanation without raw backend exceptions.

Without Qwen, extinguisher dates are **not verified**: no due date or confirmed/valid/safe verdict is inferred. Exit confirmation and depth fields that depend on Qwen remain null/unconfirmed, while supported geometric obstruction rules still operate. When Qwen is active, the screen reports **Qwen assistance active**. Date readings marked unconfirmed are provisional; unavailable/unread dates are not a verified safe verdict. Extinguisher presence does not increase the exit-hazard count.

Camera overlays and backend-annotated recordings use the same box colors: **gray** for checking/reading, unavailable or unverified dates and unreadable tags; **green** for clear exits and verified valid dates; **amber** for partial obstruction and verified dates due soon; **red** for blocked exits, obstructing objects and verified expired dates. Pending Qwen exit confirmation stays gray while assistance is active; without Qwen, exits retain their geometric status colors. Pending tag reads and provisional date assessments stay gray. Annotation labels omit tracking numbers for exits, extinguishers and obstacles; IDs remain in tracking, API data, reports and summary lists. Specific object names remain unchanged. Restart the Python backend and install the updated Android app to apply both rendering paths.

Combined recordings use `backend/annotations.py` over the same normalized live result consumed by the camera overlay. Both show thin colored outlines and white labels on translucent black backgrounds, with labels such as `EXIT · CLEAR` and `Extinguisher · READING`. Recordings no longer use the CLI monitors' solid colored label backgrounds, `door` labels, timers, tag-date strings or floor-check suffixes. `tests/fixtures/annotation_cases.json` is checked by both backend and Android tests to keep labels and colors consistent. Backend source edits require restarting the running Python process, then selecting a recording again; rebuilding the APK does not reload the server.

## Developer setup

Open **android-app/** in Android Studio. Requirements: JDK 17, Android SDK platform 35/build tools, and an Android 8+ device/emulator (API 26+). Gradle 8.13, AGP 8.7.2, Kotlin 1.9.25 and Compose compiler 1.5.15 are pinned. Media3 remains for the unused offline playback components.

Set `ANDROID_HOME` or create the ignored `android-app/local.properties` with `sdk.dir=...`. On this Mac:

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
android-app/gradlew -p android-app testDebugUnitTest assembleDebug assembleDebugAndroidTest lintDebug
adb install -r android-app/app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n sg.htx.tablet/.MainActivity
```

Start the backend from the repository root; no Qwen server is needed:

```sh
BACKEND_VIDEO_DIR="$PWD/demo_videos" BACKEND_TOKEN=local-demo \
  .venv-backend/bin/python -m backend --host 0.0.0.0 --port 8100
```

To restore Qwen later, configure `QWEN_URL=http://GPU_HOST:8010/v1` on the backend and make that Qwen/vLLM service reachable. Newly created demo sessions then enable exit assistance and extinguisher tag/date reading automatically. The tablet does not start or configure Qwen and has no toggle. Availability is evaluated at session startup; an existing degraded session is not hot upgraded. Start a new session by changing source/video, resuming the tablet camera after backgrounding, or restarting the backend, to evaluate availability again.

Use `http://10.0.2.2:8100` from the Android emulator, or the laptop's LAN address on a physical tablet. The backend may choose another port if occupied; use its printed port. A LocateAnything server is not required for this demo. Cleartext HTTP/WS is enabled for LAN development; TLS endpoints are also supported.

## Architecture and session lifecycle

- `MainActivity` forwards foreground/background lifecycle to the ViewModel and keeps the screen awake.
- `FireDetectionViewModel` binds Android storage, the existing session controller and the viewer to `DemoController`. The controller contains the existing lifecycle orchestration extracted into an Android-independent class for JVM tests; it is the sole session/viewer owner.
- `DemoPreferences` persists configuration and internal session/video references. Credentials go through `TokenStore`, which uses Android Keystore and AES-GCM. No plaintext token preference is written.
- `SessionController` lists `/api/videos` without a Qwen readiness gate, and uses existing session APIs. It requests `mode=combined`, `use_yoloe=true`, `use_qwen=true`, `use_locate=false`, `loop=true`, and `require_qwen=false`. Mode/schema checks prevent old servers silently running exit-only processing.
- Switching closes and joins the previous viewer job, stops the remembered backend session, waits for terminal status, then starts the replacement. It reuses an already-running matching session on reconnect. A persisted random tablet identifier also lets it recover its own session after an interrupted creation response; the backend reuses matching creation retries under its existing lock. Unrelated active sessions are not stopped.
- Generation guards reject results from superseded selections, configuration changes and backgrounded viewers. An additional attempt guard rejects callbacks from previous reconnect attempts. Picker requests have their own generation guard. Callbacks are serialized on the owning coroutine scope, so checking generation and updating UI state happen together.
- `BackendClient` (recorded source) owns one annotated MJPEG request and one event WebSocket. Closing/cancelling either viewer attempt releases both connections. Multipart JPEG size, headers and decoded dimensions are bounded; StateFlow retains the latest displayed bitmap. Annotated frames already contain boxes, so native overlays are not duplicated.
- `backend/combined.py` continues to feed the same clean source frame to the two existing pipelines and publishes their combined annotations and independent summaries. Both recorded and live sources use the same `CombinedMonitor.start_workers/step/live_result/shutdown` implementation; recorded mode adds a `LiveSource` reader and backend annotation. Qwen model discovery is performed once before constructing these pipelines. Non-strict sessions catch typed `QwenUnavailable`, disable Qwen on a copy of the pipeline arguments and construct both non-Qwen pipelines. `qwen_active`, `qwen_status` and `date_reading_enabled` describe effective assistance in state. `models.qwen_requested` retains the preference separately from effective `models.qwen`, so reconnecting a degraded session does not repeatedly replace it.
- Other API clients may still set `require_qwen=true` for strict combined sessions. This retains the server-side readiness check and fatal typed errors; the tablet uses non-strict sessions. Startup failure returns `detail.code=qwen_unavailable`; asynchronous initialization failure is exposed as `error_code=qwen_unavailable` in session info and WebSocket terminal state/end messages. Other errors stay distinct. `backend/qwen_status.py`, typed errors, `/api/info` diagnostics and Qwen configuration remain available.

- `CameraInput.kt` represents `RECORDED_VIDEO` and `TABLET_CAMERA` explicitly, holds capture generations and frame metadata, and provides a conflated one-frame mailbox. `DemoController` remains the single owner of source/session switching and retry behavior. Switching to camera stops only this tablet's remembered/owned recorded session. Switching away from camera cancels the live connection, invalidates pending frames/results, and releases CameraX.
- `TabletCameraView` binds rear-facing CameraX Preview and ImageAnalysis to the Activity lifecycle. They share a CameraX ViewPort. Analysis crops to `ImageProxy.cropRect`, applies the sensor/display rotation, scales and JPEG-encodes off the UI thread, then closes every ImageProxy. CameraX's `ImageProxyTransformFactory`/`CoordinateTransform` maps the rotated/cropped uploaded image into `PreviewView` pixels; pure `CameraProjection` maps normalized returned boxes. Results keep their originating frame's transform and viewport identity; boxes from old viewports or older than 2 seconds are hidden. The preview fills the screen using `FILL_CENTER` (cropping the edges to match the tablet aspect ratio). A compact source title and controls overlay the top; detection summaries overlay the bottom at their content height, with scrolling for longer lists. These overlays do not resize the camera viewport. System bars are hidden; swipe from an edge to reveal them temporarily. Recordings retain their original aspect ratio. There is no front-camera UI/mirroring shortcut.
- `LiveCameraClient` reuses `/api/live`, with a bearer **header** using the same encrypted token (no camera token in the URL). It requests `combined`, YOLOE on, LocateAnything off and optional Qwen. It sends the existing little-endian uint32 sequence + float64 source time + JPEG protocol, waits for the matching sequence result before sending again, and retains only the newest pending frame. Normalized combined results feed the same hazard/extinguisher summaries plus the preview overlay. Preview runs locally; no annotated MJPEG round trip is used for camera mode.
- `backend/live.py` adds the combined adapter to its existing frame ingestion infrastructure. The live slot remains occupied until pipeline shutdown finishes. Interrupted model initialization is joined/cleaned up on cancellation. Browser query-token authentication and existing individual live modes remain compatible. Camera snapshots are disabled in both detector pipelines: uploaded tablet images are not recorded/saved, although existing text/JSON session reports remain.

## Camera limits and developer checks

Camera inference settings are in `CameraTuning` (`data/CameraInput.kt`): maximum uploaded width **960 px**, JPEG quality **80**, maximum sampling rate **5 FPS**, overlay age limit **2 seconds**. The rear-camera preview runs independently at the camera's normal preview rate. There is one frame in flight plus one newest pending frame; older frames are dropped. CameraX also uses `STRATEGY_KEEP_ONLY_LATEST`, and network work/encoding never blocks the UI thread. Small/distant service tags may be unreadable at this conservative resolution; move closer for Qwen-assisted reading.

CameraX camera2/lifecycle/view **1.4.2** is pinned for the existing SDK/toolchain. Camera permission is requested only when choosing Tablet camera. The camera is explicitly unbound on stop/disposal, with one analyzer executor per active binding; return/recreation binds a fresh generation. Display rotation/layout changes rebuild the shared ViewPort. External USB cameras, Insta360, front-camera selection, local inference, camera recording and cloud storage are not included.

For a USB tablet, enable USB debugging, select the physical tablet in Android Studio and run Debug. Forward the backend port (replace the serial/port as needed):

```sh
adb -s TABLET_SERIAL reverse tcp:8100 tcp:8100
```

Then use `http://127.0.0.1:8100` in tablet Settings with the backend token. `10.0.2.2` is for the emulator only. Keep the backend running and use its printed port if it selects another. Restart the backend after updating this code so `/api/live` supports combined input.

For real-device QA: deny permission once and confirm recordings still work; grant it and check rear preview/results; switch video → camera → video → camera; background and confirm camera/live-session release; return and confirm one live session/analyzer; repeat without Qwen. Check overlay alignment while pointing at actual exits/extinguishers, including both landscape rotations. An emulator camera is useful for lifecycle checks but does not establish physical-camera alignment or detection quality.

Seven bundled offline clips and unused schematic/playback components remain for development. They are not part of the main demo UX or uploaded to the backend. Actual detection uses files in the backend video directory.

## Developer troubleshooting and validation limits

Restart the backend after updating its code. Qwen discovery checks the configured OpenAI-compatible `/models` response; it does not prove that vision inference succeeds. A reachable model service, readable close-up tags and real video processing are needed to verify date-reading quality.

A backend dedicated to this tablet avoids session conflicts. If the emulator or another tablet already has a demo running, the app explains that the backend is in use by another device. Stop that viewer and its backend session before retrying (backgrounding the viewer alone leaves processing running). If unrelated processing is already running, ask the administrator to resolve it; the tablet does not expose session IDs or stop unrelated sessions. Video switching has no rollback if replacement creation fails. Interrupted creation requests are recovered using the tablet identifier on the next attempt. Use the updated backend for this recovery support.

Backend regression commands:

```sh
.venv-backend/bin/python -m unittest discover -s tests -p 'test_*sessions.py'
.venv-backend/bin/python -m unittest discover -s tests -p test_demo_qwen.py
.venv-backend/bin/python -m unittest discover -s tests -p test_live_combined.py
.venv-backend/bin/python tests/test_core.py
```

JVM coverage includes setup, persistence/secure-token-store delegation, selection/startup, combined requests with preferred/optional Qwen, error classification, switching/stale callbacks, reconnect attempts and background cleanup. Actual Keystore encryption requires Android; JVM storage tests verify delegation and the absence of plaintext credentials in ordinary preferences.

Real Qwen end-to-end verification must check combined annotations, extinguisher date readings/confirmation, switching videos, background/return, connection recovery, and loss/restoration of Qwen during startup, including non-strict degradation and strict failure. A service readiness check or mocked response is not end-to-end model validation.

## Validation — selectable sources

```sh
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
  android-app/gradlew -p android-app testDebugUnitTest assembleDebug assembleDebugAndroidTest lintDebug
.venv-backend/bin/python -m unittest discover -s tests -p 'test_*sessions.py'
.venv-backend/bin/python -m unittest discover -s tests -p test_demo_qwen.py
.venv-backend/bin/python -m unittest discover -s tests -p test_live_combined.py
.venv-backend/bin/python tests/test_core.py
```

JVM validation covers source selection, permission denial/grant, camera initialization/live/backend failures, both switching directions, stale callbacks, one-frame backpressure, projection rotation/letterboxing/mirroring math, camera reconnect/background/return, recorded-session release and token/header authentication. MockWebServer checks a real OkHttp WebSocket: one upload stays in flight and the next upload is the newest pending frame, with matching sequence IDs. Existing recording/persistence/token tests remain.

Backend tests cover shared combined pipelines with deterministic detector inputs, normalized boxes, unknown/unverified Qwen fields, optional/strict Qwen contracts, live bearer authentication, disconnect/startup-cancellation cleanup, and retaining the session slot through shutdown. They also verify tablet-camera processing writes no JPEG snapshots. Mocked Qwen availability checks both assistance constructors; actual Qwen inference/date-reading quality is not claimed.


On-device CameraX probe (grant camera permission through the app and select a recorded source first, so the probe owns the camera):

```sh
adb -s TABLET_SERIAL install -r android-app/app/build/outputs/apk/debug/app-debug.apk
adb -s TABLET_SERIAL install -r android-app/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb -s TABLET_SERIAL shell am instrument -w -r \
  -e class sg.htx.tablet.CameraBindingTest \
  sg.htx.tablet.test/androidx.test.runner.AndroidJUnitRunner
```

Results on **23 October 2026**: **59 JVM tests passed**, zero failures/errors/skips; Android debug and instrumentation APK builds passed; lint **0 errors, 15 warnings and 2 informational findings** (pinned older dependencies/SDK, landscape/backup/icon configuration and state-boxing suggestions). Backend checks passed: **9 session regression tests, 12 optional/strict Qwen tests, 6 combined-live/auth/cleanup tests and 13 core checks**. The physical TB310XU CameraX instrumentation test passed (**1 test**, not skipped), covering usable rear RGBA frames, rotation/transform metadata and actual camera closure on lifecycle stop.

Physical tablet validation used the real rear camera and the Mac backend over USB forwarding, with Qwen absent. Verified source selection, permission-granted capture, missing/revoked permission with nonfatal UI and continued recorded-video access, rear camera ID 0 opening, live combined frames/results, the non-blocking date-verification warning, camera/live-session release on background, one fresh live session on return, and repeated camera → recorded video → camera → recorded video switching. Recorded `IMG_0453.MOV`/`IMG_0454.MOV` playback continued through the existing MJPEG/status path. The tablet was left on its original recording, with camera capture closed. Android UI automation needed fresh/compressed snapshots while switching dialogs; early timing assertions were rechecked after Android settled.

No emulator-camera results are claimed for this change. No real Qwen service/inference was used. Visual alignment and detection quality while aiming the tablet at particular real-world exits/extinguishers, including both landscape rotations and Qwen tag reading, still need an operator with suitable subjects/service; the automated transform tests do not establish scene-level model accuracy.
