import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/services/audio_events.dart';

void main() {
  group('AudioEvent.parse', () {
    test('bt_headset connected/disconnected', () {
      final connected =
          AudioEvent.parse({'type': 'bt_headset', 'connected': true});
      expect(connected.btConnected, isTrue);
      expect(connected.mediaButton, isNull);

      final unplugged =
          AudioEvent.parse({'type': 'bt_headset', 'connected': false});
      expect(unplugged.btConnected, isFalse);
      expect(unplugged.mediaButton, isNull);
    });

    test('media buttons map to their actions', () {
      const buttons = <String, MediaButton>{
        'play': MediaButton.play,
        'pause': MediaButton.pause,
        'play_pause': MediaButton.playPause,
        'next': MediaButton.next,
        'previous': MediaButton.previous,
      };
      for (final entry in buttons.entries) {
        final event =
            AudioEvent.parse({'type': 'media_button', 'action': entry.key});
        expect(event.mediaButton, entry.value);
        expect(event.btConnected, isNull);
      }
    });

    test('unknown type and unknown action parse to none', () {
      final unknown = AudioEvent.parse({'type': 'nope'});
      expect(unknown.btConnected, isNull);
      expect(unknown.mediaButton, isNull);

      final badAction =
          AudioEvent.parse({'type': 'media_button', 'action': 'hold'});
      expect(badAction.mediaButton, isNull);
    });
  });
}
