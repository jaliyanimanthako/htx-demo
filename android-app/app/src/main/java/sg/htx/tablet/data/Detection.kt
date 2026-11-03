package sg.htx.tablet.data

// Wire box from ExitMonitor.live_result: normalized XYXY, not XYWH.
data class BoundingBox(val left: Float, val top: Float, val right: Float, val bottom: Float)
data class ExitDetection(
    val id: Int, val exitSign: Boolean, val confirmed: Boolean?, val status: String,
    val statusText: String, val sinceS: Double, val blockers: List<String>, val box: BoundingBox
)
data class ObstacleDetection(val id: Int, val label: String, val depth: String?, val exitId: Int, val box: BoundingBox)
data class LiveResult(val seq: Long, val t: Double, val exits: List<ExitDetection>, val obstacles: List<ObstacleDetection>)
data class SessionExit(val id: Int, val status: String, val statusText: String, val visible: Boolean)
data class SessionExtinguisher(val id: Int, val status: String, val statusText: String,
    val dueText: String?, val confirmed: Boolean, val text: String)
data class SessionState(val videoTimeS: Double?, val fps: Double?, val exits: List<SessionExit>,
    val mode: String = "exits", val dateReadingEnabled: Boolean = false,
    val extinguishers: List<SessionExtinguisher> = emptyList(), val qwenActive: Boolean? = null) {
    val hazardCount get() = exits.count { it.visible && it.status in HAZARD_STATUSES }
    val checking get() = exits.any { it.visible && it.status == "unknown" }
}
val HAZARD_STATUSES = setOf("obstructed", "partially_obstructed")
