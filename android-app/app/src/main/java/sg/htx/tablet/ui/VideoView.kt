package sg.htx.tablet.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import sg.htx.tablet.viewmodel.FireDetectionViewModel

/** Only this subtree observes frames; the header/settings do not recompose at video FPS. */
@Composable
fun VideoView(model: FireDetectionViewModel, modifier: Modifier = Modifier) {
    val bitmap by model.frame.collectAsStateWithLifecycle()
    val detections by model.detections.collectAsStateWithLifecycle()
    Box(modifier.background(Color.Black)) {
        bitmap?.let { frame ->
            val image = remember(frame) { frame.asImageBitmap() }
            Image(image, "Camera feed", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
            detections?.let { DetectionOverlay(it, frame.width, frame.height, Modifier.fillMaxSize()) }
        }
    }
}
