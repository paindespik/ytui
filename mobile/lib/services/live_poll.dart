/// Foreground live polling: every 2 minutes while the app runs
/// (Twitch lives are caught within ~1 min server-side).
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/providers.dart';
import '../state/settings.dart';
import 'notifications.dart';

class LivePoller {
  final Ref ref;
  Timer? _timer;
  Timer? _initial;

  LivePoller(this.ref);

  void start() {
    _timer?.cancel();
    _initial?.cancel();
    _timer = Timer.periodic(const Duration(minutes: 2), (_) => _check());
    // First check shortly after startup.
    _initial = Timer(const Duration(seconds: 15), _check);
  }

  Future<void> _check() async {
    try {
      await checkLivesAndNotify(ref.read(sharedPreferencesProvider));
      ref.invalidate(livesProvider);
    } catch (e) {
      debugPrint('ytui live poll failed: $e');
    }
  }

  void stop() {
    _timer?.cancel();
    _initial?.cancel();
  }
}

final livePollerProvider = Provider<LivePoller>((ref) {
  final poller = LivePoller(ref);
  ref.onDispose(poller.stop);
  return poller;
});
