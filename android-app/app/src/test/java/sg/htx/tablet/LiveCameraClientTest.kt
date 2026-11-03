package sg.htx.tablet

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import okhttp3.*
import okhttp3.mockwebserver.*
import okio.ByteString
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.data.*
import sg.htx.tablet.network.*
import java.nio.ByteBuffer
import java.nio.ByteOrder

class LiveCameraClientTest {
    @Test fun authenticatedSocketKeepsOneFrameInFlightAndOnlyNewestPending()=runBlocking {
        val server=MockWebServer()
        val packets=Channel<ByteString>(4)
        val start=CompletableDeferred<JSONObject>()
        val peer=CompletableDeferred<WebSocket>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object:WebSocketListener() {
            override fun onOpen(ws:WebSocket,response:Response) { peer.complete(ws) }
            override fun onMessage(ws:WebSocket,text:String) {
                val j=JSONObject(text)
                if(j.optString("type")=="start") {
                    start.complete(j)
                    ws.send("""{"type":"ready","mode":"combined"}""")
                }
            }
            override fun onMessage(ws:WebSocket,bytes:ByteString) { packets.trySend(bytes) }
            override fun onClosing(ws:WebSocket,code:Int,reason:String) { ws.close(code,null) }
        }))
        server.start()
        val buffer=CameraFrameBuffer()
        val results=Channel<CameraResult>(2)
        val client=LiveCameraClient()
        val job=launch { client.monitor(server.url("/").toString(),"private-token",buffer.frames,{ results.trySend(it) },{}) }
        try {
            withTimeout(5000) {
                assertEquals("combined",start.await().getString("mode"))
                assertTrue(start.await().getBoolean("use_qwen"))
                val request=withContext(Dispatchers.IO) { server.takeRequest() }
                assertEquals("Bearer private-token",request.getHeader("Authorization"))
                assertEquals("/api/live",request.path)
                fun frame(n:Int)=CameraFrame(byteArrayOf(n.toByte()),n*1_000_000_000L,CameraProjection(0f,0f,960f,0f,0f,540f))
                buffer.offer(frame(1))
                val first=ByteBuffer.wrap(packets.receive().toByteArray()).order(ByteOrder.LITTLE_ENDIAN)
                assertEquals(1,first.int);assertEquals(0.0,first.double,0.0);assertEquals(1,first.get().toInt())
                buffer.offer(frame(2));buffer.offer(frame(3));delay(100)
                assertTrue(packets.tryReceive().isFailure)
                peer.await().send("""{"type":"result","mode":"combined","seq":1,"t":0,"qwen_active":false,"date_reading_enabled":false,"exits":[],"extinguishers":[]}""")
                assertEquals(false,results.receive().state.qwenActive)
                val second=ByteBuffer.wrap(packets.receive().toByteArray()).order(ByteOrder.LITTLE_ENDIAN)
                assertEquals(2,second.int);assertEquals(2.0,second.double,0.0);assertEquals(3,second.get().toInt())
            }
        } finally {
            job.cancelAndJoin();buffer.close();server.shutdown()
        }
    }
    @Test fun combinedLiveContractKeepsPresenceSeparateFromDateVerification() {
        val frame=CameraFrame(byteArrayOf(),0,CameraProjection(0f,0f,960f,0f,0f,540f))
        val result=parseCameraResult(JSONObject("""{"type":"result","mode":"combined","seq":7,"t":1,"qwen_active":false,"date_reading_enabled":false,"exits":[{"id":1,"status":"obstructed","status_text":"BLOCKED","box":[0,0,1,1]}],"extinguishers":[{"id":2,"status":"reading","status_text":"READING","confirmed":false,"due_text":null,"box":[0.2,0.2,0.3,0.4]}]}"""),frame)
        assertEquals(1,result.state.hazardCount)
        assertEquals(1,result.state.extinguishers.size)
        assertFalse(result.state.extinguishers.single().confirmed)
        assertNull(result.state.extinguishers.single().dueText)
        assertTrue(result.boxes.last().label.contains("Dates not verified"))
        assertEquals(7L,result.seq)
    }
}
