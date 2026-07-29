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

    test('odysee video round-trips platform and colon id', () {
      final video = Video.fromJson({
        'video_id': 'ma-video:abc123',
        'title': 'Odysee video',
        'channel_id': '@chan:1',
        'platform': 'odysee',
        'url': 'https://odysee.com/ma-video:abc123',
      });
      expect(video.platform, 'odysee');
      expect(video.videoId, 'ma-video:abc123');
      expect(video.url, 'https://odysee.com/ma-video:abc123');
      final json = video.toJson();
      expect(json['platform'], 'odysee');
      expect(json['video_id'], 'ma-video:abc123');
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
        'height': 1080,
      });
      expect(info.kind, 'split');
      expect(info.audioUrl, isNotNull);
      expect(info.expiresAt, DateTime.utc(2026, 7, 11, 16));
      expect(info.height, 1080);
    });

    test('hls with nulls', () {
      final info =
          StreamInfo.fromJson({'kind': 'hls', 'url': 'https://x/m.m3u8'});
      expect(info.videoUrl, isNull);
      expect(info.expiresAt, isNull);
      expect(info.height, isNull);
      expect(info.subtitles, isEmpty);
    });

    test('parses subtitle tracks', () {
      final info = StreamInfo.fromJson({
        'kind': 'hls',
        'url': 'https://x/m.m3u8',
        'subtitles': [
          {'lang': 'fr', 'label': 'French', 'url': 'u1', 'auto': false},
          {'lang': 'en', 'label': 'en (auto)', 'url': 'u2', 'auto': true},
        ],
      });
      expect(info.subtitles, hasLength(2));
      expect(info.subtitles.first.lang, 'fr');
      expect(info.subtitles.first.label, 'French');
      expect(info.subtitles.last.auto, isTrue);
    });
  });

  group('SponsorSegment', () {
    test('parses backend payload', () {
      final seg = SponsorSegment.fromJson({
        'category': 'sponsor',
        'start': 12.5,
        'end': 42,
      });
      expect(seg.category, 'sponsor');
      expect(seg.start, 12.5);
      expect(seg.end, 42.0);
    });

    test('missing fields default safely', () {
      final seg = SponsorSegment.fromJson(const {});
      expect(seg.category, '');
      expect(seg.start, 0.0);
      expect(seg.end, 0.0);
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

    test('Comment parses full payload', () {
      final comment = Comment.fromJson({
        'comment_id': 'c1',
        'text': 'hello',
        'channel_name': '@bob',
        'timestamp': 1700000000,
        'replies': 2,
        'likes': 5,
        'dislikes': 1,
        'is_pinned': true,
      });
      expect(comment.commentId, 'c1');
      expect(comment.text, 'hello');
      expect(comment.channelName, '@bob');
      expect(comment.timestamp, 1700000000);
      expect(comment.replies, 2);
      expect(comment.likes, 5);
      expect(comment.dislikes, 1);
      expect(comment.isPinned, isTrue);
    });

    test('Comment tolerates minimal payload', () {
      final comment = Comment.fromJson({'comment_id': 'c1', 'text': 't'});
      expect(comment.likes, 0);
      expect(comment.timestamp, isNull);
      expect(comment.isPinned, isFalse);
    });

    test('CommentsPage parses items and total', () {
      final page = CommentsPage.fromJson({
        'items': [
          {'comment_id': 'a', 'text': 'one'},
          {'comment_id': 'b', 'text': 'two'},
        ],
        'total': 12,
      });
      expect(page.items, hasLength(2));
      expect(page.total, 12);
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

    test('LiveItem tolerates a bad detected_at without throwing', () {
      final live = LiveItem.fromJson({
        'video': {'video_id': 'a', 'title': 'live'},
        'detected_at': 'not-a-date',
      });
      expect(live.video.videoId, 'a');
      expect(live.detectedAt, DateTime.fromMillisecondsSinceEpoch(0));
    });
  });
}
