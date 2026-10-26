package sg.htx.tablet

import kotlinx.coroutines.runBlocking
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.network.SessionController
import sg.htx.tablet.viewmodel.DemoFailure
import java.util.concurrent.ConcurrentLinkedQueue

class SessionControllerTest {
    private val requests = ConcurrentLinkedQueue<Request>()
    private fun controller(vararg replies: Pair<Int, String>, demoClientId: String = ""): SessionController {
        val responses = ConcurrentLinkedQueue(replies.toList())
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            requests.add(chain.request())
            val reply = responses.poll() ?: error("Unexpected request: ${chain.request().url}")
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(reply.first).message("test").body(reply.second.toResponseBody("application/json".toMediaType())).build()
        }.build()
        return SessionController(client, demoClientId)
    }
    private val videos = 200 to """{"videos":["one.MOV","two.MOV"]}"""
    private val session = """{"id":"old","source":"one.MOV","status":"running","models":{"yoloe":true,"locate_anything":false,"qwen":true}}"""
    @Test fun oldBackendCannotSilentlyRunExitMode() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[]}""",
            200 to """{"components":{"schemas":{"SessionRequest":{"properties":{}}}}}""")
        try { c.switchVideo("http://backend", "", "", "one.MOV", "extinguisher") {}; fail("Must refuse old backend") }
        catch (e: IllegalStateException) { assertTrue(e.message!!.contains("Restart")) }
        assertTrue(requests.none { it.method == "POST" || it.method == "DELETE" })
    }
    @Test fun extinguisherCreationEnablesOnlyRequestedModels() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[]}""",
            200 to """{"components":{"schemas":{"SessionRequest":{"properties":{"mode":{"enum":["exits","extinguisher","combined"]}}}}}}""",
            201 to """{"id":"ext"}""")
        assertEquals("ext", c.switchVideo("http://backend", "", "", "one.MOV", "extinguisher", true) {})
        val buffer = okio.Buffer(); requests.last().body!!.writeTo(buffer)
        val payload = JSONObject(buffer.readUtf8())
        assertEquals("extinguisher", payload.getString("mode"))
        assertTrue(payload.getBoolean("use_yoloe")); assertTrue(payload.getBoolean("use_qwen"))
        assertFalse(payload.getBoolean("use_locate"))
    }
    @Test fun combinedRequestIncludesBothDetectors() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[]}""",
            200 to """{"components":{"schemas":{"SessionRequest":{"properties":{"mode":{"enum":["exits","extinguisher","combined"]}}}}}}""",
            201 to """{"id":"both"}""")
        assertEquals("both", c.switchVideo("http://backend", "", "", "one.MOV", "combined", false) {})
        val buffer = okio.Buffer(); requests.last().body!!.writeTo(buffer)
        val payload = JSONObject(buffer.readUtf8())
        assertEquals("combined", payload.getString("mode"))
        assertTrue(payload.getBoolean("use_yoloe")); assertFalse(payload.getBoolean("use_qwen"))
    }
    @Test fun switchesOnlyAfterStopAndPreservesModels() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[$session]}""", 200 to "{}",
            200 to """{"status":"stopped"}""", 201 to """{"id":"new"}""")
        assertEquals("new", c.switchVideo("http://backend:8100", "secret", "old", "two.MOV") {})
        assertEquals(listOf("GET", "GET", "DELETE", "GET", "POST"), requests.map { it.method })
        assertTrue(requests.all { it.header("Authorization") == "Bearer secret" })
        val buffer = okio.Buffer(); requests.last().body!!.writeTo(buffer)
        val payload = JSONObject(buffer.readUtf8())
        assertEquals("two.MOV", payload.getString("source"))
        assertTrue(payload.getBoolean("use_qwen")); assertFalse(payload.getBoolean("use_locate"))
        assertTrue(payload.getBoolean("loop"))
    }
    @Test fun selectingCurrentVideoReusesSession() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[$session]}""")
        assertEquals("old", c.switchVideo("http://backend", "", "old", "one.MOV") {})
        assertEquals(2, requests.size)
    }
    @Test fun unrelatedSessionIsNotStopped() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[$session]}""")
        try { c.switchVideo("http://backend", "", "unrelated", "two.MOV") {}; fail("Must refuse") }
        catch (e: DemoFailure) { assertEquals(DemoFailure.Kind.IN_USE, e.kind) }
        assertEquals(2, requests.size)
    }
    @Test fun firstSessionUsesYoloOnly() = runBlocking {
        val c = controller(videos, 200 to """{"sessions":[]}""", 201 to """{"id":"first"}""")
        assertEquals("first", c.switchVideo("http://backend", "", "", "one.MOV") {})
        val buffer = okio.Buffer(); requests.last().body!!.writeTo(buffer)
        val payload = JSONObject(buffer.readUtf8())
        assertTrue(payload.getBoolean("use_yoloe")); assertFalse(payload.getBoolean("use_qwen")); assertFalse(payload.getBoolean("use_locate"))
    }
    @Test fun missingVideoDoesNotStopSession() = runBlocking {
        val c = controller(videos)
        try { c.switchVideo("http://backend", "", "old", "missing.MOV") {}; fail("Must reject") }
        catch (_: IllegalArgumentException) { }
        assertEquals(1, requests.size)
    }
    @Test fun unauthorizedExplainsFailure() = runBlocking {
        val c = controller(401 to """{"detail":"missing or wrong token"}""")
        try { c.videos("http://backend", "wrong"); fail("Must reject") }
        catch (e: DemoFailure) { assertEquals(DemoFailure.Kind.AUTH, e.kind) }
    }
    @Test fun demoPrefersQwenWithoutRequiringItAndCreatesCombinedSession() = runBlocking {
        val c = controller(videos,
            200 to """{"sessions":[]}""",
            200 to """{"components":{"schemas":{"SessionRequest":{"properties":{"mode":{"enum":["combined"]}}}}}}""",
            201 to """{"id":"demo"}""")
        assertEquals("demo", c.startDemo("http://backend", "", "", "one.MOV") {})
        val buffer = okio.Buffer(); requests.last().body!!.writeTo(buffer)
        val payload = JSONObject(buffer.readUtf8())
        assertEquals("combined", payload.getString("mode"))
        assertTrue(payload.getBoolean("use_qwen")); assertTrue(payload.getBoolean("use_yoloe"))
        assertFalse(payload.getBoolean("require_qwen")); assertFalse(payload.getBoolean("use_locate"))
    }
    @Test fun strictReadinessDiagnosticStillReportsUnavailableQwen() = runBlocking {
        val c = controller(200 to """{"models":{"qwen":{"up":false}}}""")
        try { c.requireQwen("http://backend", ""); fail("Must reject") }
        catch (e: DemoFailure) { assertEquals(DemoFailure.Kind.QWEN, e.kind) }
        assertEquals(1, requests.size)
    }
    @Test fun typedQwenStartupRaceIsNotGenericServerFailure() = runBlocking {
        val c = controller(503 to """{"detail":{"code":"qwen_unavailable"}}""")
        try { c.videos("http://backend", ""); fail("Must reject") }
        catch (e: DemoFailure) { assertEquals(DemoFailure.Kind.QWEN, e.kind) }
        val generic = controller(500 to """{"detail":"internal error"}""")
        try { generic.videos("http://backend", ""); fail("Must reject") }
        catch (e: DemoFailure) { assertEquals(DemoFailure.Kind.START, e.kind) }
    }

    @Test fun lostCreationResponseRecoversOnlyThisTabletsSession() = runBlocking {
        val owner = "00000000-0000-0000-0000-000000000001"
        val owned = """{"id":"owned","source":"one.MOV","mode":"combined","status":"running","demo_client_id":"$owner","models":{"qwen":true}}"""
        val c = controller(videos,
            200 to """{"sessions":[$owned]}""",
            200 to """{"components":{"schemas":{"SessionRequest":{"properties":{"mode":{"enum":["combined"]}}}}}}""",
            demoClientId = owner)
        assertEquals("owned", c.startDemo("http://backend", "", "", "one.MOV") {})
        assertTrue(requests.none { it.method == "POST" || it.method == "DELETE" })
    }

    @Test fun degradedSessionIsReusedWithoutRestartingOnReconnect() = runBlocking {
        val running = """{"id":"old","mode":"combined","source":"one.MOV","status":"running","models":{"qwen":false,"qwen_requested":true}}"""
        val c = controller(videos, 200 to """{"sessions":[$running]}""",
            200 to """{"components":{"schemas":{"SessionRequest":{"properties":{"mode":{"enum":["combined"]}}}}}}""")
        assertEquals("old", c.startDemo("http://backend", "", "old", "one.MOV") {})
        assertTrue(requests.none { it.method == "POST" || it.method == "DELETE" })
        assertTrue(requests.none { it.url.encodedPath == "/api/info" })
    }

    @Test fun cameraPreparationStopsOnlyRememberedRecordedSession() = runBlocking {
        val c = controller(200 to """{"sessions":[$session]}""", 200 to "{}", 200 to """{"status":"stopped"}""")
        c.prepareCamera("http://backend", "private-token", "old")
        assertEquals(listOf("GET","DELETE","GET"),requests.map { it.method })
        assertTrue(requests.all { it.header("Authorization") == "Bearer private-token" })
    }
    @Test fun cameraPreparationCannotStopAnotherDevicesVideo() = runBlocking {
        val c = controller(200 to """{"sessions":[$session]}""")
        try { c.prepareCamera("http://backend", "", ""); fail("Must refuse") }
        catch (e: DemoFailure) { assertEquals(DemoFailure.Kind.IN_USE,e.kind) }
        assertEquals(1,requests.size)
    }

}
