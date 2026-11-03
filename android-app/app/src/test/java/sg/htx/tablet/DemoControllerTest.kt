package sg.htx.tablet

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.data.SessionState
import sg.htx.tablet.viewmodel.*
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class DemoControllerTest {
    private class Store(var value: Settings = Settings()) : DemoSettingsStore {
        override fun load() = value
        override fun save(settings: Settings) { value = settings }
    }
    private class Sessions : DemoSessions {
        var error: Exception? = null
        var starts = mutableListOf<Pair<String, String>>()
        override suspend fun requireQwen(base: String, token: String) { error?.let { throw it } }
        override suspend fun videos(base: String, token: String): List<String> { error?.let { throw it }; return listOf("one.MOV", "two.MOV") }
        override suspend fun startDemo(base: String, token: String, currentId: String, source: String, progress: (String) -> Unit): String {
            requireQwen(base, token)
            starts.add(currentId to source)
            return "id-${starts.size}"
        }
    }
    private class Viewer : DemoViewer<String> {
        data class Call(val frame: (String) -> Unit, val state: (SessionState) -> Unit, val connected: () -> Unit,
                        val result: CompletableDeferred<Unit> = CompletableDeferred())
        val calls = mutableListOf<Call>()
        var open = 0
        var maxOpen = 0
        override suspend fun monitor(base: String, session: String, token: String, frame: (String) -> Unit,
            state: (SessionState) -> Unit, connected: () -> Unit) {
            val call = Call(frame, state, connected)
            calls.add(call); open++; maxOpen = maxOf(maxOpen, open)
            try { call.result.await() } finally { open-- }
        }
    }
    @Test fun initialSetupPersistsAndLandsReady() = runTest {
        val store = Store(); val controller = DemoController(backgroundScope, store, Sessions(), Viewer())
        assertFalse(controller.settings.configured)
        controller.saveSettings("http://backend:8100", "secret")
        assertEquals(Connection.IDLE, controller.state.value.connection)
        val restored = DemoController(backgroundScope, store, Sessions(), Viewer())
        assertTrue(restored.settings.configured)
        assertEquals("secret", restored.settings.token)
        assertEquals("http://backend:8100", restored.settings.base)
    }
    @Test fun startSelectAndConnectCombinedDemo() = runTest {
        val sessions = Sessions(); val viewer = Viewer()
        val controller = DemoController(backgroundScope, Store(Settings(configured = true)), sessions, viewer)
        controller.foreground(); controller.showVideoDemo(); runCurrent()
        assertTrue(controller.picker.value.open)
        assertEquals(listOf("one.MOV", "two.MOV"), controller.picker.value.videos)
        controller.detectVideo("one.MOV"); runCurrent()
        assertFalse(controller.picker.value.open)
        assertEquals(Connection.CONNECTING, controller.state.value.connection)
        viewer.calls.last().connected(); viewer.calls.last().frame("frame"); runCurrent()
        assertEquals(Connection.CONNECTED, controller.state.value.connection)
        assertEquals("frame", controller.frame.value)
        assertEquals("id-1", controller.settings.session)
    }
    @Test fun backendAndGenericFailuresHaveDifferentStates() = runTest {
        val sessions = Sessions(); val controller = DemoController(backgroundScope, Store(Settings(configured = true)), sessions, Viewer())
        controller.foreground()
        sessions.error = IOException("secret technical URL")
        controller.showVideoDemo(); runCurrent()
        assertEquals(Connection.BACKEND_UNAVAILABLE, controller.state.value.connection)
        assertFalse(controller.picker.value.message.contains("secret"))
        sessions.error = DemoFailure(DemoFailure.Kind.START)
        controller.detectVideo("one.MOV"); runCurrent()
        assertEquals(Connection.FAILED, controller.state.value.connection)
    }
    @Test fun switchRejectsOldFramesStateAndConnectionCallbacks() = runTest {
        val viewer = Viewer(); val controller = DemoController(backgroundScope, Store(Settings(configured = true)), Sessions(), viewer)
        controller.foreground(); controller.detectVideo("one.MOV"); runCurrent()
        val old = viewer.calls.single()
        controller.detectVideo("two.MOV"); runCurrent()
        old.connected(); old.frame("old"); old.state(SessionState(1.0, 1.0, emptyList())); runCurrent()
        assertNull(controller.frame.value)
        assertNull(controller.state.value.hazards)
        assertEquals(Connection.CONNECTING, controller.state.value.connection)
        viewer.calls.last().connected(); viewer.calls.last().frame("new"); runCurrent()
        assertEquals("new", controller.frame.value)
        assertEquals(1, viewer.maxOpen)
    }
    @Test fun connectionDropRetriesAndIgnoresOldAttempt() = runTest {
        val sessions = Sessions(); val viewer = Viewer()
        val controller = DemoController(backgroundScope, Store(Settings(configured = true)), sessions, viewer)
        controller.foreground(); controller.detectVideo("one.MOV"); runCurrent()
        val old = viewer.calls.single()
        old.connected(); old.frame("frame"); runCurrent()
        old.result.completeExceptionally(DemoFailure(DemoFailure.Kind.CONNECTION)); runCurrent()
        assertEquals(Connection.RECONNECTING, controller.state.value.connection)
        assertNull(controller.frame.value)
        advanceTimeBy(2000); runCurrent()
        old.frame("stale"); old.connected(); runCurrent()
        assertNull(controller.frame.value)
        assertEquals(Connection.RECONNECTING, controller.state.value.connection)
        assertEquals("id-1" to "one.MOV", sessions.starts.last())
        assertEquals(1, viewer.maxOpen)
    }
    @Test fun backgroundClosesViewerAndForegroundRestoresSelection() = runTest {
        val viewer = Viewer(); val controller = DemoController(backgroundScope, Store(Settings(configured = true)), Sessions(), viewer)
        controller.foreground(); controller.detectVideo("one.MOV"); runCurrent()
        val old = viewer.calls.single()
        controller.background(); runCurrent()
        assertEquals(0, viewer.open)
        old.frame("late"); old.connected(); runCurrent()
        assertNull(controller.frame.value)
        controller.foreground(); runCurrent()
        assertEquals(2, viewer.calls.size)
        assertEquals("one.MOV", controller.picker.value.selected)
    }
    @Test fun unavailableQwenIsInformationalWhileDetectionContinues() = runTest {
        val viewer = Viewer()
        val controller = DemoController(backgroundScope, Store(Settings(configured = true)), Sessions(), viewer)
        controller.foreground(); controller.showVideoDemo(); runCurrent()
        assertEquals(2, controller.picker.value.videos.size)
        controller.detectVideo("one.MOV"); runCurrent()
        val current = viewer.calls.single()
        current.connected(); current.frame("detection frame")
        current.state(SessionState(1.0, 10.0, emptyList(), "combined", false, qwenActive = false)); runCurrent()
        assertEquals(Connection.CONNECTED, controller.state.value.connection)
        assertEquals("detection frame", controller.frame.value)
        assertTrue(controller.state.value.assistanceMessage.contains("Qwen assistance unavailable"))
        assertFalse(controller.state.value.dateReadingEnabled)
        controller.detectVideo("two.MOV"); runCurrent()
        val next = viewer.calls.last()
        next.connected(); next.state(SessionState(1.0, 10.0, emptyList(), "combined", true, qwenActive = true)); runCurrent()
        current.state(SessionState(2.0, 10.0, emptyList(), "combined", false, qwenActive = false)); runCurrent()
        assertEquals(true, controller.state.value.qwenActive)
        assertEquals("Qwen assistance active", controller.state.value.assistanceMessage)
    }
    @Test fun backendUrlValidationRejectsCredentialsAndPaths() {
        assertTrue(validBackendUrl("https://backend:8100"))
        listOf("garbage", "http://user:secret@backend", "http://backend/api", "http://backend?token=secret").forEach {
            assertFalse(validBackendUrl(it))
        }
    }
    @Test fun switchingDuringStartupWaitsForOldWorkAndRejectsItsResult() = runTest {
        val gate = CompletableDeferred<Unit>()
        val starts = mutableListOf<String>()
        val sessions = object : DemoSessions {
            override suspend fun requireQwen(base: String, token: String) = Unit
            override suspend fun videos(base: String, token: String) = listOf("one.MOV", "two.MOV")
            override suspend fun startDemo(base: String, token: String, currentId: String, source: String, progress: (String) -> Unit): String {
                starts.add(source)
                if (source == "one.MOV") withContext(NonCancellable) { gate.await() }
                return source
            }
        }
        val viewer = Viewer()
        val controller = DemoController(backgroundScope, Store(Settings(configured = true)), sessions, viewer)
        controller.foreground(); controller.detectVideo("one.MOV"); runCurrent()
        controller.detectVideo("two.MOV"); runCurrent()
        assertEquals(listOf("one.MOV"), starts)
        gate.complete(Unit); runCurrent()
        assertEquals(listOf("one.MOV", "two.MOV"), starts)
        assertEquals("two.MOV", controller.settings.session)
        assertEquals(1, viewer.calls.size)
    }
    @Test fun framesAreConflatedBeforeReachingTheUi() = runTest {
        val viewer = Viewer()
        val controller = DemoController(backgroundScope, Store(Settings(configured = true)), Sessions(), viewer)
        controller.foreground(); controller.detectVideo("one.MOV"); runCurrent()
        repeat(1000) { viewer.calls.single().frame("frame-$it") }
        runCurrent()
        assertEquals("frame-999", controller.frame.value)
    }

    @Test fun anotherDevicesSessionHasAnActionableMessage() = runTest {
        val sessions = Sessions().apply { error = DemoFailure(DemoFailure.Kind.IN_USE) }
        val controller = DemoController(backgroundScope, Store(Settings(configured = true)), sessions, Viewer())
        controller.foreground(); controller.detectVideo("one.MOV"); runCurrent()
        assertEquals(Connection.FAILED, controller.state.value.connection)
        assertTrue(controller.state.value.detail.contains("another device"))
        assertTrue(controller.state.value.detail.contains("Stop that demo"))
    }

}
