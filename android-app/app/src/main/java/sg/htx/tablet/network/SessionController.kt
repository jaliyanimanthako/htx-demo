package sg.htx.tablet.network

import sg.htx.tablet.viewmodel.DemoFailure
import sg.htx.tablet.viewmodel.DemoSessions
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Uses existing session APIs only. Never stops an unrelated session. */
class SessionController(private val http: OkHttpClient = OkHttpClient.Builder()
    .readTimeout(180, TimeUnit.SECONDS).connectTimeout(8, TimeUnit.SECONDS).build(),
    private val demoClientId: String = "") : DemoSessions {
    private suspend fun request(base: String, token: String, path: String, method: String = "GET", body: JSONObject? = null): JSONObject {
        val url = base.trim().toHttpUrl()
        require(url.encodedPath == "/" && url.query == null && url.fragment == null) { "Enter a backend base URL" }
        val req = Request.Builder().url(url.newBuilder().addPathSegments(path).build())
            .apply { if (token.isNotBlank()) header("Authorization", "Bearer $token") }
            .method(method, body?.toString()?.toRequestBody("application/json".toMediaType())).build()
        return suspendCancellableCoroutine { continuation ->
            val call = http.newCall(req)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) { if (!continuation.isCancelled) continuation.resumeWithException(e) }
                override fun onResponse(call: Call, response: Response) {
                    try {
                        val json = response.use {
                            val text = it.body?.string().orEmpty()
                            val data = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
                            if (!it.isSuccessful) throw DemoFailure(when {
                                data.optJSONObject("detail")?.optString("code") == "qwen_unavailable" -> DemoFailure.Kind.QWEN
                                it.code in setOf(401, 403) -> DemoFailure.Kind.AUTH
                                it.code == 409 -> DemoFailure.Kind.BUSY
                                else -> DemoFailure.Kind.START
                            })
                            data
                        }
                        continuation.resume(json)
                    } catch (e: Exception) { if (!continuation.isCancelled) continuation.resumeWithException(e) }
                }
            })
        }
    }
    override suspend fun requireQwen(base: String, token: String) {
        val info = request(base, token, "api/info")
        if (!info.getJSONObject("models").getJSONObject("qwen").optBoolean("up", false))
            throw DemoFailure(DemoFailure.Kind.QWEN)
    }
    override suspend fun startDemo(base: String, token: String, currentId: String, source: String,
                          progress: (String) -> Unit): String {
        return switchVideo(base, token, currentId, source, "combined", true, false, requireQwen = false, progress = progress)
    }
    override suspend fun videos(base: String, token: String): List<String> {
        val list = request(base, token, "api/videos").getJSONArray("videos")
        return (0 until list.length()).map { list.getString(it) }
    }
    suspend fun switchVideo(base: String, token: String, currentId: String, source: String,
                            mode: String = "exits", readDates: Boolean = false, adoptSingleSession: Boolean = false,
                            requireQwen: Boolean = false, progress: (String) -> Unit): String {
        require(mode in setOf("exits", "extinguisher", "combined"))
        require(source in videos(base, token)) { "Video is no longer available on the backend" }
        val sessions = request(base, token, "api/sessions").getJSONArray("sessions")
        val all = (0 until sessions.length()).map { sessions.getJSONObject(it) }
        val active = all.filter { it.getString("status") in setOf("starting", "running") }
        val current = if (adoptSingleSession) {
            if (active.size > 1) throw DemoFailure(DemoFailure.Kind.IN_USE)
            active.singleOrNull() ?: all.find { it.getString("id") == currentId }
        } else active.find { it.getString("id") == currentId } ?:
            active.singleOrNull { demoClientId.isNotBlank() && it.optString("demo_client_id") == demoClientId } ?:
            all.find { it.getString("id") == currentId }
        if (active.any { it.getString("id") != current?.getString("id") })
            throw DemoFailure(DemoFailure.Kind.IN_USE)
        if (mode != "exits") {
            // Prevent an old backend silently ignoring the new mode field and running exit detection.
            val schema = request(base, token, "openapi.json")
            check(schema.getJSONObject("components").getJSONObject("schemas").getJSONObject("SessionRequest")
                .getJSONObject("properties").optJSONObject("mode")?.optJSONArray("enum")?.let { modes ->
                    (0 until modes.length()).any { modes.getString(it) == mode }
                } == true) { "Restart the updated backend to enable $mode sessions." }
        }
        if (current in active && current?.getString("source") == source &&
            current.optString("mode", "exits") == mode && (mode == "exits" ||
                current.optJSONObject("models")?.let { it.optBoolean("qwen_requested", it.optBoolean("qwen", false)) } == readDates)) return current.getString("id")
        if (current in active && current != null) {
            val id = current.getString("id")
            require(id.matches(Regex("[A-Za-z0-9_-]+")))
            progress("Changing video…")
            request(base, token, "api/sessions/$id", "DELETE")
            awaitStopped(base, token, id)
        }
        progress("Starting demo…")
        val models = current?.optJSONObject("models")
        val payload = JSONObject().put("require_qwen", requireQwen).put("source", source).put("loop", true).put("mode", mode)
            .put("use_yoloe", if (mode != "exits") true else models?.optBoolean("yoloe", true) ?: true)
            .put("use_locate", if (mode != "exits") false else models?.optBoolean("locate_anything", false) ?: false)
            .put("use_qwen", if (mode != "exits") readDates else models?.optBoolean("qwen", false) ?: false)
        if (mode == "combined" && demoClientId.isNotBlank()) payload.put("demo_client_id", demoClientId)
        return request(base, token, "api/sessions", "POST", payload).getString("id")
    }
    private suspend fun awaitStopped(base: String, token: String, id: String) {
            var stopped = false
            repeat(60) {
                if (!stopped) {
                    val status = request(base, token, "api/sessions/$id").getString("status")
                    stopped = status in setOf("stopped", "finished", "error")
                    if (!stopped) delay(500)
                }
            }
            check(stopped) { "Previous session is still stopping. Wait a moment, then retry." }
    }
    override suspend fun prepareCamera(base: String, token: String, currentId: String) {
        val list = request(base, token, "api/sessions").getJSONArray("sessions")
        val active = (0 until list.length()).map { list.getJSONObject(it) }.filter { it.optString("status") in setOf("starting", "running") }
        val owned = active.filter { it.optString("id") == currentId ||
            (demoClientId.isNotBlank() && it.optString("demo_client_id") == demoClientId) }
        if (active.size != owned.size) throw DemoFailure(DemoFailure.Kind.IN_USE)
        for (session in owned) {
            val id = session.getString("id")
            require(id.matches(Regex("[A-Za-z0-9_-]+")))
            request(base, token, "api/sessions/$id", "DELETE")
            awaitStopped(base, token, id)
        }
    }

}
