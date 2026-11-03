package sg.htx.tablet.network

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.selects.select
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import sg.htx.tablet.data.*
import sg.htx.tablet.viewmodel.*
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit

fun cameraPacket(seq: Int, time: Double, jpeg: ByteArray): ByteArray =
    ByteBuffer.allocate(12 + jpeg.size).order(ByteOrder.LITTLE_ENDIAN).putInt(seq).putDouble(time).put(jpeg).array()

/** Existing /api/live transport: one frame in flight, one newest pending frame. */
class LiveCameraClient(private val http: OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(8, TimeUnit.SECONDS).readTimeout(0, TimeUnit.SECONDS).pingInterval(5, TimeUnit.SECONDS).build()) : CameraDemoClient {
    override suspend fun monitor(base: String, token: String, frames: ReceiveChannel<CameraFrame>,
        result: (CameraResult) -> Unit, connected: () -> Unit) {
        require(validBackendUrl(base))
        val url = base.toHttpUrl().newBuilder().addPathSegments("api/live").build()
        val ready = CompletableDeferred<Unit>()
        val failed = CompletableDeferred<DemoFailure>()
        val closed = CompletableDeferred<Unit>()
        val replies = Channel<JSONObject>(1)
        var hasOpened = false
        val socket = http.newWebSocket(Request.Builder().url(url)
            .apply { if (token.isNotBlank()) header("Authorization", "Bearer $token") }.build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                hasOpened = true
                webSocket.send(JSONObject().put("type", "start").put("mode", "combined")
                    .put("use_yoloe", true).put("use_locate", false).put("use_qwen", true).toString())
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val j = JSONObject(text)
                    when (j.getString("type")) {
                        "ready" -> { check(j.getString("mode") == "combined"); ready.complete(Unit) }
                        "result" -> if (!replies.trySend(j).isSuccess) failed.complete(DemoFailure(DemoFailure.Kind.LIVE_FAILED))
                        "error" -> failed.complete(DemoFailure(if (j.optString("code") == "backend_in_use") DemoFailure.Kind.IN_USE else DemoFailure.Kind.LIVE_FAILED))
                    }
                } catch (e: Exception) { failed.complete(DemoFailure(DemoFailure.Kind.LIVE_FAILED)) }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                failed.complete(DemoFailure(if (response?.code in setOf(401,403)) DemoFailure.Kind.AUTH
                    else if (hasOpened) DemoFailure.Kind.CONNECTION else DemoFailure.Kind.BACKEND))
                closed.complete(Unit)
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, null) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                failed.complete(DemoFailure(if (code == 4401) DemoFailure.Kind.AUTH else DemoFailure.Kind.CONNECTION))
                closed.complete(Unit)
            }
        })
        suspend fun <T> waiting(timeout: Long, block: suspend () -> T): T = try { withTimeout(timeout) { block() } }
            catch (e: TimeoutCancellationException) { throw DemoFailure(DemoFailure.Kind.CONNECTION) }
        try {
            waiting(90_000) { select<Unit> { ready.onAwait { }; failed.onAwait { throw it } } }
            connected()
            var seq = 0
            var lastSent = 0L
            var origin: Long? = null
            while (currentCoroutineContext().isActive) {
                val remaining = 1_000_000_000L/CameraTuning.MAX_FPS - (System.nanoTime()-lastSent)
                if (remaining > 0) delay((remaining/1_000_000L).coerceAtLeast(1))
                val frame = select<CameraFrame> { frames.onReceive { it }; failed.onAwait { throw it } }
                if (origin == null) origin = frame.capturedAtNanos
                seq++
                lastSent = System.nanoTime()
                check(frame.jpeg.size <= 8_000_000)
                if (!socket.send(cameraPacket(seq, (frame.capturedAtNanos-origin) / 1e9, frame.jpeg).toByteString()))
                    throw DemoFailure(DemoFailure.Kind.CONNECTION)
                val response = waiting(30_000) { select<JSONObject> { replies.onReceive { it }; failed.onAwait { throw it } } }
                if (response.getLong("seq") != seq.toLong()) throw DemoFailure(DemoFailure.Kind.LIVE_FAILED)
                val parsed = try { parseCameraResult(response, frame) } catch (e: Exception) { throw DemoFailure(DemoFailure.Kind.LIVE_FAILED) }
                result(parsed)
            }
        } finally {
            replies.cancel()
            socket.send("{\"type\":\"stop\"}")
            socket.close(1000, "source closed")
            // Joining the close handshake gives the backend time to release its
            // live-session slot before the recorded-video path starts.
            withContext(NonCancellable) { withTimeoutOrNull(15_000) { closed.await() } }
            socket.cancel()
        }
    }
}
