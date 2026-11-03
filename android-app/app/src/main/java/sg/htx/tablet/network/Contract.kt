package sg.htx.tablet.network

import org.json.JSONArray
import org.json.JSONObject
import sg.htx.tablet.data.*

private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }
private fun JSONObject.box(): BoundingBox {
    val b = getJSONArray("box")
    require(b.length() == 4)
    return BoundingBox(b.getDouble(0).toFloat(), b.getDouble(1).toFloat(), b.getDouble(2).toFloat(), b.getDouble(3).toFloat())
}
fun parseLiveResult(json: String): LiveResult {
    val j = JSONObject(json)
    require(j.getString("type") == "result" && j.optString("mode", "exits") == "exits")
    return LiveResult(j.getLong("seq"), j.getDouble("t"), j.getJSONArray("exits").objects().map {
        val blockers = it.getJSONArray("blockers")
        ExitDetection(it.getInt("id"), it.getBoolean("exit_sign"),
            if (it.isNull("confirmed")) null else it.getBoolean("confirmed"),
            it.getString("status"), it.getString("status_text"), it.getDouble("since_s"),
            (0 until blockers.length()).map { i -> blockers.getString(i) }, it.box())
    }, j.getJSONArray("obstacles").objects().map {
        ObstacleDetection(it.getInt("id"), it.getString("label"),
            if (it.isNull("depth")) null else it.getString("depth"), it.getInt("exit_id"), it.box())
    })
}
fun parseSessionState(j: JSONObject): SessionState {
    val dateReading = j.optBoolean("date_reading_enabled", false) &&
        (!j.has("qwen_active") || j.isNull("qwen_active") || j.optBoolean("qwen_active", false))
    return SessionState(
    if (j.has("video_time_s")) j.getDouble("video_time_s") else null,
    if (j.has("fps")) j.getDouble("fps") else null,
    (j.optJSONArray("exits") ?: JSONArray()).objects().map {
        SessionExit(it.getInt("id"), it.getString("status"), it.getString("status_text"), it.getBoolean("visible"))
    }, j.optString("mode", "exits"), dateReading,
    (j.optJSONArray("extinguishers") ?: JSONArray()).objects().map {
        if (!dateReading) SessionExtinguisher(it.getInt("id"), "unverified", "DATE NOT VERIFIED", null, false, "Date verification unavailable")
        else SessionExtinguisher(it.getInt("id"), it.getString("status"), it.getString("status_text"),
            if (it.isNull("due_text")) null else it.getString("due_text"),
            it.getBoolean("confirmed"), it.optString("text"))
    }, if (j.has("qwen_active") && !j.isNull("qwen_active")) j.getBoolean("qwen_active")
        else if (j.has("date_reading_enabled")) dateReading else null)
}

/** Live results describe the exact uploaded, rotated/cropped image, with normalized XYXY boxes. */
fun parseCameraResult(j: JSONObject, frame: CameraFrame): CameraResult {
    require(j.getString("type") == "result" && j.getString("mode") == "combined")
    val stateJson = JSONObject(j.toString()).put("video_time_s", j.getDouble("t"))
    val exits = stateJson.optJSONArray("exits") ?: JSONArray()
    exits.objects().forEach { it.put("visible", true) }
    val state = parseSessionState(stateJson)
    val boxes = mutableListOf<CameraBox>()
    exits.objects().forEach {
        val checking = state.qwenActive == true && !it.optBoolean("confirmed", false)
        boxes.add(CameraBox(it.box(), "EXIT · " + if (checking) "CHECKING" else it.getString("status_text"),
            if (checking) "unknown" else it.getString("status")))
    }
    (j.optJSONArray("obstacles") ?: JSONArray()).objects().forEach {
        boxes.add(CameraBox(it.box(), it.getString("label"), "obstacle"))
    }
    (j.optJSONArray("extinguishers") ?: JSONArray()).objects().forEach {
        val reading = state.dateReadingEnabled && (it.optBoolean("reading", false) || it.getString("status") == "reading")
        val verified = state.dateReadingEnabled && it.optBoolean("confirmed", false)
        val status = when {
            reading -> "reading"
            !verified -> "unverified"
            else -> it.getString("status")
        }
        val label = when {
            !state.dateReadingEnabled -> "Dates not verified"
            reading -> "READING"
            !verified -> it.getString("status_text") + " · unverified"
            else -> it.getString("status_text")
        }
        boxes.add(CameraBox(it.box(), "Extinguisher · $label", status))
    }
    return CameraResult(j.getLong("seq"), frame.copy(jpeg = byteArrayOf()), state, boxes)
}
