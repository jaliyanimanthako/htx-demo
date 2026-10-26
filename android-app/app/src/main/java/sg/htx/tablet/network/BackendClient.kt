package sg.htx.tablet.network

import sg.htx.tablet.viewmodel.DemoViewer
import sg.htx.tablet.viewmodel.DemoFailure
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONObject
import sg.htx.tablet.data.SessionState
import java.util.concurrent.TimeUnit

class BackendClient : DemoViewer<Bitmap> {
    private val http = OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS).pingInterval(5, TimeUnit.SECONDS).build()
    override suspend fun monitor(base: String, session: String, token: String,
                        frame: (Bitmap) -> Unit, state: (SessionState) -> Unit,
                        connected: () -> Unit): Unit = coroutineScope {
        val url = base.trim().toHttpUrl()
        require(url.encodedPath == "/" && url.query == null && url.fragment == null && url.username.isEmpty()) {
            "Use a base URL such as http://192.168.1.20:8100"
        }
        require(session.matches(Regex("[A-Za-z0-9_-]+"))) { "Enter an existing session ID" }
        fun endpoint(suffix: String) = url.newBuilder().addPathSegments("api/sessions")
            .addPathSegment(session).addPathSegments(suffix).build()
        val videoRequest = Request.Builder().url(endpoint("stream/annotated"))
            .apply { if (token.isNotBlank()) header("Authorization", "Bearer $token") }.build()
        val eventsUrl = endpoint("events").newBuilder()
            .apply { if (token.isNotBlank()) addQueryParameter("token", token) }.build()
        val call = http.newCall(videoRequest)
        val ended = CompletableDeferred<DemoFailure>()
        fun sessionFailure(payload: JSONObject) = DemoFailure(
            if (payload.optString("error_code") == "qwen_unavailable") DemoFailure.Kind.QWEN
            else if (payload.optString("status") == "error") DemoFailure.Kind.START
            else DemoFailure.Kind.CONNECTION)
        val socket = http.newWebSocket(Request.Builder().url(eventsUrl).build(), object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (!isActive) return
                try {
                    val message = JSONObject(text)
                    val type = message.getString("type")
                    if (type == "end") {
                        ended.complete(sessionFailure(message.optJSONObject("session") ?: JSONObject()))
                        return
                    }
                    if (type == "hello" || type == "state") {
                        val payload = if (type == "hello") message.getJSONObject("session") else message
                        val status = payload.getString("status")
                        if (status in setOf("error", "stopped", "finished")) {
                            ended.complete(sessionFailure(payload))
                        } else {
                            connected()
                            state(parseSessionState(payload.getJSONObject("state").put("mode", payload.optString("mode", "exits"))))
                        }
                    }
                } catch (e: Exception) {
                    ended.complete(DemoFailure(DemoFailure.Kind.START))
                }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                ended.complete(DemoFailure(if (response?.code in setOf(401, 403))
                    DemoFailure.Kind.AUTH else DemoFailure.Kind.CONNECTION))
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                ended.complete(DemoFailure(if (code == 4401) DemoFailure.Kind.AUTH else DemoFailure.Kind.CONNECTION))
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
            }
        })
        // Cancellation closes blocking I/O immediately, including when the Activity backgrounds.
        val cancellation = launch(start = CoroutineStart.UNDISPATCHED) {
            try { awaitCancellation() } finally { call.cancel(); socket.cancel() }
        }
        val video = launch(Dispatchers.IO) {
            try {
                call.execute().use { response ->
                    if (response.code in setOf(401, 403)) throw DemoFailure(DemoFailure.Kind.AUTH)
                    check(response.isSuccessful) { "Video HTTP ${response.code}" }
                    check(response.header("Content-Type")?.startsWith("multipart/x-mixed-replace") == true) { "Expected MJPEG video" }
                    val reader = MjpegReader(requireNotNull(response.body).byteStream())
                    while (isActive) {
                        val bytes = reader.nextFrame()
                        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
                        require(options.outWidth in 1..8192 && options.outHeight in 1..8192 &&
                            options.outWidth.toLong() * options.outHeight <= 20_000_000) { "Unsupported frame dimensions" }
                        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: error("Invalid JPEG")
                        frame(bitmap)
                    }
                }
            } catch (e: Exception) {
                if (isActive) {
                    ended.complete(e as? DemoFailure ?: DemoFailure(DemoFailure.Kind.CONNECTION))
                }
            }
        }
        try { throw ended.await() } finally {
            cancellation.cancelAndJoin()
            video.cancelAndJoin()
        }
    }
}
