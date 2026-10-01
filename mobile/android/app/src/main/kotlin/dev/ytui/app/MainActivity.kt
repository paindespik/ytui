package dev.ytui.app

import android.app.UiModeManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.view.KeyEvent
import com.pravera.flutter_foreground_task.service.ForegroundService
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_DEVICE)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "isTv" -> result.success(isTv())
                    "isCellular" -> result.success(isCellular())
                    // Keeps the media session's playback state in sync with
                    // the player (Dart side: player.dart _playingSub).
                    "setMediaPlaybackState" -> {
                        setMediaSessionPlaybackState(call.arguments as? Boolean ?: true)
                        result.success(null)
                    }
                    // libmpv instance lifetime, see onDestroy.
                    "playerCreated" -> {
                        livePlayers++
                        result.success(null)
                    }
                    "playerDisposed" -> {
                        livePlayers = (livePlayers - 1).coerceAtLeast(0)
                        result.success(null)
                    }
                    // System share sheet for a video link (player "Partager").
                    "share" -> {
                        val text = call.argument<String>("text")
                        if (text.isNullOrEmpty()) {
                            result.error("no_text", "nothing to share", null)
                        } else {
                            share(text, call.argument<String>("subject"))
                            result.success(null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
        // Audio events for the Dart player: Bluetooth headset plug state and
        // media buttons (Bose earbuds) — see lib/services/audio_events.dart.
        EventChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_AUDIO)
            .setStreamHandler(object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                    audioEventSink = events
                    startAudioMonitors()
                }

                override fun onCancel(arguments: Any?) {
                    stopAudioMonitors()
                }
            })
    }

    /** The monitors normally stop with the Dart subscription (onCancel); an
     *  activity torn down without it must not leak the receiver or leave a
     *  ghost media session catching the media keys. */
    override fun onDestroy() {
        stopAudioMonitors()
        audioEventSink = null
        if (livePlayers > 0) {
            // The Flutter engine dies with this activity, but a media_kit
            // player is native (libmpv) and only Dart can release it: when
            // the activity goes while the player screen is still mounted —
            // the task swiped away from recents, kept alive by the playback
            // foreground service — libmpv survives with callbacks into a dead
            // isolate. Playing, its next frame crashes on the detached engine
            // ("FlutterJNI is not attached to native"); paused, it lingers
            // until the app is reopened, and the next video's libmpv log lines
            // (routed process-wide to the first instance) or its audiotrack
            // output (JNI ids shared by every instance) abort the process:
            // "Callback invoked after it has been deleted" / "mid == null".
            // Nothing can drive that player any more, so end playback the way
            // Android ends an app without a foreground service: stop the
            // service (no sticky restart) and take the process down.
            stopService(Intent(this, ForegroundService::class.java))
            Process.killProcess(Process.myPid())
        }
        super.onDestroy()
    }

    /** media_kit players alive in this activity's engine: reported by
     *  player.dart when it creates one and once its dispose() completed. */
    private var livePlayers = 0

    private fun share(text: String, subject: String?) {
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, text)
            if (!subject.isNullOrEmpty()) putExtra(Intent.EXTRA_SUBJECT, subject)
        }
        startActivity(Intent.createChooser(send, null))
    }

    // ------------------------------------------------------------------
    // Audio events: Bluetooth (A2DP/HFP) headset plug state and the Bose
    // earbuds' buttons. The monitors live exactly as long as the player
    // screen holds its subscription (onListen/onCancel).
    // ------------------------------------------------------------------

    private val mainHandler = Handler(Looper.getMainLooper())
    private var audioEventSink: EventChannel.EventSink? = null
    private var audioMonitorsStarted = false
    private var audioDeviceCallback: AudioDeviceCallback? = null
    private var mediaButtonReceiver: BroadcastReceiver? = null
    private var mediaSession: MediaSession? = null

    /** Emits {"type": ...} to Dart; [extra] adds the type-specific fields. */
    private fun emitAudioEvent(type: String, extra: Map<String, Any>) {
        val sink = audioEventSink ?: return
        val payload = mutableMapOf<String, Any>("type" to type)
        payload.putAll(extra)
        mainHandler.post {
            try {
                sink.success(payload)
            } catch (_: Exception) {
                // Sink closed in the meantime (subscription cancelled).
            }
        }
    }

    private fun startAudioMonitors() {
        if (audioMonitorsStarted) return
        audioMonitorsStarted = true
        // Bluetooth music (A2DP) / voice (HFP) headset connect/disconnect.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val callback = object : AudioDeviceCallback() {
                override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>) {
                    if (addedDevices.any { it.isBluetoothAudio() }) {
                        emitAudioEvent("bt_headset", mapOf("connected" to true))
                    }
                }

                override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>) {
                    // A2DP is the music path: when it goes, the earbuds are
                    // gone. An HFP-only drop (a call ending) must not pause.
                    if (removedDevices.any { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP }) {
                        emitAudioEvent("bt_headset", mapOf("connected" to false))
                    }
                }
            }
            audioDeviceCallback = callback
            audioManager.registerAudioDeviceCallback(callback, mainHandler)
        }
        // Media buttons (Bose earbuds): while the player is alive this app
        // owns a MediaSession, which makes the system route media key events
        // to us (a bare BroadcastReceiver loses to any other app's session,
        // e.g. Teams'). The session's playback state is synced from Dart via
        // the "setMediaPlaybackState" method; the framework then maps the
        // pressed key onto the matching callback below (a single play/pause
        // tap becomes onPlay/onPause according to the reported state).
        val session = MediaSession(applicationContext, "ytui")
        session.setCallback(object : MediaSession.Callback() {
            override fun onPlay() =
                emitAudioEvent("media_button", mapOf("action" to "play"))

            override fun onPause() =
                emitAudioEvent("media_button", mapOf("action" to "pause"))

            override fun onSkipToNext() =
                emitAudioEvent("media_button", mapOf("action" to "next"))

            override fun onSkipToPrevious() =
                emitAudioEvent("media_button", mapOf("action" to "previous"))
        })
        mediaSession = session // before the state call: it reads this field
        setMediaSessionPlaybackState(false)
        session.setActive(true)
        // Fallback: when no media session anywhere is active the system
        // broadcasts the KeyEvent instead of routing it to a session.
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                val keyEvent = mediaKeyEvent(intent) ?: return
                val action = when (keyEvent.keyCode) {
                    KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> "play_pause"
                    KeyEvent.KEYCODE_MEDIA_PLAY -> "play"
                    KeyEvent.KEYCODE_MEDIA_PAUSE -> "pause"
                    KeyEvent.KEYCODE_MEDIA_NEXT -> "next"
                    KeyEvent.KEYCODE_MEDIA_PREVIOUS -> "previous"
                    else -> return
                }
                emitAudioEvent("media_button", mapOf("action" to action))
            }
        }
        mediaButtonReceiver = receiver
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(
                receiver,
                IntentFilter(Intent.ACTION_MEDIA_BUTTON),
                Context.RECEIVER_NOT_EXPORTED,
            )
        } else {
            registerReceiver(receiver, IntentFilter(Intent.ACTION_MEDIA_BUTTON))
        }
    }

    private fun stopAudioMonitors() {
        if (!audioMonitorsStarted) return
        audioMonitorsStarted = false
        audioDeviceCallback?.let { callback ->
            (getSystemService(Context.AUDIO_SERVICE) as AudioManager)
                .unregisterAudioDeviceCallback(callback)
        }
        audioDeviceCallback = null
        mediaButtonReceiver?.let { unregisterReceiver(it) }
        mediaButtonReceiver = null
        mediaSession?.let { session ->
            session.setActive(false)
            session.release()
        }
        mediaSession = null
    }

    /** Advertises the player's state so the framework maps the Bose button
     *  presses onto the right callback (play or pause) and the lock screen
     *  shows the right controls. */
    private fun setMediaSessionPlaybackState(playing: Boolean) {
        val session = mediaSession ?: return
        session.setPlaybackState(
            PlaybackState.Builder()
                .setActions(
                    PlaybackState.ACTION_PLAY or
                        PlaybackState.ACTION_PAUSE or
                        PlaybackState.ACTION_PLAY_PAUSE or
                        PlaybackState.ACTION_SKIP_TO_NEXT or
                        PlaybackState.ACTION_SKIP_TO_PREVIOUS
                )
                .setState(
                    if (playing) PlaybackState.STATE_PLAYING
                    else PlaybackState.STATE_PAUSED,
                    0L,
                    1f,
                )
                .build()
        )
    }

    private fun mediaKeyEvent(intent: Intent): KeyEvent? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT, KeyEvent::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT)
        }

    private fun AudioDeviceInfo.isBluetoothAudio(): Boolean =
        type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
            type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
            type == AudioDeviceInfo.TYPE_BLE_HEADSET

    /** Active network is mobile data: playback then caps quality and proxies bytes. */
    private fun isCellular(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
    }

    /** Android TV / projector: no touchscreen, everything is driven by the D-pad. */
    private fun isTv(): Boolean {
        if (packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK) ||
            packageManager.hasSystemFeature(PackageManager.FEATURE_TELEVISION)
        ) {
            return true
        }
        val uiMode = getSystemService(Context.UI_MODE_SERVICE) as UiModeManager
        return uiMode.currentModeType == Configuration.UI_MODE_TYPE_TELEVISION
    }

    private companion object {
        const val CHANNEL_DEVICE = "dev.ytui.app/device"
        const val CHANNEL_AUDIO = "dev.ytui.app/audio"
    }
}
