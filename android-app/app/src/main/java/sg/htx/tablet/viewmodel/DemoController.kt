package sg.htx.tablet.viewmodel

import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.channels.Channel
import sg.htx.tablet.data.*
import kotlinx.coroutines.channels.ReceiveChannel
import sg.htx.tablet.data.SessionExtinguisher
import sg.htx.tablet.data.SessionState
import java.io.IOException
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

fun validBackendUrl(base: String): Boolean {
    val url = base.trim().toHttpUrlOrNull() ?: return false
    return url.encodedPath == "/" && url.query == null && url.fragment == null && url.username.isEmpty() && url.password.isEmpty()
}

class DemoFailure(val kind: Kind) : IOException() {
    enum class Kind { QWEN, BACKEND, AUTH, START, CONNECTION, BUSY, IN_USE, LIVE_FAILED }
}
enum class Connection(val label: String) {
    IDLE("Ready"), CONNECTING("Starting demo…"), CONNECTED("Running"),
    RECONNECTING("Reconnecting…"),
    BACKEND_UNAVAILABLE("Backend unavailable"), FAILED("Could not start demo"),
    CAMERA_PERMISSION("Camera permission required"), CAMERA_FAILED("Camera initialization failed"), LIVE_FAILED("Live session failed")
}
data class MonitorState(val connection: Connection = Connection.IDLE,
    val videoAvailable: Boolean = false, val hazards: Int? = null, val checking: Boolean = false,
    val detail: String = "Choose a source to start the demo.", val mode: String = "combined",
    val dateReadingEnabled: Boolean = false, val extinguishers: List<SessionExtinguisher> = emptyList(),
    val qwenActive: Boolean? = null) {
    val assistanceMessage: String get() = when (qwenActive) {
        false -> "Qwen assistance unavailable — date verification is disabled. Detection continues normally."
        true -> "Qwen assistance active"
        null -> ""
    }
}
data class Settings(val base: String = "http://10.0.2.2:8100", val session: String = "", val token: String = "",
    val configured: Boolean = false, val video: String = "", val source: InputSource = InputSource.RECORDED_VIDEO)
interface DemoSettingsStore {
    fun load(): Settings
    fun save(settings: Settings)
}
interface CameraDemoClient {
    suspend fun monitor(base: String, token: String, frames: ReceiveChannel<CameraFrame>,
        result: (CameraResult) -> Unit, connected: () -> Unit)
}
interface DemoSessions {
    suspend fun prepareCamera(base: String, token: String, currentId: String) {}
    suspend fun requireQwen(base: String, token: String)
    suspend fun videos(base: String, token: String): List<String>
    suspend fun startDemo(base: String, token: String, currentId: String, source: String, progress: (String) -> Unit): String
}
interface DemoViewer<T> {
    suspend fun monitor(base: String, session: String, token: String, frame: (T) -> Unit,
                        state: (SessionState) -> Unit, connected: () -> Unit)
}

/** Existing session/viewer lifecycle, independent of Android so races can be tested. */
class DemoController<T>(private val scope: CoroutineScope, private val store: DemoSettingsStore,
    private val sessions: DemoSessions, private val client: DemoViewer<T>, private val cameraClient: CameraDemoClient? = null) {
    var settings = store.load()
        private set
    data class VideoPicker(val open: Boolean = false, val busy: Boolean = false,
        val videos: List<String> = emptyList(), val message: String = "", val selected: String = "")
    private val _picker = MutableStateFlow(VideoPicker(selected = settings.video))
    val picker = _picker.asStateFlow()
    private val _state = MutableStateFlow(MonitorState())
    val state = _state.asStateFlow()
    private val _frame = MutableStateFlow<T?>(null)
    val frame = _frame.asStateFlow()
    private val _source = MutableStateFlow(settings.source)
    val source = _source.asStateFlow()
    private val _sourcePicker = MutableStateFlow(false)
    val sourcePicker = _sourcePicker.asStateFlow()
    private val _capture = MutableStateFlow(CameraCapture())
    val capture = _capture.asStateFlow()
    private val _cameraResult = MutableStateFlow<CameraResult?>(null)
    val cameraResult = _cameraResult.asStateFlow()
    private var cameraPermission = false
    @Volatile private var pendingCamera: CameraFrameBuffer? = null
    private var job: Job? = null
    private var listing: Job? = null
    @Volatile private var active = false
    @Volatile private var generation = 0L
    private var pickerGeneration = 0L
    private var attemptGeneration = 0L
    private data class FrameUpdate<T>(val generation: Long, val attempt: Long, val value: T)
    private val frames = Channel<FrameUpdate<T>>(Channel.CONFLATED)
    init {
        scope.launch {
            for (update in frames) {
                if (active && update.generation == generation && update.attempt == attemptGeneration) {
                    _frame.value = update.value
                    _state.update { it.copy(videoAvailable = true) }
                }
            }
        }
    }
    // All callbacks, including OkHttp callbacks, are serialized on the owning scope.
    private fun accept(id: Long, attempt: Long, action: () -> Unit) { scope.launch { if (active && id == generation && attempt == attemptGeneration) action() } }
    private fun failure(e: Exception, reconnecting: Boolean = false): MonitorState = when ((e as? DemoFailure)?.kind) {
        DemoFailure.Kind.LIVE_FAILED -> MonitorState(Connection.LIVE_FAILED, detail = "Live monitoring could not start. Check the backend and try again.")
        DemoFailure.Kind.QWEN -> MonitorState(Connection.FAILED, detail = "Update and restart the backend to enable detection without Qwen.")
        DemoFailure.Kind.BUSY -> MonitorState(Connection.CONNECTING, detail = "Waiting for previous processing to finish. Retrying automatically.")
        DemoFailure.Kind.IN_USE -> MonitorState(Connection.FAILED, detail = "The backend is running a demo on another device. Stop that demo, then retry.")
        DemoFailure.Kind.AUTH -> MonitorState(Connection.FAILED, detail = "Check the backend token in Settings.")
        DemoFailure.Kind.START -> MonitorState(Connection.FAILED, detail = "The demo could not start. Try another video or ask the demo administrator.")
        else -> if (e is IOException) MonitorState(Connection.BACKEND_UNAVAILABLE, detail = "Cannot reach the backend. Check the connection and Settings. Retrying automatically.")
            else MonitorState(if (reconnecting) Connection.RECONNECTING else Connection.FAILED,
                detail = if (reconnecting) "Connection lost. Retrying automatically." else "The demo could not start. Please try again.")
    }
    fun showSources() { if (settings.configured) _sourcePicker.value = true }
    fun dismissSources() { _sourcePicker.value = false }
    fun chooseRecorded() { dismissSources(); showVideoDemo() }
    fun chooseCamera(permissionGranted: Boolean) {
        dismissSources(); dismissVideos()
        settings = settings.copy(source = InputSource.TABLET_CAMERA)
        store.save(settings); _source.value = settings.source
        cameraPermission = permissionGranted
        restart()
    }
    fun cameraPermission(granted: Boolean) {
        cameraPermission = granted
        _capture.update { it.copy(permissionGranted = granted) }
        if (active && settings.source == InputSource.TABLET_CAMERA) restart()
    }
    fun cameraFailed(token: Long) {
        if (!active || token != generation || settings.source != InputSource.TABLET_CAMERA) return
        generation++; job?.cancel(); pendingCamera?.close(); pendingCamera = null
        _capture.value = CameraCapture(permissionGranted = cameraPermission)
        _cameraResult.value = null
        _state.value = MonitorState(Connection.CAMERA_FAILED, detail = "The rear camera could not open. Retry, check camera access in Android Settings, or choose Pre-recorded video.")
    }
    fun cameraOpened(token: Long) { if (active && token == generation) _state.update { it.copy(videoAvailable = true) } }
    fun cameraFrame(token: Long, frame: CameraFrame) {
        val buffer = pendingCamera
        if (active && token == generation && settings.source == InputSource.TABLET_CAMERA) buffer?.offer(frame)
    }
    fun dismissVideos() { pickerGeneration++; listing?.cancel(); _picker.update { it.copy(open = false, busy = false) } }
    fun showVideoDemo() {
        if (!settings.configured) return
        listing?.cancel()
        val id = ++pickerGeneration
        val config = settings
        _picker.update { it.copy(open = true, busy = true, videos = emptyList(), message = "Checking demo availability…") }
        listing = scope.launch {
            try {
                val videos = sessions.videos(config.base, config.token)
                if (id == pickerGeneration) _picker.update { it.copy(busy = false, videos = videos,
                    message = if (videos.isEmpty()) "No demo videos are available. Ask the demo administrator." else "Choose a video.") }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                if (id == pickerGeneration) {
                    val error = failure(e)
                    _picker.update { it.copy(busy = false, message = "${error.connection.label}. ${error.detail}") }
                    if (_state.value.connection != Connection.CONNECTED) _state.value = error
                }
            }
        }
    }
    fun saveSettings(base: String, token: String) {
        require(validBackendUrl(base))
        dismissVideos()
        generation++; job?.cancel(); _frame.value = null
        pendingCamera?.close(); pendingCamera = null; _capture.value = CameraCapture(permissionGranted = cameraPermission); _cameraResult.value = null
        val sameHost = base.trim() == settings.base
        settings = Settings(base.trim(), if (sameHost) settings.session else "", token, true,
            if (sameHost) settings.video else "", if (sameHost) settings.source else InputSource.RECORDED_VIDEO)
        store.save(settings)
        _source.value = settings.source
        _picker.value = VideoPicker(selected = settings.video)
        _state.value = MonitorState()
        // Setup lands on Ready; the operator explicitly starts or resumes the demo.
    }
    fun detectVideo(source: String) {
        dismissVideos()
        settings = settings.copy(video = source, source = InputSource.RECORDED_VIDEO)
        _source.value = settings.source
        store.save(settings)
        _picker.update { it.copy(selected = source) }
        restart()
    }
    fun foreground() { active = true; if (settings.configured && (settings.video.isNotBlank() || settings.source == InputSource.TABLET_CAMERA)) restart() }
    fun background() {
        active = false; generation++; job?.cancel(); dismissVideos(); dismissSources()
        pendingCamera?.close(); pendingCamera = null
        _capture.value = CameraCapture(permissionGranted = cameraPermission)
        _cameraResult.value = null; _frame.value = null; _state.value = MonitorState()
    }
    fun retry() { if (settings.video.isBlank() && settings.source != InputSource.TABLET_CAMERA) showSources() else restart() }
    private fun restart() {
        if (!active || !settings.configured) return
        val current = ++generation
        val old = job
        old?.cancel()
        _frame.value = null
        pendingCamera?.close(); pendingCamera = null; _cameraResult.value = null
        val camera = settings.source == InputSource.TABLET_CAMERA
        _capture.value = CameraCapture(camera && cameraPermission, current, cameraPermission)
        if (camera && !cameraPermission) {
            _state.value = MonitorState(Connection.CAMERA_PERMISSION, detail = "Camera permission is required to use Tablet camera. Grant permission or choose Pre-recorded video.")
            return
        }
        _state.value = MonitorState(Connection.CONNECTING, detail = if (camera) "Starting live monitoring…" else "Preparing the selected video…")
        job = scope.launch {
            withContext(NonCancellable) { old?.join() } // cancellation still waits for preceding cleanup
            ensureActive()
            var retrySeconds = 2L
            var hasRun = false
            while (isActive && current == generation) {
                if (hasRun) _state.value = MonitorState(Connection.RECONNECTING, detail = "Restoring the demo connection…")
                val attempt = ++attemptGeneration
                try {
                    val config = settings
                    if (camera) {
                        sessions.prepareCamera(config.base, config.token, config.session)
                        ensureActive()
                        val buffer = CameraFrameBuffer()
                        pendingCamera = buffer
                        try {
                            (cameraClient ?: throw DemoFailure(DemoFailure.Kind.LIVE_FAILED)).monitor(config.base, config.token, buffer.frames,
                                result = { result -> accept(current, attempt) {
                                    _cameraResult.value = result
                                    _state.update { it.copy(videoAvailable = true, hazards = result.state.hazardCount, checking = result.state.checking,
                                        dateReadingEnabled = result.state.dateReadingEnabled, extinguishers = result.state.extinguishers,
                                        qwenActive = result.state.qwenActive) }
                                } }, connected = { accept(current, attempt) {
                                    hasRun = true; retrySeconds = 2L
                                    _state.update { it.copy(connection = Connection.CONNECTED, detail = "Live exit and extinguisher monitoring") }
                                } })
                        } finally {
                            if (pendingCamera === buffer) pendingCamera = null
                            buffer.close()
                        }
                        throw DemoFailure(DemoFailure.Kind.CONNECTION)
                    }
                    val id = sessions.startDemo(config.base, config.token, config.session, config.video) { message ->
                        accept(current, attempt) { _state.update { it.copy(detail = message) } }
                    }
                    ensureActive()
                    if (current != generation) return@launch
                    settings = config.copy(session = id)
                    store.save(settings)
                    client.monitor(config.base, id, config.token,
                        frame = { bitmap -> frames.trySend(FrameUpdate(current, attempt, bitmap)) },
                        state = { result -> accept(current, attempt) { _state.update { it.copy(
                            hazards = if (result.videoTimeS != null) result.hazardCount else null, checking = result.checking,
                            dateReadingEnabled = result.dateReadingEnabled, extinguishers = result.extinguishers, qwenActive = result.qwenActive) } } },
                        connected = { accept(current, attempt) {
                            hasRun = true; retrySeconds = 2L
                            _state.update { it.copy(connection = Connection.CONNECTED, detail = "Exit and extinguisher monitoring") }
                        } })
                    throw DemoFailure(DemoFailure.Kind.CONNECTION)
                } catch (e: CancellationException) { throw e }
                catch (e: Exception) {
                    if (current != generation || !active) return@launch
                    attemptGeneration++
                    _frame.value = null; _cameraResult.value = null
                    val error = failure(e, hasRun)
                    _state.value = if (hasRun && error.connection == Connection.BACKEND_UNAVAILABLE &&
                        (e as? DemoFailure)?.kind == DemoFailure.Kind.CONNECTION)
                        MonitorState(Connection.RECONNECTING, detail = "Connection lost. Retrying automatically.") else error
                    if (error.connection in setOf(Connection.FAILED, Connection.LIVE_FAILED)) return@launch
                }
                delay(retrySeconds * 1000)
                retrySeconds = (retrySeconds * 2).coerceAtMost(30)
            }
        }
    }
}
