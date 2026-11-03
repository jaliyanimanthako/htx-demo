package sg.htx.tablet.viewmodel

import android.Manifest
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import android.app.Application
import sg.htx.tablet.data.CameraFrame
import sg.htx.tablet.network.LiveCameraClient
import android.graphics.Bitmap
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import sg.htx.tablet.data.DemoPreferences
import sg.htx.tablet.data.LiveResult
import sg.htx.tablet.network.BackendClient
import sg.htx.tablet.network.SessionController
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

class FireDetectionViewModel(app: Application) : AndroidViewModel(app) {
    private val preferences = DemoPreferences(app.getSharedPreferences("backend", 0))
    private val demo = DemoController<Bitmap>(viewModelScope, preferences, SessionController(demoClientId = preferences.clientId), BackendClient(), LiveCameraClient())
    val source = demo.source
    val sourcePicker = demo.sourcePicker
    val cameraCapture = demo.capture
    val cameraResult = demo.cameraResult
    fun showSources() = demo.showSources()
    fun dismissSources() = demo.dismissSources()
    fun chooseExternal() = demo.chooseExternal()
    fun chooseRecorded() = demo.chooseRecorded()
    fun chooseCamera(granted: Boolean) = demo.chooseCamera(granted)
    fun cameraPermission(granted: Boolean) = demo.cameraPermission(granted)
    fun cameraFailed(token: Long) = demo.cameraFailed(token)
    fun cameraOpened(token: Long) = demo.cameraOpened(token)
    fun cameraFrame(token: Long, frame: CameraFrame) = demo.cameraFrame(token, frame)
    val settings get() = demo.settings
    val state = demo.state
    val picker = demo.picker
    val frame = demo.frame
    val detections = MutableStateFlow<LiveResult?>(null).asStateFlow()
    fun dismissVideos() = demo.dismissVideos()
    fun showVideoDemo() = demo.showVideoDemo()
    fun saveSettings(base: String, token: String) = demo.saveSettings(base, token)
    fun detectVideo(source: String) = demo.detectVideo(source)
    fun foreground() {
        demo.cameraPermission(ContextCompat.checkSelfPermission(getApplication(), Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
        demo.foreground()
    }
    fun background() = demo.background()
    fun retry() = demo.retry()
}
