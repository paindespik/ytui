import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/client.dart';
import 'package:ytui_mobile/api/models.dart';

/// Minimal fake adapter: routes requests to canned responses and records them.
class _FakeAdapter implements HttpClientAdapter {
  final Map<String, (int, Object)> routes; // "METHOD path" -> (status, body)
  final List<RequestOptions> requests = [];

  _FakeAdapter(this.routes);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? _,
      Future<void>? __) async {
    requests.add(options);
    final key = '${options.method} ${options.path}';
    final route = routes[key];
    if (route == null) {
      return ResponseBody.fromString(
          jsonEncode({'detail': 'not found'}), 404,
          headers: _jsonHeaders);
    }
    final (status, body) = route;
    return ResponseBody.fromString(jsonEncode(body), status,
        headers: _jsonHeaders);
  }

  static final _jsonHeaders = {
    Headers.contentTypeHeader: [Headers.jsonContentType],
  };

  @override
  void close({bool force = false}) {}
}

YtuiApi _api(Map<String, (int, Object)> routes, {_FakeAdapter? adapter}) {
  final dio = Dio(BaseOptions(
    baseUrl: 'https://server.test',
    headers: {'Authorization': 'Bearer sekret'},
  ));
  dio.httpClientAdapter = adapter ?? _FakeAdapter(routes);
  return YtuiApi(baseUrl: 'https://server.test', token: 'sekret', dio: dio);
}

void main() {
  test('sends the bearer token', () async {
    final adapter = _FakeAdapter({
      'GET /health': (200, {'status': 'ok', 'version': '0.2.0'}),
    });
    final api = _api({}, adapter: adapter);
    await api.health();
    expect(adapter.requests.single.headers['Authorization'], 'Bearer sekret');
  });

  test('feed parses videos and warnings', () async {
    final api = _api({
      'GET /api/feed': (
        200,
        {
          'videos': [
            {'video_id': 'a', 'title': 'one'},
          ],
          'warnings': ['stale'],
        }
      ),
    });
    final feed = await api.feed();
    expect(feed.videos.single.videoId, 'a');
    expect(feed.warnings, ['stale']);
  });

  test('search parses items', () async {
    final api = _api({
      'GET /api/search': (
        200,
        {
          'items': [
            {'video_id': 'a', 'title': 't', 'kind': 'channel'},
          ]
        }
      ),
    });
    final items = await api.search('query');
    expect(items.single.kind, 'channel');
  });

  test('videoStreams parses StreamInfo', () async {
    final api = _api({
      'GET /api/videos/abc/streams': (
        200,
        {'kind': 'hls', 'url': 'https://x/m.m3u8', 'title': 't'}
      ),
    });
    final info = await api.videoStreams('abc');
    expect(info.kind, 'hls');
    expect(info.url, 'https://x/m.m3u8');
  });

  test('HTTP errors raise ApiException with detail', () async {
    final api = _api({
      'GET /api/feed': (502, {'detail': 'upstream broke'}),
    });
    expect(
      api.feed(),
      throwsA(isA<ApiException>()
          .having((e) => e.statusCode, 'statusCode', 502)
          .having((e) => e.detail, 'detail', 'upstream broke')),
    );
  });

  test('connection failure raises ApiException with status 0', () async {
    final dio = Dio(BaseOptions(
      baseUrl: 'http://127.0.0.1:1',
      connectTimeout: const Duration(milliseconds: 200),
    ));
    final api =
        YtuiApi(baseUrl: 'http://127.0.0.1:1', token: 'x', dio: dio);
    expect(
      api.health(),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'statusCode', 0)),
    );
  });

  test('resume returns null on 404', () async {
    final api = _api({
      'GET /api/history/abc/resume': (404, {'detail': 'Video not in history'}),
    });
    expect(await api.resume('abc'), isNull);
  });

  test('resume parses ResumeInfo', () async {
    final api = _api({
      'GET /api/history/abc/resume': (
        200,
        {'position': 42.5, 'duration': 600, 'playlist_id': ''}
      ),
    });
    final info = await api.resume('abc');
    expect(info!.position, 42.5);
    expect(info.duration, 600.0);
  });

  test('createPlaylist returns null on 409', () async {
    final api = _api({
      'POST /api/playlists': (409, {'detail': 'taken'}),
    });
    expect(await api.createPlaylist('name'), isNull);
  });

  test('addPlaylistItem returns false on 409', () async {
    final api = _api({
      'POST /api/playlists/1/items': (409, {'detail': 'dup'}),
    });
    final ok = await api.addPlaylistItem(
        1, Video.fromJson({'video_id': 'a', 'title': 't'}));
    expect(ok, isFalse);
  });

  test('watchedIds returns a set', () async {
    final api = _api({
      'GET /api/history/watched-ids': (
        200,
        {
          'ids': ['a', 'b', 'a']
        }
      ),
    });
    expect(await api.watchedIds(), {'a', 'b'});
  });

  test('recordWatch posts the video json', () async {
    final adapter = _FakeAdapter({
      'POST /api/history': (204, {}),
    });
    final api = _api({}, adapter: adapter);
    await api.recordWatch(Video.fromJson({'video_id': 'a', 'title': 't'}));
    final body = adapter.requests.single.data as Map<String, dynamic>;
    expect((body['video'] as Map<String, dynamic>)['video_id'], 'a');
  });

  test('channels parses the list', () async {
    final api = _api({
      'GET /api/channels': (
        200,
        [
          {'ref': '@x', 'channel_id': 'UC1', 'title': 'X'},
        ]
      ),
    });
    final channels = await api.channels();
    expect(channels.single.channelId, 'UC1');
  });

  test('lives parses LiveItem list', () async {
    final api = _api({
      'GET /api/lives': (
        200,
        [
          {
            'video': {'video_id': 'a', 'title': 'live'},
            'detected_at': '2026-07-11T10:00:00Z',
          }
        ]
      ),
    });
    final lives = await api.lives();
    expect(lives.single.video.videoId, 'a');
  });
}
