package sg.htx.tablet.ui

import sg.htx.tablet.data.BoundingBox
import kotlin.math.max
import kotlin.math.min

enum class ScaleMode { FIT, CROP }
/** Maps normalized XYXY to a centered video viewport; clips cropped boxes to the viewport. */
fun transformBox(box: BoundingBox, sourceWidth: Float, sourceHeight: Float,
                 displayWidth: Float, displayHeight: Float, mode: ScaleMode = ScaleMode.FIT): BoundingBox {
    require(listOf(sourceWidth, sourceHeight, displayWidth, displayHeight).all { it.isFinite() && it > 0 })
    require(listOf(box.left, box.top, box.right, box.bottom).all { it.isFinite() })
    require(box.left <= box.right && box.top <= box.bottom)
    val scale = if (mode == ScaleMode.FIT) min(displayWidth / sourceWidth, displayHeight / sourceHeight)
                else max(displayWidth / sourceWidth, displayHeight / sourceHeight)
    val width = sourceWidth * scale
    val height = sourceHeight * scale
    val dx = (displayWidth - width) / 2
    val dy = (displayHeight - height) / 2
    return BoundingBox(
        (dx + box.left.coerceIn(0f, 1f) * width).coerceIn(0f, displayWidth),
        (dy + box.top.coerceIn(0f, 1f) * height).coerceIn(0f, displayHeight),
        (dx + box.right.coerceIn(0f, 1f) * width).coerceIn(0f, displayWidth),
        (dy + box.bottom.coerceIn(0f, 1f) * height).coerceIn(0f, displayHeight))
}
