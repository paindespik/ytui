// Resume battery: the "restart where I left off" semantics (resumeStart in
// api/models.dart) and the HTTP contract the player relies on to persist and
// read back a position (recordWatch / savePosition / resume in api/client.dart).
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
  Future<ResponseBody> fetch(
      RequestOptions options, Stream<Uint8List>? _, Future<void>? __) async {
    requests.add(options);
    final key = '${options.method} ${options.path}';
    final route = routes[key];
    if (route == null) {
      return ResponseBody.fromString(jsonEncode({'detail': 'not found'}), 404,
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
  group('resumeStart — where playback must start', () {
    test('never watched: starts at 0', () {
      expect(resumeStart(0, 600), 0);
    });

    test('a negative position (garbage) starts at 0', () {
      expect(resumeStart(-5, 600), 0);
    });

    test('barely started: resumes at 1 s', () {
      expect(resumeStart(1, 600), 1);
    });

    test('mid-video: resumes exactly where it stopped', () {
      expect(resumeStart(300, 600), 300);
    });

    test('fractional positions are preserved', () {
      expect(resumeStart(42.5, 600), 42.5);
    });

    test('94 % watched still resumes (just under the threshold)', () {
      expect(resumeStart(564, 600), 564); // 94 %
    });

    test('exactly 95 % counts as finished: restarts at 0', () {
      expect(resumeStart(570, 600), 0); // 95 %
    });

    test('96 % watched counts as finished: restarts at 0', () {
      expect(resumeStart(576, 600), 0); // 96 %
    });

    test('watched to the very end: restarts at 0', () {
      expect(resumeStart(600, 600), 0);
    });

    test('position beyond the duration: restarts at 0', () {
      expect(resumeStart(900, 600), 0);
    });

    test('unknown duration: the stored position is trusted', () {
      // No duration means no way to tell "finished" from "almost finished":
      // resuming is the safe answer (the user asked for that position).
      expect(resumeStart(300, null), 300);
    });

    test('zero duration: the stored position is trusted', () {
      // Guards a division by zero — a 0 duration is a server/demuxer artefact.
      expect(resumeStart(300, 0), 300);
    });

    test('negative duration (garbage) does not zero a valid position', () {
      expect(resumeStart(300, -600), 300);
    });

    test('a 0 position with an unknown duration still starts at 0', () {
      expect(resumeStart(0, null), 0);
    });

    test('threshold is stable on a short video', () {
      expect(resumeStart(8, 10), 8); // 80 %: resumes
      expect(resumeStart(9, 10), 9); // 90 %: resumes
      expect(resumeStart(9.5, 10), 0); // 95 %: finished
    });

    test('threshold is stable on a long video', () {
      expect(resumeStart(3400, 3600), 3400); // ~94.4 %: resumes
      expect(resumeStart(3420, 3600), 0); // exactly 95 %: finished
    });
  });

  group('savePosition — persisting the heartbeat', () {
    test('PUTs position and duration to the history position endpoint',
        () async {
      final adapter = _FakeAdapter({
        'PUT /api/history/abc/position': (204, {}),
      });
      final api = _api({}, adapter: adapter);

      await api.savePosition('abc', 42.5, duration: 600.0);

      final req = adapter.requests.single;
      expect(req.method, 'PUT');
      expect(req.path, '/api/history/abc/position');
      expect(req.data, {'position': 42.5, 'duration': 600.0});
    });

    test('sends a null duration when it is unknown', () async {
      final adapter = _FakeAdapter({
        'PUT /api/history/abc/position': (204, {}),
      });
      final api = _api({}, adapter: adapter);

      await api.savePosition('abc', 10.0);

      expect(adapter.requests.single.data,
          {'position': 10.0, 'duration': null});
    });

    test('percent-encodes composite ids in the path', () async {
      // Odysee/Twitch style ids carry ':' — an unencoded path would 404.
      final adapter = _FakeAdapter({
        'PUT /api/history/ma-video%3Aabc123/position': (204, {}),
      });
      final api = _api({}, adapter: adapter);

      await api.savePosition('ma-video:abc123', 5.0, duration: 60.0);

      expect(adapter.requests.single.path,
          '/api/history/ma-video%3Aabc123/position');
    });

    test('a video missing from history surfaces the 404', () async {
      // The player fires this and forgets, but the failure must not be
      // swallowed inside the client (callers decide).
      final api = _api({
        'PUT /api/history/ghost/position': (404, {'detail': 'Video not in history'}),
      });

      expect(
        api.savePosition('ghost', 1.0, duration: 2.0),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'statusCode', 404)
            .having((e) => e.detail, 'detail', 'Video not in history')),
      );
    });
  });

  group('resume — reading the position back', () {
    test('GETs the resume endpoint and parses position/duration/playlist_id',
        () async {
      final adapter = _FakeAdapter({
        'GET /api/history/abc/resume': (
          200,
          {'position': 42.5, 'duration': 600, 'playlist_id': 'PL7'}
        ),
      });
      final api = _api({}, adapter: adapter);

      final info = await api.resume('abc');

      final req = adapter.requests.single;
      expect(req.method, 'GET');
      expect(req.path, '/api/history/abc/resume');
      expect(info, isNotNull);
      expect(info!.position, 42.5);
      expect(info.duration, 600.0);
      expect(info.playlistId, 'PL7');
    });

    test('integer JSON numbers are read as doubles', () async {
      final api = _api({
        'GET /api/history/abc/resume': (
          200,
          {'position': 42, 'duration': 600, 'playlist_id': ''}
        ),
      });

      final info = await api.resume('abc');
      expect(info!.position, 42.0);
      expect(info.duration, 600.0);
    });

    test('a null duration is preserved (unknown, not zero)', () async {
      // resumeStart must be able to tell "unknown duration" apart from 0.
      final api = _api({
        'GET /api/history/abc/resume': (
          200,
          {'position': 42.0, 'duration': null, 'playlist_id': ''}
        ),
      });

      final info = await api.resume('abc');
      expect(info!.duration, isNull);
      expect(resumeStart(info.position, info.duration), 42.0);
    });

    test('a missing playlist_id defaults to empty', () async {
      final api = _api({
        'GET /api/history/abc/resume': (200, {'position': 1.0}),
      });

      final info = await api.resume('abc');
      expect(info!.playlistId, '');
      expect(info.duration, isNull);
    });

    test('a never-watched video returns null (404 is not an error)', () async {
      final api = _api({
        'GET /api/history/abc/resume': (404, {'detail': 'Video not in history'}),
      });

      expect(await api.resume('abc'), isNull);
    });

    test('a server error is NOT swallowed as "start from 0"', () async {
      // Only 404 means "never watched"; a 500 must surface so the caller can
      // decide, instead of silently losing the resume point.
      final api = _api({
        'GET /api/history/abc/resume': (500, {'detail': 'boom'}),
      });

      expect(
        api.resume('abc'),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'statusCode', 500)),
      );
    });

    test('percent-encodes composite ids in the path', () async {
      final adapter = _FakeAdapter({
        'GET /api/history/ma-video%3Aabc123/resume': (
          200,
          {'position': 3.0, 'duration': 30.0}
        ),
      });
      final api = _api({}, adapter: adapter);

      final info = await api.resume('ma-video:abc123');
      expect(adapter.requests.single.path,
          '/api/history/ma-video%3Aabc123/resume');
      expect(info!.position, 3.0);
    });
  });

  group('recordWatch — history entry before the position exists', () {
    test('POSTs the video payload to /api/history', () async {
      final adapter = _FakeAdapter({
        'POST /api/history': (204, {}),
      });
      final api = _api({}, adapter: adapter);

      await api.recordWatch(const Video(
        videoId: 'abc',
        title: 'Title',
        channelTitle: 'Chan',
        channelId: 'UC1',
        playlistId: 'PL7',
      ));

      final req = adapter.requests.single;
      expect(req.method, 'POST');
      expect(req.path, '/api/history');
      final body = req.data as Map<String, dynamic>;
      final video = body['video'] as Map<String, dynamic>;
      expect(video['video_id'], 'abc');
      expect(video['title'], 'Title');
      expect(video['channel_title'], 'Chan');
      expect(video['playlist_id'], 'PL7');
    });
  });

  group('round trip — save then resume drives the next playback', () {
    test('a mid-video position comes back and is resumed', () async {
      final adapter = _FakeAdapter({
        'PUT /api/history/abc/position': (204, {}),
        'GET /api/history/abc/resume': (
          200,
          {'position': 300.0, 'duration': 600.0, 'playlist_id': ''}
        ),
      });
      final api = _api({}, adapter: adapter);

      await api.savePosition('abc', 300.0, duration: 600.0);
      final info = await api.resume('abc');

      expect(resumeStart(info!.position, info.duration), 300.0);
    });

    test('a position past the 95 % mark comes back but restarts at 0',
        () async {
      final api = _api({
        'GET /api/history/abc/resume': (
          200,
          {'position': 580.0, 'duration': 600.0, 'playlist_id': ''}
        ),
      });

      final info = await api.resume('abc');
      expect(info!.position, 580.0); // stored as-is…
      expect(resumeStart(info.position, info.duration), 0); // …but replayed
    });

    test('a never-watched video starts at 0 without a resume call result',
        () async {
      final api = _api({
        'GET /api/history/new/resume': (404, {'detail': 'Video not in history'}),
      });

      final info = await api.resume('new');
      // Player semantics: no info → start = 0.
      final start = info == null ? 0.0 : resumeStart(info.position, info.duration);
      expect(start, 0.0);
    });
  });
}
