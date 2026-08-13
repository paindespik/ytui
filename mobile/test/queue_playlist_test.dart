// Playlist playback battery: what the queue must do when the user plays a
// whole playlist ("Play all" / tap on an entry, cf. screens/playlist_yt.dart
// and screens/local_playlists.dart), walks through it, reorders it, or starts
// another playlist mid-flight.
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/models.dart';
import 'package:ytui_mobile/state/queue.dart';

Video _v(String id) => Video(videoId: id, title: id);

/// A playlist of [n] videos: P1..Pn (1-based, like the UI numbering).
List<Video> _playlist(int n, {String prefix = 'P'}) =>
    List.generate(n, (i) => _v('$prefix${i + 1}'));

List<String> _ids(PlayQueue q) => q.items.map((v) => v.videoId).toList();

void main() {
  late ProviderContainer container;
  late QueueNotifier queue;

  PlayQueue read() => container.read(queueProvider);

  setUp(() {
    container = ProviderContainer();
    queue = container.read(queueProvider.notifier);
  });

  tearDown(() => container.dispose());

  group('play() — starting a playlist', () {
    test('"Play all" starts at the first entry', () {
      final videos = _playlist(10);
      queue.play(videos);

      final q = read();
      expect(_ids(q), _ids(PlayQueue(items: videos)));
      expect(q.index, 0);
      expect(q.current!.videoId, 'P1');
      expect(q.hasPrevious, isFalse);
      expect(q.hasNext, isTrue);
    });

    test('tapping an entry starts at that index (middle)', () {
      queue.play(_playlist(10), startIndex: 4);

      final q = read();
      expect(q.current!.videoId, 'P5');
      expect(q.index, 4);
      expect(q.hasPrevious, isTrue);
      expect(q.hasNext, isTrue);
      // The whole playlist stays queued, before and after the tapped entry.
      expect(q.items.length, 10);
    });

    test('tapping the last entry leaves nothing upcoming', () {
      queue.play(_playlist(10), startIndex: 9);

      final q = read();
      expect(q.current!.videoId, 'P10');
      expect(q.index, 9);
      expect(q.hasNext, isFalse);
      expect(q.hasPrevious, isTrue);
    });

    test('a single-video playlist has neither next nor previous', () {
      queue.play(_playlist(1));

      final q = read();
      expect(q.current!.videoId, 'P1');
      expect(q.hasNext, isFalse);
      expect(q.hasPrevious, isFalse);
    });

    test('play() of an empty list leaves nothing to play', () {
      queue.play(const []);

      final q = read();
      expect(q.items, isEmpty);
      expect(q.current, isNull);
      expect(q.hasNext, isFalse);
      expect(q.hasPrevious, isFalse);
    });

    test('out-of-range startIndex degrades to "nothing playing", not a crash',
        () {
      // Defensive: an index past the end must never throw when the player
      // reads `current` (a RangeError here would blank the player screen).
      queue.play(_playlist(3), startIndex: 7);

      final q = read();
      expect(q.current, isNull);
      expect(q.items.length, 3);
    });

    test('negative startIndex degrades to "nothing playing", not a crash', () {
      queue.play(_playlist(3), startIndex: -1);

      final q = read();
      expect(q.current, isNull);
      expect(q.hasPrevious, isFalse);
    });

    test('the queue copies the caller list (later mutations do not leak in)',
        () {
      final videos = _playlist(3);
      queue.play(videos);
      videos.add(_v('LEAK'));

      expect(_ids(read()), ['P1', 'P2', 'P3']);
    });
  });

  group('next() — walking a whole playlist', () {
    test('advances through all 10 entries in order, then stops', () {
      queue.play(_playlist(10));

      final seen = <String>[read().current!.videoId];
      for (var i = 0; i < 9; i++) {
        queue.next();
        seen.add(read().current!.videoId);
      }
      expect(seen, [
        'P1', 'P2', 'P3', 'P4', 'P5', //
        'P6', 'P7', 'P8', 'P9', 'P10',
      ]);

      final q = read();
      expect(q.hasNext, isFalse);
      expect(q.index, 9);

      // End of playlist: next() must not move (the player chains a suggestion
      // instead — cf. _autoplayNext in screens/player.dart).
      queue.next();
      expect(read().index, 9);
      expect(read().current!.videoId, 'P10');
    });

    test('walking never drops or reorders the playlist', () {
      final videos = _playlist(10);
      queue.play(videos);
      for (var i = 0; i < 9; i++) {
        queue.next();
      }
      expect(_ids(read()), _ids(PlayQueue(items: videos)));
    });

    test('resuming a playlist mid-way still reaches the end', () {
      queue.play(_playlist(10), startIndex: 7); // tapped P8
      queue.next();
      expect(read().current!.videoId, 'P9');
      queue.next();
      expect(read().current!.videoId, 'P10');
      expect(read().hasNext, isFalse);
    });
  });

  group('previous() — walking back', () {
    test('goes back one entry at a time down to the first', () {
      queue.play(_playlist(10), startIndex: 9);

      final seen = <String>[];
      while (read().hasPrevious) {
        queue.previous();
        seen.add(read().current!.videoId);
      }
      expect(seen, ['P9', 'P8', 'P7', 'P6', 'P5', 'P4', 'P3', 'P2', 'P1']);
      expect(read().index, 0);
    });

    test('is a no-op on the first entry', () {
      queue.play(_playlist(10));
      expect(read().hasPrevious, isFalse);

      queue.previous();
      expect(read().index, 0);
      expect(read().current!.videoId, 'P1');
    });

    test('next then previous returns to the same entry', () {
      queue.play(_playlist(10), startIndex: 3);
      queue.next();
      queue.previous();

      final q = read();
      expect(q.index, 3);
      expect(q.current!.videoId, 'P4');
    });
  });

  group('enqueue() during playlist playback', () {
    test('appends after the whole playlist without moving the cursor', () {
      queue.play(_playlist(3), startIndex: 1);
      queue.enqueue(_v('EXTRA'));

      final q = read();
      expect(_ids(q), ['P1', 'P2', 'P3', 'EXTRA']);
      expect(q.index, 1);
      expect(q.current!.videoId, 'P2'); // playback untouched
    });

    test('an entry enqueued mid-playlist plays last', () {
      queue.play(_playlist(3));
      queue.enqueue(_v('EXTRA'));

      queue.next(); // P2
      queue.next(); // P3
      expect(read().current!.videoId, 'P3');
      queue.next();
      expect(read().current!.videoId, 'EXTRA');
      expect(read().hasNext, isFalse);
    });

    test('enqueue after the playlist ended keeps playback chainable', () {
      queue.play(_playlist(2), startIndex: 1);
      expect(read().hasNext, isFalse);

      // Autoplay pattern at the end of a playlist: enqueue + next.
      queue.enqueue(_v('R1'));
      expect(read().hasNext, isTrue);
      queue.next();
      expect(read().current!.videoId, 'R1');
    });
  });

  group('jumpTo() — tapping an entry of the playing playlist', () {
    test('jumping forward keeps the skipped entries queued after it', () {
      queue.play(_playlist(5)); // playing P1

      queue.jumpTo(3); // tap P4

      final q = read();
      expect(q.current!.videoId, 'P4');
      // P2/P3 were skipped, not dropped: they follow P4 in order.
      expect(_ids(q), ['P1', 'P4', 'P2', 'P3', 'P5']);
      expect(q.index, 1);
      expect(q.hasPrevious, isTrue);
    });

    test('jumping backward to an already-played entry does not duplicate it',
        () {
      queue.play(_playlist(5), startIndex: 3); // playing P4

      queue.jumpTo(1); // tap P2 (already played)

      final q = read();
      expect(q.current!.videoId, 'P2');
      expect(_ids(q), ['P1', 'P3', 'P4', 'P2', 'P5']);
      expect(q.index, 3);
      expect(_ids(q).where((id) => id == 'P2').length, 1);
      // Everything is still there: nothing lost by the reorder.
      expect(_ids(q)..sort(), ['P1', 'P2', 'P3', 'P4', 'P5']);
    });

    test('previous() after a backward jump returns to the entry played before',
        () {
      queue.play(_playlist(5), startIndex: 3); // playing P4
      queue.jumpTo(1); // tap P2
      queue.previous();

      expect(read().current!.videoId, 'P4');
    });

    test('a playlist with duplicate videos keeps both copies (positional)', () {
      // Same video twice in the playlist (legitimate: a track repeated in a
      // YouTube playlist). Filtering by value would silently delete one.
      queue.play([_v('A'), _v('DUP'), _v('B'), _v('DUP'), _v('C')]);

      queue.jumpTo(3); // tap the SECOND DUP

      final q = read();
      // The tapped entry plays now, and the skipped ones follow it (same
      // reordering as the no-duplicate case).
      expect(q.current!.videoId, 'DUP');
      expect(_ids(q), ['A', 'DUP', 'DUP', 'B', 'C']);
      expect(q.index, 1);
      // Neither copy was swallowed by a value-based filter.
      expect(_ids(q).where((id) => id == 'DUP').length, 2);
      expect(q.items.length, 5);
    });

    test('jumping onto a duplicate from behind keeps the other copy upcoming',
        () {
      queue.play([_v('DUP'), _v('A'), _v('DUP'), _v('B')]); // playing DUP #1

      queue.jumpTo(2); // tap DUP #2

      final q = read();
      expect(q.current!.videoId, 'DUP');
      expect(_ids(q), ['DUP', 'DUP', 'A', 'B']);
      expect(q.index, 1);
      // The first copy stays in history, the tapped one plays: nothing lost.
      expect(_ids(q).where((id) => id == 'DUP').length, 2);
      expect(q.items.length, 4);
    });

    test('is a no-op on the currently playing index', () {
      queue.play(_playlist(5), startIndex: 2);
      queue.jumpTo(2);

      final q = read();
      expect(_ids(q), ['P1', 'P2', 'P3', 'P4', 'P5']);
      expect(q.index, 2);
      expect(q.current!.videoId, 'P3');
    });

    test('is a no-op past the end of the playlist', () {
      queue.play(_playlist(5), startIndex: 1);
      queue.jumpTo(5);
      queue.jumpTo(99);

      final q = read();
      expect(_ids(q), ['P1', 'P2', 'P3', 'P4', 'P5']);
      expect(q.index, 1);
    });

    test('is a no-op for a negative index', () {
      queue.play(_playlist(5), startIndex: 1);
      queue.jumpTo(-1);

      final q = read();
      expect(_ids(q), ['P1', 'P2', 'P3', 'P4', 'P5']);
      expect(q.index, 1);
    });

    test('jumping to the last entry then next() stops cleanly', () {
      queue.play(_playlist(4)); // playing P1
      queue.jumpTo(3); // tap P4

      // P4 now plays with P2/P3 still upcoming behind it.
      expect(read().current!.videoId, 'P4');
      expect(read().hasNext, isTrue);
      queue.next();
      expect(read().current!.videoId, 'P2');
      queue.next();
      expect(read().current!.videoId, 'P3');
      expect(read().hasNext, isFalse);
    });

    test('repeated jumps keep the playlist intact', () {
      queue.play(_playlist(6));
      queue.jumpTo(4);
      queue.jumpTo(1);
      queue.jumpTo(5);

      final q = read();
      expect(_ids(q)..sort(), ['P1', 'P2', 'P3', 'P4', 'P5', 'P6']);
      expect(q.items.length, 6);
      expect(q.current, isNotNull);
    });
  });

  group('switching playlists / clearing', () {
    test('starting another playlist replaces the queue entirely', () {
      queue.play(_playlist(5), startIndex: 2); // deep into playlist A
      queue.enqueue(_v('EXTRA'));

      queue.play(_playlist(3, prefix: 'Q')); // "Play all" on playlist B

      final q = read();
      expect(_ids(q), ['Q1', 'Q2', 'Q3']);
      expect(q.index, 0);
      expect(q.current!.videoId, 'Q1');
      expect(q.hasPrevious, isFalse);
    });

    test('starting another playlist at an entry replaces and positions', () {
      queue.play(_playlist(5), startIndex: 4);

      queue.play(_playlist(4, prefix: 'Q'), startIndex: 2);

      final q = read();
      expect(_ids(q), ['Q1', 'Q2', 'Q3', 'Q4']);
      expect(q.index, 2);
      expect(q.current!.videoId, 'Q3');
    });

    test('playing a single video from history replaces the playlist', () {
      queue.play(_playlist(5), startIndex: 3);

      queue.play([_v('ONE')]); // cf. screens/history.dart

      final q = read();
      expect(_ids(q), ['ONE']);
      expect(q.index, 0);
      expect(q.hasNext, isFalse);
      expect(q.hasPrevious, isFalse);
    });

    test('clear() empties the queue', () {
      queue.play(_playlist(5), startIndex: 2);
      queue.clear();

      final q = read();
      expect(q.items, isEmpty);
      expect(q.index, 0);
      expect(q.current, isNull);
      expect(q.hasNext, isFalse);
      expect(q.hasPrevious, isFalse);
    });

    test('a playlist can be started again after clear()', () {
      queue.play(_playlist(3));
      queue.clear();
      queue.play(_playlist(2, prefix: 'Q'), startIndex: 1);

      final q = read();
      expect(_ids(q), ['Q1', 'Q2']);
      expect(q.current!.videoId, 'Q2');
    });
  });

  group('state propagation (the player listens to the queue)', () {
    test('every mutation emits a new state to listeners', () {
      // screens/player.dart reloads on queueProvider changes: identical state
      // objects would stall playback advance.
      final seen = <String?>[];
      container.listen<PlayQueue>(
        queueProvider,
        (_, next) => seen.add(next.current?.videoId),
        fireImmediately: false,
      );

      queue.play(_playlist(3));
      queue.next();
      queue.jumpTo(2);
      queue.previous();
      queue.clear();

      expect(seen, ['P1', 'P2', 'P3', 'P2', null]);
    });
  });
}
