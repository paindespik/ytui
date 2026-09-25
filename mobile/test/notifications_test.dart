import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/models.dart';
import 'package:ytui_mobile/services/notifications.dart';

void main() {
  final now = DateTime.utc(2026, 9, 20, 12);
  final nowMs = now.millisecondsSinceEpoch;

  group('reconcileLives', () {
    test('notifies a live once, then keeps it seen while listed', () {
      final first = reconcileLives(const [], ['youtube:a'], now);
      expect(first.notify, ['youtube:a']);
      final second = reconcileLives(
          first.seen, ['youtube:a'], now.add(const Duration(minutes: 2)));
      expect(second.notify, isEmpty);
    });

    test('a live missing from one poll does not notify again on return', () {
      // The backend's live list blinks when an upstream check fails.
      final seen = reconcileLives(const [], ['twitch:x:1'], now).seen;
      final blink = reconcileLives(
          seen, const [], now.add(const Duration(minutes: 2)));
      expect(blink.notify, isEmpty);
      final back = reconcileLives(
          blink.seen, ['twitch:x:1'], now.add(const Duration(minutes: 4)));
      expect(back.notify, isEmpty);
    });

    test('forgets a live after the grace period', () {
      final seen = reconcileLives(const [], ['youtube:a'], now).seen;
      final later = reconcileLives(
          seen, const [], now.add(kLiveSeenGrace + const Duration(minutes: 1)));
      expect(later.seen, isEmpty);
    });

    test('a live still listed after a long gap stays seen', () {
      // A phone in Doze polls hours apart: the stale timestamp must be
      // refreshed before it could expire.
      final seen = reconcileLives(const [], ['youtube:a'], now).seen;
      final later = reconcileLives(
          seen, ['youtube:a'], now.add(const Duration(hours: 5)));
      expect(later.notify, isEmpty);
      expect(later.seen, hasLength(1));
    });

    test('bare keys from the previous format count as seen now', () {
      final result = reconcileLives(['youtube:a'], ['youtube:a'], now);
      expect(result.notify, isEmpty);
      expect(result.seen, ['youtube:a\t$nowMs']);
    });

    test('notifies each new key once even if listed twice', () {
      final result =
          reconcileLives(const [], ['youtube:a', 'youtube:a', 'twitch:b'], now);
      expect(result.notify, ['youtube:a', 'twitch:b']);
    });
  });

  group('mergeSeenVideoIds', () {
    test('never drops an id of the current feed', () {
      // Regression: "keep the last 500 of seen ∪ feed" pruned the oldest
      // entries — the feed's own ids — and re-notified them every poll.
      final feed = [for (var i = 0; i < 450; i++) 'youtube:f$i'];
      final stored = [
        ...feed,
        for (var i = 0; i < 200; i++) 'youtube:old$i',
      ];
      final merged = mergeSeenVideoIds(stored, feed, staleCap: 100);
      expect(merged.toSet().containsAll(feed), isTrue);
      expect(merged, hasLength(550));
    });

    test('keeps the most recent stale ids, newest last', () {
      final merged = mergeSeenVideoIds(
        ['a', 'b', 'c', 'd'],
        ['d', 'e'],
        staleCap: 2,
      );
      expect(merged, ['b', 'c', 'd', 'e']);
    });

    test('ids that leave the feed become the newest stale entries', () {
      final first = mergeSeenVideoIds(const ['old'], ['x', 'y']);
      final second = mergeSeenVideoIds(first, ['y', 'z'], staleCap: 1);
      expect(second, ['x', 'y', 'z']);
    });
  });

  group('videosToNotify', () {
    Video video(String id, {Duration? age, String kind = 'video'}) => Video(
          videoId: id,
          title: id,
          kind: kind,
          published: age == null ? null : now.subtract(age),
        );

    test('skips seen, old and non-video entries', () {
      final result = videosToNotify(
        [
          video('new', age: const Duration(hours: 2)),
          video('seen', age: const Duration(hours: 1)),
          video('backlog', age: const Duration(days: 30)),
          video('playlist', age: const Duration(hours: 1), kind: 'playlist'),
          video('undated'),
        ],
        {'youtube:seen'},
        now,
      );
      expect(result.map((v) => v.videoId), ['new', 'undated']);
    });
  });

  group('payload', () {
    test('round-trips the video to play on tap', () {
      const original = Video(
        videoId: 'name:ab12',
        title: 'Titre',
        channelTitle: 'Chaîne',
        platform: 'odysee',
      );
      final back = videoFromPayload(videoPayload(original));
      expect(back?.videoId, 'name:ab12');
      expect(back?.platform, 'odysee');
      expect(back?.channelTitle, 'Chaîne');
    });

    test('ignores empty or foreign payloads', () {
      expect(videoFromPayload(null), isNull);
      expect(videoFromPayload(''), isNull);
      expect(videoFromPayload('not json'), isNull);
      expect(videoFromPayload('[1, 2]'), isNull);
    });
  });
}
