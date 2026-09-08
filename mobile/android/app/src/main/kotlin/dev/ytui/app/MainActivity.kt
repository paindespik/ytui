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
import android.view.KeyEvent
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
