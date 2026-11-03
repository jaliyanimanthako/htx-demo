package sg.htx.tablet

import androidx.compose.ui.graphics.Color
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.data.*
import sg.htx.tablet.network.parseCameraResult
import sg.htx.tablet.ui.annotationColor

class AnnotationPresentationTest {
    private val frame = CameraFrame(byteArrayOf(), 0, CameraProjection(0f, 0f, 960f, 0f, 0f, 540f))
    @Test fun liveCameraMatchesRecordedVideoPresentationFixtures() {
        val cases = JSONArray(javaClass.classLoader!!.getResourceAsStream("annotation_cases.json")!!
            .bufferedReader().use { it.readText() })
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val boxes = parseCameraResult(case.getJSONObject("wire"), frame).boxes
            val expected = case.getJSONArray("expected")
            assertEquals(case.getString("name"), expected.length(), boxes.size)
            boxes.forEachIndexed { i, box ->
                val value = expected.getJSONObject(i)
                assertEquals(case.getString("name"), value.getString("label"), box.label)
                assertEquals(value.getString("status"), box.status)
                assertEquals(Color(("FF" + value.getString("color")).toLong(16)), annotationColor(box.status))
            }
        }
    }
    private fun result(status: String, confirmed: Boolean = true, reading: Boolean = false, qwen: Boolean = true): CameraResult {
        val box = JSONArray("[0.1,0.1,0.8,0.8]")
        val json = JSONObject().put("type", "result").put("mode", "combined").put("seq", 1).put("t", 0)
            .put("qwen_active", qwen).put("date_reading_enabled", qwen)
            .put("exits", JSONArray().put(JSONObject().put("id", 30).put("status", "clear")
                .put("status_text", "CLEAR").put("confirmed", confirmed).put("box", box)))
            .put("obstacles", JSONArray().put(JSONObject().put("id", 44).put("label", "chair").put("box", box)))
            .put("extinguishers", JSONArray().put(JSONObject().put("id", 52).put("status", status)
                .put("status_text", status.uppercase()).put("confirmed", confirmed).put("reading", reading)
                .put("due_text", JSONObject.NULL).put("box", box)))
        return parseCameraResult(json, frame)
    }

    @Test fun statusColorsAreIntentionalAndUnknownValuesStayNeutral() {
        val cases = mapOf("unknown" to 0xFFA0A0A0, "reading" to 0xFFA0A0A0,
            "unverified" to 0xFFA0A0A0, "unclear" to 0xFFA0A0A0, "unreadable" to 0xFFA0A0A0,
            "future_status" to 0xFFA0A0A0, "clear" to 0xFF00C800, "valid" to 0xFF00C800,
            "partially_obstructed" to 0xFFFFC800, "expiring_soon" to 0xFFFFC800,
            "obstructed" to 0xFFFF0000, "expired" to 0xFFFF0000, "obstacle" to 0xFFFF0000)
        cases.forEach { (status, color) -> assertEquals(status, Color(color), annotationColor(status)) }
    }

    @Test fun liveLabelsHideTrackingNumbersWithoutChangingIdentityOrObjectNames() {
        val result = result("valid")
        assertEquals("EXIT · CLEAR", result.boxes[0].label)
        assertEquals("chair", result.boxes[1].label)
        assertEquals("Extinguisher · VALID", result.boxes[2].label)
        assertTrue(result.boxes.none { it.label.contains('#') })
        assertEquals(30, result.state.exits.single().id)
        assertEquals(52, result.state.extinguishers.single().id)
    }

    @Test fun provisionalDatesAndPendingReadsNeverUseVerifiedColors() {
        for (status in listOf("valid", "expiring_soon", "expired")) {
            assertEquals("unverified", result(status, confirmed = false).boxes.last().status)
            assertEquals("reading", result(status, reading = true).boxes.last().status)
            assertEquals("unverified", result(status, qwen = false).boxes.last().status)
            assertEquals(status, result(status).boxes.last().status)
        }
        assertEquals("unknown", result("reading", confirmed = false).boxes.first().status)
        assertEquals("EXIT · CHECKING", result("reading", confirmed = false).boxes.first().label)
        assertEquals("clear", result("reading", confirmed = false, qwen = false).boxes.first().status)
    }
}
