import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/models.dart';

void main() {
  group('Video', () {
    test('parses full backend payload', () {
      final video = Video.fromJson({
        'video_id': 'dQw4w9WgXcQ',
        'title': 'Never Gonna Give You Up',
        'channel_title': 'Rick Astley',
        'channel_id': 'UCuAXFkgsw1L7xaCfnd5JJOw',
        'published': '2026-07-11T10:00:00Z',
        'duration': 212,
        'thumbnail_url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.jpg',
        'kind': 'video',
        'platform': 'youtube',
        'playlist_id': '',
        'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      });
      expect(video.videoId, 'dQw4w9WgXcQ');
      expect(video.published, DateTime.utc(2026, 7, 11, 10));
      expect(video.duration, 212);
      expect(video.url, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ');
      expect(video.durationLabel, '3:32');
    });

    test('tolerates minimal payload with defaults', () {
      final video = Video.fromJson({'video_id': 'abc', 'title': 't'});
      expect(video.kind, 'video');
      expect(video.platform, 'youtube');
      expect(video.published, isNull);
      expect(video.durationLabel, '');
    });

    test('durationLabel formats hours', () {
      final video =
          Video.fromJson({'video_id': 'a', 'title': 't', 'duration': 3725});
      expect(video.durationLabel, '1:02:05');
    });

    test('toJson round-trips the API fields', () {
      final video = Video.fromJson({
        'video_id': 'abc',
        'title': 't',
        'platform': 'bitchute',
        'kind': 'video',
      });
      final json = video.toJson();
      expect(json['video_id'], 'abc');
      expect(json['platform'], 'bitchute');
      expect(json.containsKey('url'), isFalse); // computed server-side
    });
  });

  group('FeedResult', () {
    test('parses videos and warnings', () {
      final feed = FeedResult.fromJson({
        'videos': [
          {'video_id': 'a', 'title': 'one'},
          {'video_id': 'b', 'title': 'two'},
        ],
        'warnings': ['channel X unreachable'],
      });
      expect(feed.videos, hasLength(2));
      expect(feed.warnings, ['channel X unreachable']);
    });

    test('missing warnings defaults to empty', () {
      final feed = FeedResult.fromJson({'videos': []});
      expect(feed.warnings, isEmpty);
    });
  });

  group('StreamInfo', () {
    test('parses split streams', () {
      final info = StreamInfo.fromJson({
        'kind': 'split',
        'url': 'https://v.example/video.mp4',
        'video_url': 'https://v.example/video.mp4',
        'audio_url': 'https://v.example/audio.m4a',
        'title': 't',
        'duration': 100,
        'expires_at': '2026-07-11T16:00:00Z',
      });
      expect(info.kind, 'split');
      expect(info.audioUrl, isNotNull);
      expect(info.expiresAt, DateTime.utc(2026, 7, 11, 16));
    });

    test('hls with nulls', () {
      final info =
          StreamInfo.fromJson({'kind': 'hls', 'url': 'https://x/m.m3u8'});
      expect(info.videoUrl, isNull);
      expect(info.expiresAt, isNull);
    });
  });

  group('resumeStart', () {
    test('resumes mid-video', () {
      expect(resumeStart(120, 600), 120);
    });

    test('restarts when ≥95% watched', () {
      expect(resumeStart(590, 600), 0);
    });

    test('zero or negative position restarts', () {
      expect(resumeStart(0, 600), 0);
      expect(resumeStart(-5, 600), 0);
    });

    test('unknown duration keeps the position', () {
      expect(resumeStart(120, null), 120);
    });
  });

  group('other models', () {
    test('HistoryEntry parses', () {
      final entry = HistoryEntry.fromJson({
        'video': {'video_id': 'a', 'title': 't'},
        'watched_at': 1700000000.5,
        'position': 42,
      });
      expect(entry.watchedAt, 1700000000.5);
      expect(entry.position, 42.0);
    });

    test('LocalPlaylist parses', () {
      final playlist = LocalPlaylist.fromJson(
          {'id': 3, 'name': 'watch later', 'created_at': 1.0, 'count': 7});
      expect(playlist.id, 3);
      expect(playlist.count, 7);
    });

    test('LiveItem parses', () {
      final live = LiveItem.fromJson({
        'video': {'video_id': 'a', 'title': 'live!'},
        'detected_at': '2026-07-11T10:00:00Z',
      });
      expect(live.video.title, 'live!');
    });

    test('FollowedChannel parses', () {
      final channel = FollowedChannel.fromJson({
        'ref': '@LinusTechTips',
        'channel_id': 'UCX',
        'title': 'LTT',
        'platform': 'youtube',
      });
      expect(channel.ref, '@LinusTechTips');
    });
  });
}
