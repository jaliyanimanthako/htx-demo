package sg.htx.tablet.ui

import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import sg.htx.tablet.data.*

@Composable
fun DetectionOverlay(result: LiveResult, width: Int, height: Int, modifier: Modifier = Modifier) {
    val paint = remember { Paint(Paint.ANTI_ALIAS_FLAG).apply { color = android.graphics.Color.WHITE; isFakeBoldText = true } }
    Canvas(modifier) {
        paint.textSize = 14f * density
        fun box(raw: BoundingBox, label: String, color: Color) {
            val b = transformBox(raw, width.toFloat(), height.toFloat(), size.width, size.height)
            if (b.right <= b.left || b.bottom <= b.top) return
            drawRect(color, Offset(b.left, b.top), Size(b.right - b.left, b.bottom - b.top), style = Stroke(2.5f * density))
            val textWidth = paint.measureText(label)
            val x = b.left.coerceAtMost((size.width - textWidth - 12).coerceAtLeast(0f))
            val y = (b.top - 4).coerceAtLeast(paint.textSize + 8)
            drawRect(Color.Black.copy(alpha = 0.85f), Offset(x, y - paint.textSize - 4), Size(textWidth + 12, paint.textSize + 10))
            drawContext.canvas.nativeCanvas.drawText(label, x + 6, y, paint)
        }
        result.exits.forEach {
            box(it.box, "EXIT · ${it.statusText}", annotationColor(it.status))
        }
        result.obstacles.forEach { box(it.box, it.label, annotationColor("obstacle")) }
    }
}
