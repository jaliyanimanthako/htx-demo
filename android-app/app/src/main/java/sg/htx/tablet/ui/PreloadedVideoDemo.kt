package sg.htx.tablet.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView

/** Offline assets only. No inferred hazards or synthetic boxes are attached to these recordings. */
@androidx.annotation.OptIn(UnstableApi::class)
@Composable
fun PreloadedVideoDemo(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val clips = remember { context.assets.list("demo_videos")?.filter { it.endsWith(".mp4") }?.sorted().orEmpty() }
    var selected by rememberSaveable { mutableStateOf(clips.firstOrNull().orEmpty()) }
    var position by rememberSaveable(selected) { mutableStateOf(0L) }
    var playing by rememberSaveable(selected) { mutableStateOf(true) }
    var menu by remember { mutableStateOf(false) }
    var player by remember { mutableStateOf<ExoPlayer?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) }

    DisposableEffect(owner, selected, retry) {
        fun release() {
            player?.let { position = it.currentPosition; playing = it.playWhenReady; it.release() }
            player = null
        }
        fun start() {
            if (player != null || selected.isEmpty()) return
            error = null
            loading = true
            player = ExoPlayer.Builder(context).build().apply {
                repeatMode = Player.REPEAT_MODE_ONE
                addListener(object : Player.Listener {
                    override fun onPlaybackStateChanged(playbackState: Int) {
                        loading = playbackState == Player.STATE_BUFFERING
                    }
                    override fun onPlayerError(failure: PlaybackException) {
                        loading = false
                        error = "Unable to play this clip (${failure.errorCodeName})"
                    }
                })
                setMediaItem(MediaItem.fromUri("asset:///demo_videos/$selected"))
                seekTo(position)
                playWhenReady = playing
                prepare()
            }
        }
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> start()
                Lifecycle.Event.ON_STOP -> release()
                else -> Unit
            }
        }
        owner.lifecycle.addObserver(observer)
        if (owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) start()
        onDispose { owner.lifecycle.removeObserver(observer); release() }
    }

    Column(modifier) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box {
                OutlinedButton(onClick = { menu = true }, enabled = clips.isNotEmpty()) {
                    Text(if (selected.isEmpty()) "No bundled videos" else selected.substringBeforeLast('.').uppercase() + " ▾")
                }
                DropdownMenu(menu, onDismissRequest = { menu = false }) {
                    clips.forEach { clip ->
                        DropdownMenuItem(text = { Text(clip.substringBeforeLast('.').uppercase()) }, onClick = {
                            selected = clip
                            menu = false
                        })
                    }
                }
            }
            Spacer(Modifier.width(16.dp))
            Text("${clips.size} preloaded clips • loops automatically • tap video for controls",
                style = MaterialTheme.typography.bodySmall)
        }
        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            AndroidView(factory = { PlayerView(it).apply {
                resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                useController = true
                setShowBuffering(PlayerView.SHOW_BUFFERING_WHEN_PLAYING)
            } }, update = { it.player = player }, onRelease = { it.player = null }, modifier = Modifier.fillMaxSize())
            if (loading && error == null && clips.isNotEmpty()) CircularProgressIndicator()
            if (error != null || clips.isEmpty()) {
                Column(Modifier.background(Color.Black).padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(error ?: "No bundled videos. Run the demo asset preparation script and rebuild.")
                    if (error != null) TextButton(onClick = { retry++ }) { Text("Retry playback") }
                }
            }
        }
    }
}
