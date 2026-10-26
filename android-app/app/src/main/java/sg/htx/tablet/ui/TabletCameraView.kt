package sg.htx.tablet.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.hardware.display.DisplayManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Size
import android.view.View
import androidx.camera.core.*
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.camera.view.transform.CoordinateTransform
import androidx.camera.view.transform.ImageProxyTransformFactory
import androidx.camera.view.transform.OutputTransform
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size as ComposeSize
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.*
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import sg.htx.tablet.data.*
import sg.htx.tablet.viewmodel.FireDetectionViewModel
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicLong

/** Rear-only preview and analysis share a ViewPort; uploads are cropped/rotated before JPEG encoding. */
@androidx.annotation.OptIn(androidx.camera.view.TransformExperimental::class)
@Composable
fun TabletCameraView(model: FireDetectionViewModel, capture: CameraCapture, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val result by model.cameraResult.collectAsStateWithLifecycle()
    val view = remember { PreviewView(context).apply {
        implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        scaleType = PreviewView.ScaleType.FILL_CENTER
    } }
    var viewport by remember { mutableStateOf(0L) }
    Box(modifier.clipToBounds()) {
        AndroidView(factory = { view }, modifier = Modifier.fillMaxSize())
        var overlayVisible by remember { mutableStateOf(false) }
        LaunchedEffect(result, viewport) {
            val value = result
            val age = if (value == null) Long.MAX_VALUE else (SystemClock.elapsedRealtimeNanos()-value.frame.capturedAtNanos)/1_000_000
            overlayVisible = value != null && value.frame.viewport == viewport && age in 0..CameraTuning.RESULT_MAX_AGE_MS
            if (overlayVisible) { delay(CameraTuning.RESULT_MAX_AGE_MS-age); overlayVisible = false }
        }
        if (overlayVisible) result?.takeIf { it.frame.viewport == viewport }?.let { CameraDetectionOverlay(it, Modifier.fillMaxSize()) }
    }
    DisposableEffect(view, owner, capture.generation) {
        val executor = Executors.newSingleThreadExecutor()
        val target = AtomicReference<OutputTransform?>(null)
        val activeBinding = AtomicLong(0)
        val future = ProcessCameraProvider.getInstance(context)
        var provider: ProcessCameraProvider? = null
        var preview: Preview? = null
        var analysis: ImageAnalysis? = null
        var disposed = false
        var bindingKey = ""
        fun unbind() {
            activeBinding.set(0)
            target.set(null)
            analysis?.clearAnalyzer()
            val cases = listOfNotNull(preview, analysis).toTypedArray()
            if (cases.isNotEmpty()) provider?.unbind(*cases)
            preview = null; analysis = null; bindingKey = ""
        }
        fun bind() {
            if (disposed || !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED) ||
                !model.cameraCapture.value.enabled || model.cameraCapture.value.generation != capture.generation ||
                view.width == 0 || view.height == 0 || provider == null) return
            val rotation = view.display?.rotation ?: return
            val key = "${view.width}:${view.height}:$rotation"
            if (key == bindingKey) return
            unbind()
            try {
                val port = view.viewPort ?: return
                val binding = SystemClock.elapsedRealtimeNanos()
                viewport = binding
                activeBinding.set(binding)
                val resolution = ResolutionSelector.Builder().setResolutionStrategy(
                    ResolutionStrategy(Size(1280,720), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER)).build()
                val p = Preview.Builder().setResolutionSelector(resolution).setTargetRotation(rotation).build()
                val a = ImageAnalysis.Builder().setResolutionSelector(resolution).setTargetRotation(rotation)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888).build()
                preview = p; analysis = a
                p.setSurfaceProvider(view.surfaceProvider)
                val factory = ImageProxyTransformFactory().apply { isUsingCropRect = true; isUsingRotationDegrees = true }
                var lastFrame = 0L
                a.setAnalyzer(executor) { image ->
                    try {
                        val now = SystemClock.elapsedRealtimeNanos()
                        val destination = target.get()
                        if (destination != null && activeBinding.get() == binding && now-lastFrame >= 1_000_000_000L/CameraTuning.MAX_FPS) {
                            lastFrame = now
                            val source = factory.getOutputTransform(image)
                            val original = image.toBitmap()
                            val crop = image.cropRect
                            val cropped = Bitmap.createBitmap(original, crop.left, crop.top, crop.width(), crop.height())
                            val rotated = Bitmap.createBitmap(cropped, 0, 0, cropped.width, cropped.height,
                                Matrix().apply { postRotate(image.imageInfo.rotationDegrees.toFloat()) }, true)
                            val width = minOf(CameraTuning.WIDTH, rotated.width)
                            val scaled = Bitmap.createScaledBitmap(rotated, width, (rotated.height.toLong()*width/rotated.width).toInt().coerceAtLeast(1), true)
                            try {
                                val corners = floatArrayOf(0f,0f,rotated.width.toFloat(),0f,0f,rotated.height.toFloat())
                                CoordinateTransform(source, destination).mapPoints(corners)
                                val projection = CameraProjection(corners[0],corners[1],corners[2],corners[3],corners[4],corners[5])
                                val jpeg = ByteArrayOutputStream().use { output ->
                                    scaled.compress(Bitmap.CompressFormat.JPEG, CameraTuning.JPEG_QUALITY, output)
                                    output.toByteArray()
                                }
                                if (activeBinding.get() == binding)
                                    model.cameraFrame(capture.generation, CameraFrame(jpeg, now, projection, binding))
                            } finally {
                                setOf(scaled,rotated,cropped,original).forEach { it.recycle() }
                            }
                        }
                    } catch (e: Exception) {
                        // A failed conversion/upload must not crash CameraX's analyzer thread.
                        ContextCompat.getMainExecutor(context).execute {
                            if (!disposed && activeBinding.get() == binding) model.cameraFailed(capture.generation)
                        }
                    } finally { image.close() }
                }
                provider!!.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA,
                    UseCaseGroup.Builder().setViewPort(port).addUseCase(p).addUseCase(a).build())
                bindingKey = key
            } catch (e: Exception) { unbind(); model.cameraFailed(capture.generation) }
        }
        val stream = Observer<PreviewView.StreamState> { state ->
            if (!disposed && state == PreviewView.StreamState.STREAMING) {
                target.set(view.outputTransform)
                model.cameraOpened(capture.generation)
            }
        }
        view.previewStreamState.observe(owner, stream)
        val listener = View.OnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> bind() }
        view.addOnLayoutChangeListener(listener)
        val displays = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        val displayListener = object : DisplayManager.DisplayListener {
            override fun onDisplayAdded(id: Int) {}
            override fun onDisplayRemoved(id: Int) {}
            override fun onDisplayChanged(id: Int) { if (id == view.display?.displayId) bind() }
        }
        displays.registerDisplayListener(displayListener, Handler(Looper.getMainLooper()))
        val lifecycle = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) unbind()
            if (event == Lifecycle.Event.ON_START) view.post { bind() }
        }
        owner.lifecycle.addObserver(lifecycle)
        future.addListener({
            if (!disposed) try { provider = future.get(); bind() }
            catch (e: Exception) { model.cameraFailed(capture.generation) }
        }, ContextCompat.getMainExecutor(context))
        onDispose {
            disposed = true
            unbind()
            owner.lifecycle.removeObserver(lifecycle)
            displays.unregisterDisplayListener(displayListener)
            view.removeOnLayoutChangeListener(listener)
            view.previewStreamState.removeObserver(stream)
            executor.shutdownNow()
        }
    }
}

@Composable
private fun CameraDetectionOverlay(result: CameraResult, modifier: Modifier) {
    val paint = remember { android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.WHITE; isFakeBoldText = true
    } }
    Canvas(modifier) {
        paint.textSize = 14f*density
        for (item in result.boxes) {
            val box = result.frame.projection.map(item.box)
            val color = annotationColor(item.status)
            drawRect(color, Offset(box.left,box.top), ComposeSize((box.right-box.left).coerceAtLeast(0f),(box.bottom-box.top).coerceAtLeast(0f)), style = Stroke(2.5f*density))
            val textWidth = paint.measureText(item.label)
            val x = box.left.coerceIn(0f, (size.width-textWidth-12).coerceAtLeast(0f))
            val y = (box.top-4).coerceIn(paint.textSize+8, size.height.coerceAtLeast(paint.textSize+8))
            drawRect(Color.Black.copy(alpha=.85f), Offset(x,y-paint.textSize-4), ComposeSize(textWidth+12,paint.textSize+10))
            drawContext.canvas.nativeCanvas.drawText(item.label,x+6,y,paint)
        }
    }
}
