package sg.htx.tablet

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.RectF
import android.view.Surface
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.transform.CoordinateTransform
import androidx.camera.view.transform.ImageProxyTransformFactory
import androidx.lifecycle.*
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Focused native-camera probe: no backend dependency and no camera pixels saved. */
@RunWith(AndroidJUnit4::class)
class CameraBindingTest {
    @androidx.annotation.OptIn(androidx.camera.view.TransformExperimental::class)
    @Test fun rearAnalysisFramesRotateTransformAndCloseWithLifecycle() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assumeTrue("Grant camera permission through the app before running this probe",
            context.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
        ActivityScenario.launch(MainActivity::class.java).use {
            val provider = ProcessCameraProvider.getInstance(context).get(15,TimeUnit.SECONDS)
            assumeTrue(provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA))
            val executor = Executors.newSingleThreadExecutor()
            val frames = CountDownLatch(3)
            val errors = ConcurrentLinkedQueue<Throwable>()
            val stopped = AtomicBoolean(false)
            val closed = CountDownLatch(1)
            val analysis = ImageAnalysis.Builder().setTargetRotation(Surface.ROTATION_90)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888).build()
            val owner = object : LifecycleOwner {
                val registry = LifecycleRegistry(this)
                override val lifecycle: Lifecycle get() = registry
            }
            val factory = ImageProxyTransformFactory().apply { isUsingCropRect=true;isUsingRotationDegrees=true }
            analysis.setAnalyzer(executor) { image ->
                try {
                    assertTrue(image.width>0 && image.height>0)
                    assertTrue(image.imageInfo.rotationDegrees in setOf(0,90,180,270))
                    val bitmap=image.toBitmap()
                    assertEquals(image.width,bitmap.width);assertEquals(image.height,bitmap.height)
                    bitmap.recycle()
                    val transform=factory.getOutputTransform(image)
                    val rect=RectF(0f,0f,10f,10f)
                    CoordinateTransform(transform,transform).mapRect(rect)
                    assertEquals(0f,rect.left,.01f);assertEquals(10f,rect.right,.01f)
                } catch (error:Throwable) { errors.add(error) }
                finally { image.close();frames.countDown() }
            }
            var camera:Camera?=null
            val observer=Observer<CameraState> { state -> if(stopped.get() && state.type==CameraState.Type.CLOSED) closed.countDown() }
            try {
                instrumentation.runOnMainSync {
                    owner.registry.currentState=Lifecycle.State.RESUMED
                    camera=provider.bindToLifecycle(owner,CameraSelector.DEFAULT_BACK_CAMERA,analysis)
                    camera!!.cameraInfo.cameraState.observeForever(observer)
                    assertTrue(provider.isBound(analysis))
                }
                assertTrue("No usable rear-camera frames",frames.await(12,TimeUnit.SECONDS))
                assertTrue(errors.joinToString { it.toString() },errors.isEmpty())
                instrumentation.runOnMainSync { stopped.set(true);owner.registry.currentState=Lifecycle.State.CREATED }
                assertTrue("Camera did not close when stopped",closed.await(8,TimeUnit.SECONDS))
            } finally {
                instrumentation.runOnMainSync {
                    camera?.cameraInfo?.cameraState?.removeObserver(observer)
                    analysis.clearAnalyzer();provider.unbind(analysis)
                    owner.registry.currentState=Lifecycle.State.DESTROYED
                    assertFalse(provider.isBound(analysis))
                }
                executor.shutdownNow()
            }
        }
    }
}
