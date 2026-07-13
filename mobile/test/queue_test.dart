import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/models.dart';
import 'package:ytui_mobile/state/queue.dart';

Video _v(String id) => Video(videoId: id, title: id);

List<String> _ids(PlayQueue q) => q.items.map((v) => v.videoId).toList();

void main() {
  late ProviderContainer container;
  late QueueNotifier queue;

  setUp(() {
    container = ProviderContainer();
    queue = container.read(queueProvider.notifier);
  });

  tearDown(() => container.dispose());

  group('enqueue (suggestion tap)', () {
    test('appends to end without changing current', () {
      queue.play([_v('V1')]);
      queue.enqueue(_v('S1'));
      queue.enqueue(_v('S2'));

      final q = container.read(queueProvider);
      expect(_ids(q), ['V1', 'S1', 'S2']);
      expect(q.index, 0);
      expect(q.current!.videoId, 'V1'); // still playing V1, not interrupted
      expect(q.hasNext, isTrue);
    });

    test('preserves click order across multiple enqueues', () {
      queue.play([_v('V1')]);
      for (final id in ['A', 'B', 'C']) {
        queue.enqueue(_v(id));
      }
      expect(_ids(container.read(queueProvider)), ['V1', 'A', 'B', 'C']);
      expect(container.read(queueProvider).index, 0);
    });

    test('enqueue on empty queue seeds it without auto-advancing', () {
      queue.enqueue(_v('X'));
      final q = container.read(queueProvider);
      expect(_ids(q), ['X']);
      expect(q.index, 0);
      expect(q.current!.videoId, 'X');
    });
  });

  group('jumpTo (queue item tap)', () {
    test('jumps to an upcoming item; others follow in order; previous works',
        () {
      // V1 playing, S1 then S2 queued.
      queue.play([_v('V1')]);
      queue.enqueue(_v('S1'));
      queue.enqueue(_v('S2'));

      // Tap S2 (full index 2).
      queue.jumpTo(2);

      final q = container.read(queueProvider);
      expect(q.current!.videoId, 'S2'); // now playing S2
      // S1 follows the newly playing S2.
      expect(_ids(q), ['V1', 'S2', 'S1']);
      expect(q.index, 1);
      // previous returns to V1.
      expect(q.hasPrevious, isTrue);
      queue.previous();
      expect(container.read(queueProvider).current!.videoId, 'V1');
    });

    test('jump to first upcoming keeps remaining order', () {
      queue.play([_v('V1')]);
      queue.enqueue(_v('S1'));
      queue.enqueue(_v('S2'));
      queue.enqueue(_v('S3'));

      queue.jumpTo(1); // tap S1
      final q = container.read(queueProvider);
      expect(q.current!.videoId, 'S1');
      expect(_ids(q), ['V1', 'S1', 'S2', 'S3']);
      expect(q.index, 1);
    });

    test('jump to middle upcoming reorders the tapped one to front', () {
      queue.play([_v('V1')]);
      queue.enqueue(_v('S1'));
      queue.enqueue(_v('S2'));
      queue.enqueue(_v('S3'));

      queue.jumpTo(2); // tap S2
      final q = container.read(queueProvider);
      expect(q.current!.videoId, 'S2');
      expect(_ids(q), ['V1', 'S2', 'S1', 'S3']);
      expect(q.index, 1);
    });

    test('is a no-op on the current index', () {
      queue.play([_v('A'), _v('B')], startIndex: 1);
      queue.jumpTo(1);
      final q = container.read(queueProvider);
      expect(_ids(q), ['A', 'B']);
      expect(q.index, 1);
    });

    test('is a no-op for out-of-range index', () {
      queue.play([_v('A')]);
      queue.jumpTo(5);
      queue.jumpTo(-1);
      expect(_ids(container.read(queueProvider)), ['A']);
      expect(container.read(queueProvider).index, 0);
    });

    test('jumping back to an already-played item does not duplicate it', () {
      // [A, B, C, D] with C playing (index 2).
      queue.play([_v('A'), _v('B'), _v('C'), _v('D')], startIndex: 2);
      queue.jumpTo(0); // tap A (history)

      final q = container.read(queueProvider);
      expect(q.current!.videoId, 'A');
      // A must appear exactly once. Upcoming D preserved after A; B and C
      // remain as history before A.
      expect(_ids(q), ['B', 'C', 'A', 'D']);
      expect(q.index, 2);
      expect(_ids(q).where((id) => id == 'A').length, 1);
    });

    test('duplicate Video instances in the queue are not lost on jump', () {
      // Same-id videos queued twice (user tapped the same suggestion twice).
      queue.play([_v('V1')]);
      queue.enqueue(_v('DUP'));
      queue.enqueue(_v('DUP'));
      expect(_ids(container.read(queueProvider)), ['V1', 'DUP', 'DUP']);

      queue.jumpTo(1); // tap the first DUP
      final q = container.read(queueProvider);
      expect(q.current!.videoId, 'DUP');
      // The second DUP must survive (positional filter, not value filter).
      expect(_ids(q), ['V1', 'DUP', 'DUP']);
      expect(_ids(q).where((id) => id == 'DUP').length, 2);
      expect(q.index, 1);
    });
  });

  group('next / previous (natural playback + autoplay chaining)', () {
    test('next advances through the queue', () {
      queue.play([_v('V1')]);
      queue.enqueue(_v('S1'));
      queue.enqueue(_v('S2'));

      queue.next();
      expect(container.read(queueProvider).current!.videoId, 'S1');
      queue.next();
      expect(container.read(queueProvider).current!.videoId, 'S2');
      expect(container.read(queueProvider).hasNext, isFalse);
    });

    test('autoplay pattern: enqueue + next chains onto an exhausted queue', () {
      queue.play([_v('V1')]); // single item, hasNext false
      expect(container.read(queueProvider).hasNext, isFalse);

      // _autoplayNext behavior: enqueue the related, then advance.
      queue.enqueue(_v('R1'));
      queue.next();

      final q = container.read(queueProvider);
      expect(q.current!.videoId, 'R1');
      expect(_ids(q), ['V1', 'R1']);
      expect(q.index, 1);
    });

    test('next is a no-op at the end', () {
      queue.play([_v('A')]);
      queue.next();
      expect(container.read(queueProvider).index, 0);
    });
  });
}
