import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/services/network.dart';

void main() {
  group('proxiedStreamUrl', () {
    test('routes byte streams through /api/proxy with the URL encoded', () {
      final url = proxiedStreamUrl(
        'https://ytui.example.org',
        'https://rr1---sn-abc.googlevideo.com/videoplayback?expire=1&sig=a%3Db',
      );
      expect(
        url,
        'https://ytui.example.org/api/proxy?url='
        'https%3A%2F%2Frr1---sn-abc.googlevideo.com%2Fvideoplayback'
        '%3Fexpire%3D1%26sig%3Da%253Db',
      );
    });

    test('routes playlists through /api/proxy/hls', () {
      final url = proxiedStreamUrl(
        'https://ytui.example.org',
        'https://manifest.googlevideo.com/api/manifest/hls_variant/x.m3u8',
        playlist: true,
      );
      expect(url, startsWith('https://ytui.example.org/api/proxy/hls?url='));
      expect(url, isNot(contains('m3u8?'))); // fully percent-encoded
    });

    test('keeps the upstream URL round-trippable', () {
      const upstream = 'https://seed12.bitchute.com/x/y.mp4?a=1&b=2';
      final url = proxiedStreamUrl('https://s', upstream);
      final query = Uri.parse(url).queryParameters['url'];
      expect(query, upstream);
    });
  });

  group('isCellularNow', () {
    test('reports false when the platform channel is unavailable', () async {
      // In tests no MethodChannel handler exists: the lookup must fail closed
      // (Wi-Fi behaviour) instead of throwing into the player.
      TestWidgetsFlutterBinding.ensureInitialized();
      expect(await isCellularNow(), isFalse);
    });
  });
}
