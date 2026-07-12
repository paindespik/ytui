/// Foreground live polling: every 5 minutes while the app runs.
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/providers.dart';
import '../state/settings.dart';
import 'notifications.dart';

class LivePoller {
  final Ref ref;
  Timer? _timer;

  LivePoller(this.ref);

  void start() {
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(minutes: 5), (_) => _check());
    // First check shortly after startup.
    Timer(const Duration(seconds: 15), _check);
  }

  Future<void> _check() async {
    try {
      await checkLivesAndNotify(ref.read(sharedPreferencesProvider));
      ref.invalidate(livesProvider);
    } catch (_) {
      // Server unreachable: stay silent, retry at the next tick.
    }
  }

  void stop() => _timer?.cancel();
}

final livePollerProvider = Provider<LivePoller>((ref) {
  final poller = LivePoller(ref);
  ref.onDispose(poller.stop);
  return poller;
});
