/// Bluetooth headset + media button (Bose earbuds) events, emitted by the
/// Android activity through the `dev.ytui.app/audio` EventChannel:
///   {"type": "bt_headset", "connected": true|false}
///   {"type": "media_button", "action": "play"|"pause"|"play_pause"|"next"|"previous"}
library;

/// A media button press as reported by [AudioEvent].
enum MediaButton { play, pause, playPause, next, previous }

/// One parsed audio event: at most one of [btConnected] / [mediaButton] is
/// set, and neither is set for an unknown event type.
class AudioEvent {
  const AudioEvent({this.btConnected, this.mediaButton});

  const AudioEvent.none()
      : btConnected = null,
        mediaButton = null;

  /// Bluetooth headset state: `true` = connected, `false` = disconnected,
  /// `null` = this is not a headset event.
  final bool? btConnected;

  /// The media button press, `null` when this is not a button event.
  final MediaButton? mediaButton;

  static const _buttons = <String, MediaButton>{
    'play': MediaButton.play,
    'pause': MediaButton.pause,
    'play_pause': MediaButton.playPause,
    'next': MediaButton.next,
    'previous': MediaButton.previous,
  };

  factory AudioEvent.parse(Map<Object?, Object?> raw) {
    switch (raw['type']) {
      case 'bt_headset':
        return AudioEvent(btConnected: raw['connected'] == true);
      case 'media_button':
        final action = raw['action'];
        return AudioEvent(
          mediaButton: action is String ? _buttons[action] : null,
        );
      default:
        return const AudioEvent.none();
    }
  }
}
