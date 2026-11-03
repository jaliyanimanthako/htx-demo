package sg.htx.tablet

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.data.*
import sg.htx.tablet.viewmodel.*
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class CameraSourceTest {
    private class Store(var settings: Settings = Settings(configured=true, token="saved-token")) : DemoSettingsStore {
        override fun load() = settings
        override fun save(settings: Settings) { this.settings=settings }
    }
    private class Sessions : DemoSessions {
        var externalStarted = 0
        override suspend fun startExternal(base: String, token: String, currentId: String, progress: (String)->Unit): String {
            externalStarted++; return "external-session"
        }
        var videosStarted = 0
        var prepared = 0
        override suspend fun requireQwen(base: String, token: String) = Unit
        override suspend fun videos(base: String, token: String) = listOf("one.MOV")
        override suspend fun startDemo(base: String, token: String, currentId: String, source: String, progress: (String)->Unit): String {
            videosStarted++; return "video-session"
        }
        override suspend fun prepareCamera(base: String, token: String, currentId: String) { prepared++ }
    }
    private class Video : DemoViewer<String> {
        var frame: ((String)->Unit)?=null
        var open=0
        override suspend fun monitor(base:String,session:String,token:String,frame:(String)->Unit,state:(SessionState)->Unit,connected:()->Unit) {
            this.frame=frame;open++
            try { awaitCancellation() } finally { open-- }
        }
    }
    private class Camera : CameraDemoClient {
        data class Call(val result:(CameraResult)->Unit,val connected:()->Unit,val frames:ReceiveChannel<CameraFrame>,val end:CompletableDeferred<Unit> = CompletableDeferred())
        val calls=mutableListOf<Call>()
        var error:Exception?=null
        var open=0
        var maxOpen=0
        var token=""
        override suspend fun monitor(base:String,token:String,frames:ReceiveChannel<CameraFrame>,result:(CameraResult)->Unit,connected:()->Unit) {
            error?.let { throw it }
            this.token=token
            val call=Call(result,connected,frames);calls.add(call);open++;maxOpen=maxOf(open,maxOpen)
            try { call.end.await() } finally { open-- }
        }
    }
    private fun frame(n:Int=1)=CameraFrame(byteArrayOf(n.toByte()),1_000_000_000L,
        CameraProjection(0f,0f,960f,0f,0f,540f))
    private fun result(qwen:Boolean=false)=CameraResult(1,frame(),SessionState(1.0,10.0,emptyList(),"combined",qwen,qwenActive=qwen),emptyList())

    @Test fun readyOffersSourcesAndRecordedPathRemainsAvailable()=runTest {
        val sessions=Sessions();val c=DemoController(backgroundScope,Store(),sessions,Video(),Camera())
        c.foreground();c.showSources()
        assertTrue(c.sourcePicker.value)
        assertEquals(0,sessions.videosStarted)
        c.chooseRecorded();runCurrent()
        assertFalse(c.sourcePicker.value)
        assertTrue(c.picker.value.open)
        c.detectVideo("one.MOV");runCurrent()
        assertEquals(InputSource.RECORDED_VIDEO,c.source.value)
        assertEquals(1,sessions.videosStarted)
    }
    @Test fun cameraPermissionDeniedDoesNotStartNetworkAndAllowsVideo()=runTest {
        val sessions=Sessions();val camera=Camera();val c=DemoController(backgroundScope,Store(),sessions,Video(),camera)
        c.foreground();c.chooseCamera(false);runCurrent()
        assertEquals(Connection.CAMERA_PERMISSION,c.state.value.connection)
        assertFalse(c.capture.value.enabled)
        assertEquals(0,sessions.prepared);assertTrue(camera.calls.isEmpty())
        c.chooseRecorded();runCurrent();c.detectVideo("one.MOV");runCurrent()
        assertEquals(1,sessions.videosStarted)
    }
    @Test fun permissionGrantStartsCombinedCameraWithSavedToken()=runTest {
        val camera=Camera();val store=Store();val c=DemoController(backgroundScope,store,Sessions(),Video(),camera)
        c.foreground();c.chooseCamera(false);c.cameraPermission(true);runCurrent()
        assertEquals(1,camera.calls.size)
        assertEquals("saved-token",camera.token)
        camera.calls.single().connected();camera.calls.single().result(result());runCurrent()
        assertEquals(Connection.CONNECTED,c.state.value.connection)
        assertTrue(c.state.value.videoAvailable)
        assertFalse(c.state.value.dateReadingEnabled)
        assertTrue(c.state.value.assistanceMessage.contains("unavailable"))
        assertEquals(InputSource.TABLET_CAMERA,store.settings.source)
    }
    @Test fun switchesBothWaysAndRejectsLateCallbacksFromBothSources()=runTest {
        val video=Video();val camera=Camera();val sessions=Sessions()
        val c=DemoController(backgroundScope,Store(),sessions,video,camera)
        c.foreground();c.detectVideo("one.MOV");runCurrent()
        val oldVideo=video.frame!!
        c.chooseCamera(true);runCurrent()
        assertEquals(0,video.open);assertEquals(1,sessions.prepared)
        oldVideo("old");runCurrent();assertNull(c.frame.value)
        val old=camera.calls.single();val oldToken=c.capture.value.generation
        c.detectVideo("one.MOV");runCurrent()
        assertEquals(0,camera.open)
        old.result(result(true));old.connected();c.cameraFrame(oldToken,frame());runCurrent()
        assertNull(c.cameraResult.value)
        assertEquals(InputSource.RECORDED_VIDEO,c.source.value)
        assertEquals(Connection.CONNECTING,c.state.value.connection)
        c.chooseCamera(true);runCurrent();assertEquals(1,camera.maxOpen)
    }
    @Test fun cameraInitializationFailureStopsCaptureAndLiveConnection()=runTest {
        val camera=Camera();val c=DemoController(backgroundScope,Store(),Sessions(),Video(),camera)
        c.foreground();c.chooseCamera(true);runCurrent()
        val token=c.capture.value.generation
        c.cameraFailed(token);runCurrent()
        assertEquals(Connection.CAMERA_FAILED,c.state.value.connection)
        assertFalse(c.capture.value.enabled);assertEquals(0,camera.open)
        camera.calls.single().result(result());runCurrent();assertNull(c.cameraResult.value)
    }
    @Test fun cameraBackendUnavailableAndLiveFailureRemainDistinct()=runTest {
        val camera=Camera().apply { error=IOException() }
        val c=DemoController(backgroundScope,Store(),Sessions(),Video(),camera)
        c.foreground();c.chooseCamera(true);runCurrent()
        assertEquals(Connection.BACKEND_UNAVAILABLE,c.state.value.connection)
        camera.error=DemoFailure(DemoFailure.Kind.LIVE_FAILED)
        c.retry();runCurrent()
        assertEquals(Connection.LIVE_FAILED,c.state.value.connection)
    }
    @Test fun cameraReconnectDropsPendingFramesAndOldResults()=runTest {
        val camera=Camera();val c=DemoController(backgroundScope,Store(),Sessions(),Video(),camera)
        c.foreground();c.chooseCamera(true);runCurrent()
        val old=camera.calls.single();old.connected();runCurrent()
        c.cameraFrame(c.capture.value.generation,frame(1))
        old.end.completeExceptionally(DemoFailure(DemoFailure.Kind.CONNECTION));runCurrent()
        assertEquals(Connection.RECONNECTING,c.state.value.connection)
        assertTrue(old.frames.isClosedForReceive)
        advanceTimeBy(2000);runCurrent()
        val next=camera.calls.last();next.connected();next.result(result(true));runCurrent()
        old.result(result(false));runCurrent()
        assertEquals(true,c.state.value.qwenActive)
        assertTrue(c.state.value.videoAvailable);assertEquals(1,camera.maxOpen)
    }
    @Test fun cameraBackgroundAndReturnDoNotDuplicateSessions()=runTest {
        val camera=Camera();val store=Store();val c=DemoController(backgroundScope,store,Sessions(),Video(),camera)
        c.foreground();c.chooseCamera(true);runCurrent();val oldToken=c.capture.value.generation
        c.background();runCurrent();assertFalse(c.capture.value.enabled);assertEquals(0,camera.open)
        c.foreground();runCurrent();assertTrue(c.capture.value.enabled)
        assertNotEquals(oldToken,c.capture.value.generation);assertEquals(1,camera.maxOpen)
        val restored=DemoController(backgroundScope,store,Sessions(),Video(),Camera())
        assertEquals(InputSource.TABLET_CAMERA,restored.settings.source)
    }
    @Test fun newestPendingFrameWinsAndCloseInvalidatesIt()=runTest {
        val buffer=CameraFrameBuffer()
        repeat(1000) { buffer.offer(frame(it)) }
        assertEquals(999.toByte(),buffer.frames.receive().jpeg.single())
        assertTrue(buffer.frames.tryReceive().isFailure)
        buffer.offer(frame(1));buffer.close();buffer.offer(frame(2))
        assertTrue(buffer.frames.isClosedForReceive)
    }
    @Test fun projectionHandlesLetterboxRotationAndMirroring() {
        val raw=BoundingBox(.2f,.25f,.6f,.75f)
        assertEquals(BoundingBox(300f,125f,700f,375f),CameraProjection(100f,0f,1100f,0f,100f,500f).map(raw))
        assertEquals(BoundingBox(160f,96f,480f,288f),CameraProjection(640f,0f,640f,480f,0f,0f).map(raw))
        assertEquals(BoundingBox(400f,125f,800f,375f),CameraProjection(1000f,0f,0f,0f,1000f,500f).map(raw))
    }
    @Test fun externalCameraPersistsSelectionAndResumesWithoutTabletPermission()=runTest {
        val sessions=Sessions(); val camera=Camera(); val video=Video(); val store=Store()
        val c=DemoController(backgroundScope,store,sessions,video,camera)
        c.foreground(); c.chooseExternal(); runCurrent()
        assertEquals(InputSource.EXTERNAL_CAMERA,c.source.value)
        assertEquals(InputSource.EXTERNAL_CAMERA,store.settings.source)
        assertEquals(1,sessions.externalStarted)
        assertEquals(0,sessions.videosStarted)
        assertFalse(c.capture.value.enabled)
        assertTrue(camera.calls.isEmpty())
        assertEquals(1,video.open)
        c.background(); runCurrent()
        c.foreground(); runCurrent()
        assertEquals(2,sessions.externalStarted)
        c.detectVideo("one.MOV"); runCurrent()
        assertEquals(InputSource.RECORDED_VIDEO,c.source.value)
        assertEquals(1,sessions.videosStarted)
    }

}
