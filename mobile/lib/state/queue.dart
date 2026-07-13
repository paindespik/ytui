/// Local playback queue (mirrors the desktop mpv queue, purely client-side).
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';

class PlayQueue {
  final List<Video> items;
  final int index;

  const PlayQueue({this.items = const [], this.index = 0});

  Video? get current => index >= 0 && index < items.length ? items[index] : null;
  bool get hasNext => index + 1 < items.length;
  bool get hasPrevious => index > 0;
}

class QueueNotifier extends Notifier<PlayQueue> {
  @override
  PlayQueue build() => const PlayQueue();

  /// Replace the queue and start at [startIndex].
  void play(List<Video> videos, {int startIndex = 0}) {
    state = PlayQueue(items: List.of(videos), index: startIndex);
  }

  /// Append to the queue (starts it if empty).
  void enqueue(Video video) {
    state = PlayQueue(items: [...state.items, video], index: state.index);
  }

  /// Jump to the item at [targetIndex] (an index into the FULL `items`
  /// list) and play it now. Everything before the old current (plus the old
  /// current itself) is kept before it so `previous` still works; the other
  /// still-upcoming items are moved to sit right after it, preserving order.
  /// Filtering is positional, so duplicate [Video] instances in the queue are
  /// preserved and an already-played item can be jumped to without duplicating.
  /// No-op if [targetIndex] is out of range or is already the current index.
  void jumpTo(int targetIndex) {
    final items = state.items;
    if (targetIndex < 0 || targetIndex >= items.length) return;
    if (targetIndex == state.index) return;
    final target = items[targetIndex];
    // History: old current and everything before it, minus the target if it
    // happens to live there (jumping back to an already-played item).
    final history = <Video>[
      for (var i = 0; i <= state.index; i++)
        if (i != targetIndex) items[i],
    ];
    // Upcoming: items after the old current, minus the target (by position).
    final upcoming = <Video>[
      for (var i = state.index + 1; i < items.length; i++)
        if (i != targetIndex) items[i],
    ];
    state = PlayQueue(
      items: [...history, target, ...upcoming],
      index: history.length,
    );
  }

  void next() {
    if (state.hasNext) state = PlayQueue(items: state.items, index: state.index + 1);
  }

  void previous() {
    if (state.hasPrevious) {
      state = PlayQueue(items: state.items, index: state.index - 1);
    }
  }

  void clear() => state = const PlayQueue();
}

final queueProvider = NotifierProvider<QueueNotifier, PlayQueue>(QueueNotifier.new);
