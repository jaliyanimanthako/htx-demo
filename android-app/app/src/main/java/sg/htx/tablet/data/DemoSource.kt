package sg.htx.tablet.data

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import sg.htx.tablet.network.parseLiveResult

object DemoSource {
    // Original schematic sample image, no network or camera required.
    fun image(): Bitmap = Bitmap.createBitmap(1280, 720, Bitmap.Config.ARGB_8888).also {
        val canvas = Canvas(it)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        canvas.drawColor(Color.rgb(31, 43, 55))
        fun rect(l: Float, t: Float, r: Float, b: Float, color: Int) {
            paint.color = color; canvas.drawRect(l, t, r, b, paint)
        }
        rect(0f, 550f, 1280f, 720f, Color.rgb(54, 65, 74))
        rect(128f, 144f, 448f, 634f, Color.rgb(88, 103, 113))
        rect(832f, 144f, 1152f, 634f, Color.rgb(88, 103, 113))
        rect(210f, 420f, 430f, 630f, Color.rgb(145, 109, 61))
        rect(900f, 470f, 1130f, 630f, Color.rgb(145, 109, 61))
        paint.color = Color.WHITE; paint.textSize = 30f
        canvas.drawText("EXIT A", 200f, 120f, paint)
        canvas.drawText("EXIT B", 900f, 120f, paint)
        paint.textSize = 22f
        canvas.drawText("SCHEMATIC DEMO • not a real camera feed", 30f, 685f, paint)
    }
    fun result(empty: Boolean) = parseLiveResult(if (empty) """
        {"type":"result","mode":"exits","seq":2,"t":2.0,"exits":[],"obstacles":[]}
    """ else """
        {"type":"result","mode":"exits","seq":1,"t":1.0,
         "exits":[
          {"id":1,"exit_sign":true,"confirmed":true,"status":"obstructed","status_text":"BLOCKED","since_s":3.0,"blockers":["cardboard box"],"box":[0.1,0.2,0.35,0.88]},
          {"id":2,"exit_sign":true,"confirmed":true,"status":"partially_obstructed","status_text":"PARTIAL","since_s":1.0,"blockers":["crate"],"box":[0.65,0.2,0.9,0.88]}],
         "obstacles":[
          {"id":3,"label":"cardboard box","depth":"floor","exit_id":1,"box":[0.164,0.583,0.336,0.875]},
          {"id":4,"label":"crate","depth":"floor","exit_id":2,"box":[0.703,0.653,0.883,0.875]}]}
    """)
}
