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
