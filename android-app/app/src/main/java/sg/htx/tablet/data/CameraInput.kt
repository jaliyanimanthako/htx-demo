package sg.htx.tablet.data

import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ReceiveChannel

/** Limits live inference traffic; preview itself is not throttled. */
object CameraTuning {
    const val WIDTH = 960
    const val JPEG_QUALITY = 80
    const val MAX_FPS = 5
    const val RESULT_MAX_AGE_MS = 2000L
}
enum class InputSource { RECORDED_VIDEO, TABLET_CAMERA, EXTERNAL_CAMERA }
data class CameraCapture(val enabled: Boolean = false, val generation: Long = 0, val permissionGranted: Boolean = false)
/** Three projected corners of the rotated/cropped image in PreviewView pixels. */
data class CameraProjection(val originX: Float, val originY: Float, val edgeXx: Float, val edgeXy: Float,
    val edgeYx: Float, val edgeYy: Float) {
    fun map(box: BoundingBox): BoundingBox {
        val points = listOf(box.left to box.top, box.right to box.top, box.right to box.bottom, box.left to box.bottom).map { (x,y) ->
            (originX + x * (edgeXx-originX) + y * (edgeYx-originX)) to
                (originY + x * (edgeXy-originY) + y * (edgeYy-originY))
        }
        return BoundingBox(points.minOf { it.first }, points.minOf { it.second }, points.maxOf { it.first }, points.maxOf { it.second })
    }
}
data class CameraFrame(val jpeg: ByteArray, val capturedAtNanos: Long, val projection: CameraProjection,
    val viewport: Long = 0)
data class CameraBox(val box: BoundingBox, val label: String, val status: String)
data class CameraResult(val seq: Long, val frame: CameraFrame, val state: SessionState, val boxes: List<CameraBox>)
/** Exactly one newest pending frame; the live client owns the single in-flight frame. */
class CameraFrameBuffer {
    private val channel = Channel<CameraFrame>(Channel.CONFLATED)
    val frames: ReceiveChannel<CameraFrame> get() = channel
    fun offer(frame: CameraFrame) { channel.trySend(frame) }
    fun close() { channel.cancel() }
}
