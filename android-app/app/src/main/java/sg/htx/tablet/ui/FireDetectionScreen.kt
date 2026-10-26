package sg.htx.tablet.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings as AndroidSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.ui.platform.LocalContext
import sg.htx.tablet.data.InputSource

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import sg.htx.tablet.viewmodel.*

@Composable
fun FireDetectionScreen(model: FireDetectionViewModel) {
    val state by model.state.collectAsStateWithLifecycle()
    val picker by model.picker.collectAsStateWithLifecycle()
    val source by model.source.collectAsStateWithLifecycle()
    val sourcesOpen by model.sourcePicker.collectAsStateWithLifecycle()
    val capture by model.cameraCapture.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { model.cameraPermission(it) }
    fun chooseCamera() {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        model.chooseCamera(granted)
        if (!granted) permission.launch(Manifest.permission.CAMERA)
    }
    var settingsOpen by rememberSaveable { mutableStateOf(!model.settings.configured) }
    val running = state.connection == Connection.CONNECTED && state.videoAvailable
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        // Controls and summaries overlay the feed, keeping its viewport full size and stable.
        if (source == InputSource.TABLET_CAMERA) {
            if (capture.enabled) TabletCameraView(model, capture, Modifier.fillMaxSize())
        } else VideoView(model, Modifier.fillMaxSize())
        Column(Modifier.fillMaxSize().safeDrawingPadding()) {
            Row(
                Modifier.fillMaxWidth().background(Color.Black.copy(alpha = 0.72f)).padding(horizontal = 12.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(Modifier.weight(1f)) {
                    Text(if (source == InputSource.TABLET_CAMERA) "Tablet camera" else "Pre-recorded videos",
                        style = MaterialTheme.typography.titleLarge)
                    if (source == InputSource.RECORDED_VIDEO && picker.selected.isNotEmpty())
                        Text(picker.selected, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                }
                Text(if (state.connection == Connection.CONNECTED && !state.videoAvailable) "Starting demo…" else state.connection.label,
                    color = if (state.connection == Connection.CONNECTED) Color(0xFF80DFB1) else Color(0xFFFFB454))
                Spacer(Modifier.width(12.dp))
                Button(onClick = { if (model.settings.configured) model.showSources() else settingsOpen = true }) { Text(if (picker.selected.isBlank() && source != InputSource.TABLET_CAMERA) "Start demo" else "Change source") }
                Spacer(Modifier.width(8.dp))
                OutlinedButton(onClick = { settingsOpen = true }) { Text("Settings") }
            }
            Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                if (state.connection != Connection.CONNECTED || !state.videoAvailable) {
                    Column(Modifier.background(Color.Black.copy(alpha = 0.9f)).verticalScroll(rememberScrollState()).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        if (state.connection in setOf(Connection.CONNECTING, Connection.RECONNECTING)) CircularProgressIndicator()
                        Text(if (state.connection == Connection.CONNECTED) "Starting demo…" else state.connection.label,
                            style = MaterialTheme.typography.titleLarge)
                        Text(state.detail)
                        if (state.connection == Connection.CAMERA_PERMISSION) {
                            TextButton(onClick = { permission.launch(Manifest.permission.CAMERA) }) { Text("Grant camera permission") }
                            TextButton(onClick = { context.startActivity(Intent(AndroidSettings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))) }) { Text("Open app settings") }
                            TextButton(onClick = { model.showSources() }) { Text("Choose another source") }
                        } else if (state.connection != Connection.CONNECTING) TextButton(onClick = { if (!model.settings.configured) settingsOpen = true else model.retry() }) {
                            Text(if (state.connection == Connection.IDLE) "Start demo" else "Retry now")
                        }
                    }
                }
            }
            if (running) {
                Column(Modifier.fillMaxWidth().background(Color.Black.copy(alpha = 0.72f))
                    .heightIn(max = 160.dp).verticalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp, vertical = 6.dp)) {
                    MonitoringSummary(state)
                }
            }
        }
    }
    if (sourcesOpen) AlertDialog(onDismissRequest = { model.dismissSources() }, title = { Text("Choose source") }, text = {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = { model.chooseRecorded() }, modifier = Modifier.fillMaxWidth()) { Text("Pre-recorded video") }
            Text("Use one of the backend demo videos.", style = MaterialTheme.typography.bodySmall)
            OutlinedButton(onClick = { chooseCamera() }, modifier = Modifier.fillMaxWidth()) { Text("Tablet camera") }
            Text("Use the rear camera for live monitoring.", style = MaterialTheme.typography.bodySmall)
        }
    }, confirmButton = { TextButton(onClick = { model.dismissSources() }) { Text("Cancel") } })
    if (picker.open) AlertDialog(onDismissRequest = { model.dismissVideos() }, title = { Text("Choose demo video") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Exit obstruction and extinguisher monitoring. Date verification is included when assistance is available.")
            Text(picker.message)
            if (picker.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            picker.videos.forEach { source ->
                OutlinedButton(enabled = !picker.busy, onClick = { model.detectVideo(source) }, modifier = Modifier.fillMaxWidth()) { Text(source) }
            }
        }
    }, confirmButton = { TextButton(enabled = !picker.busy, onClick = { model.showVideoDemo() }) { Text("Retry") } },
        dismissButton = { Row {
            TextButton(onClick = { model.dismissVideos(); settingsOpen = true }) { Text("Settings") }
            TextButton(onClick = { model.dismissVideos() }) { Text("Close") }
        } })
    if (settingsOpen) {
        var base by remember { mutableStateOf(model.settings.base) }
        var token by remember { mutableStateOf("") }
        var clearToken by remember { mutableStateOf(false) }
        var validation by remember { mutableStateOf("") }
        AlertDialog(onDismissRequest = { settingsOpen = false }, title = { Text("Demo setup") }, text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Enter the backend address and token supplied by your demo administrator.")
                OutlinedTextField(base, { base = it }, label = { Text("Backend URL") }, singleLine = true)
                OutlinedTextField(token, { token = it }, label = { Text("Backend token") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
                Text(if (model.settings.token.isNotBlank()) "A token is securely saved. Leave the field blank to keep it." else "The token is stored encrypted on this device.")
                if (model.settings.token.isNotBlank()) Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(clearToken, { clearToken = it }); Text("Clear saved token")
                }
                if (validation.isNotBlank()) Text(validation, color = MaterialTheme.colorScheme.error)
            }
        }, confirmButton = { TextButton(onClick = {
            if (!validBackendUrl(base)) validation = "Enter an HTTP or HTTPS backend address without a path or credentials."
            else {
                model.saveSettings(base, if (clearToken) "" else if (token.isBlank() && base.trim() == model.settings.base) model.settings.token else token)
                settingsOpen = false
            }
        }) { Text("Save") } },
            dismissButton = { TextButton(onClick = { settingsOpen = false }) { Text("Cancel") } })
    }
}


@Composable
private fun MonitoringSummary(state: MonitorState) {
    Column {
            Text(when {
                state.hazards == null -> "Waiting for detection results"
                state.hazards > 0 -> "${state.hazards} exit hazards detected"
                state.checking -> "Checking exits • no confirmed hazards yet"
                else -> "No exit hazards detected"
            }, style = MaterialTheme.typography.titleMedium)
            if (state.assistanceMessage.isNotBlank()) Text(state.assistanceMessage,
                color = if (state.qwenActive == false) Color(0xFFFFB454) else MaterialTheme.colorScheme.onSurface)
            Text("${state.extinguishers.size} extinguishers visible • " + if (!state.dateReadingEnabled) "Dates not verified" else
                "${state.extinguishers.count { it.status == "expired" }} expired / overdue • ${state.extinguishers.count { it.status == "expiring_soon" }} due soon")
            Column {
                state.extinguishers.forEach { item ->
                    Text("Extinguisher #${item.id} · ${item.statusText} · ${item.dueText ?: "No date read"}" +
                        if (item.confirmed) " · confirmed" else " · unconfirmed", style = MaterialTheme.typography.bodySmall)
                }
            }
    }
}
