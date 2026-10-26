package sg.htx.tablet

import org.junit.Assert.*
import org.junit.Test
import sg.htx.tablet.data.BoundingBox
import sg.htx.tablet.ui.*

class CoordinatesTest {
    private fun assertBox(expected: BoundingBox, actual: BoundingBox) {
        assertEquals(expected.left, actual.left, 0.01f); assertEquals(expected.top, actual.top, 0.01f)
        assertEquals(expected.right, actual.right, 0.01f); assertEquals(expected.bottom, actual.bottom, 0.01f)
    }
    @Test fun sameAspectRatio() { assertBox(BoundingBox(128f, 144f, 640f, 576f), transformBox(BoundingBox(.1f, .2f, .5f, .8f), 1920f, 1080f, 1280f, 720f)) }
    @Test fun letterbox() { assertBox(BoundingBox(0f, 62.5f, 1200f, 737.5f), transformBox(BoundingBox(0f, 0f, 1f, 1f), 1920f, 1080f, 1200f, 800f)) }
    @Test fun independentBoxes() {
        val boxes = listOf(BoundingBox(0f, 0f, .5f, .5f), BoundingBox(.5f, .5f, 1f, 1f))
        val result = boxes.map { transformBox(it, 1920f, 1080f, 1200f, 800f) }
        assertBox(BoundingBox(0f, 62.5f, 600f, 400f), result[0])
        assertBox(BoundingBox(600f, 400f, 1200f, 737.5f), result[1])
    }
    @Test fun allBoundaries() { assertBox(BoundingBox(0f, 0f, 1280f, 720f), transformBox(BoundingBox(0f, 0f, 1f, 1f), 1920f, 1080f, 1280f, 720f)) }
    @Test fun emptyDetections() { assertTrue(emptyList<BoundingBox>().map { transformBox(it, 1920f, 1080f, 1200f, 800f) }.isEmpty()) }
    @Test fun cropClipsEdges() { assertBox(BoundingBox(0f, 0f, 1200f, 800f), transformBox(BoundingBox(0f, 0f, 1f, 1f), 1920f, 1080f, 1200f, 800f, ScaleMode.CROP)) }
    @Test fun pillarbox() { assertBox(BoundingBox(300f, 0f, 900f, 800f), transformBox(BoundingBox(0f, 0f, 1f, 1f), 600f, 800f, 1200f, 800f)) }
    @Test(expected = IllegalArgumentException::class) fun invalidDimensions() { transformBox(BoundingBox(0f, 0f, 1f, 1f), 0f, 1080f, 1200f, 800f) }
}
