package sg.htx.tablet.network

import java.io.InputStream

/** The repository sends Content-Length per multipart JPEG. Bound headers and frame storage. */
class MjpegReader(private val input: InputStream) {
    private fun line(): String {
        val out = StringBuilder()
        while (out.length < 4096) {
            val c = input.read()
            if (c == -1) throw java.io.EOFException("Video stream ended")
            if (c == 10) return out.toString().trimEnd('\r')
            out.append(c.toChar())
        }
        error("MJPEG header too long")
    }
    fun nextFrame(): ByteArray {
        var length: Int? = null
        var lines = 0
        while (true) {
            val line = line()
            check(++lines <= 64) { "Invalid MJPEG headers" }
            if (line.startsWith("Content-Length:", ignoreCase = true)) length = line.substringAfter(':').trim().toInt()
            if (line.isEmpty() && length != null) break
        }
        val count = requireNotNull(length)
        require(count in 1..8_000_000) { "Invalid MJPEG frame length" }
        val bytes = ByteArray(count)
        var offset = 0
        while (offset < count) {
            val read = input.read(bytes, offset, count - offset)
            if (read < 0) throw java.io.EOFException("Incomplete JPEG")
            offset += read
        }
        return bytes
    }
}
