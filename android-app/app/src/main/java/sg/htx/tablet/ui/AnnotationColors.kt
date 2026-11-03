package sg.htx.tablet.ui

import androidx.compose.ui.graphics.Color

/** Same status palette as the backend's recorded-video annotations. */
fun annotationColor(status: String): Color = when (status) {
    "clear", "valid" -> Color(0xFF00C800)
    "partially_obstructed", "expiring_soon" -> Color(0xFFFFC800)
    "obstructed", "expired", "obstacle" -> Color(0xFFFF0000)
    else -> Color(0xFFA0A0A0)
}
