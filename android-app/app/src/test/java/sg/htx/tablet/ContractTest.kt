package sg.htx.tablet

import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject
import sg.htx.tablet.network.*

class ContractTest {
    @Test fun combinedStateKeepsIndependentResults() {
        val s = parseSessionState(JSONObject("""{"mode":"combined","video_time_s":1,"fps":10,"exits":[{"id":1,"status":"obstructed","status_text":"BLOCKED","visible":true}],"extinguishers":[{"id":1,"status":"reading","status_text":"READING","due_text":null,"confirmed":false}]}"""))
        assertEquals("combined", s.mode)
        assertEquals(1, s.hazardCount)
        assertEquals(1, s.extinguishers.size)
        assertFalse(s.dateReadingEnabled)
    }

    @Test fun extinguisherStatePreservesUnknownAndDateStatus() {
        val s = parseSessionState(JSONObject("""{"mode":"extinguisher","video_time_s":1,"fps":20,"date_reading_enabled":true,"extinguishers":[{"id":1,"status":"expired","status_text":"EXPIRED","due_text":"June 2026","confirmed":true,"text":"overdue"},{"id":2,"status":"unreadable","status_text":"UNREADABLE","due_text":null,"confirmed":false}]}"""))
        assertEquals("extinguisher", s.mode)
        assertTrue(s.dateReadingEnabled)
        assertEquals(2, s.extinguishers.size)
        assertEquals("expired", s.extinguishers[0].status)
        assertNull(s.extinguishers[1].dueText)
        assertFalse(s.extinguishers[1].confirmed)
    }

    @Test fun liveContractPreservesSequenceAndBox() {
        val r = parseLiveResult("""{"type":"result","seq":42,"t":1.25,"exits":[{"id":1,"exit_sign":true,"confirmed":null,"status":"unknown","status_text":"CHECKING","since_s":0,"blockers":[],"box":[0.1,0.2,0.9,1]}],"obstacles":[]}""")
        assertEquals(42L, r.seq); assertEquals(1.25, r.t, 0.0)
        assertNull(r.exits[0].confirmed); assertEquals(.1f, r.exits[0].box.left)
    }
    @Test fun sessionCountsOnlyVisibleAffectedExits() {
        val s = parseSessionState(JSONObject("""{"video_time_s":2.5,"fps":20,"exits":[{"id":1,"status":"obstructed","status_text":"BLOCKED","visible":true},{"id":2,"status":"obstructed","status_text":"BLOCKED","visible":false},{"id":3,"status":"clear","status_text":"CLEAR","visible":true}]}"""))
        assertEquals(1, s.hazardCount); assertEquals(2.5, s.videoTimeS!!, 0.0)
    }
    @Test fun startupIsNotEmptyDetectionState() { assertNull(parseSessionState(JSONObject("{}")).videoTimeS) }
    @Test fun readsMultipartFramesWithoutMixing() {
        val reader = MjpegReader("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nabc\r\n--frame\r\nContent-Length: 2\r\n\r\nde\r\n".byteInputStream())
        assertEquals("abc", String(reader.nextFrame())); assertEquals("de", String(reader.nextFrame()))
    }
    @Test(expected = IllegalArgumentException::class) fun rejectsUnboundedFrame() { MjpegReader("--frame\r\nContent-Length: 9000000\r\n\r\n".byteInputStream()).nextFrame() }
    @Test(expected = java.io.EOFException::class) fun rejectsTruncatedFrame() { MjpegReader("--frame\r\nContent-Length: 3\r\n\r\na".byteInputStream()).nextFrame() }
    @Test fun noDateVerificationDoesNotBecomeAValidOrConfirmedVerdict() {
        val s = parseSessionState(JSONObject("""{"mode":"combined","qwen_active":false,"date_reading_enabled":false,"extinguishers":[{"id":1,"status":"valid","status_text":"VALID","due_text":"June 2027","confirmed":true}]}"""))
        assertEquals(false, s.qwenActive)
        assertEquals(1, s.extinguishers.size)
        assertEquals("unverified", s.extinguishers.single().status)
        assertNull(s.extinguishers.single().dueText)
        assertFalse(s.extinguishers.single().confirmed)
    }
    @Test fun activeQwenPreservesReadDatesAndConfirmation() {
        val s = parseSessionState(JSONObject("""{"mode":"combined","qwen_active":true,"date_reading_enabled":true,"extinguishers":[{"id":1,"status":"valid","status_text":"VALID","due_text":"June 2027","confirmed":true}]}"""))
        assertEquals(true, s.qwenActive)
        assertEquals("valid", s.extinguishers.single().status)
        assertEquals("June 2027", s.extinguishers.single().dueText)
        assertTrue(s.extinguishers.single().confirmed)
    }

    @Test fun inactiveQwenOverridesContradictoryDateVerificationFlags() {
        val s = parseSessionState(JSONObject("""{"mode":"combined","qwen_active":false,"date_reading_enabled":true,"extinguishers":[{"id":1,"status":"valid","status_text":"VALID","due_text":"June 2027","confirmed":true}]}"""))
        assertFalse(s.dateReadingEnabled)
        assertNull(s.extinguishers.single().dueText)
        assertFalse(s.extinguishers.single().confirmed)
    }

}
