/// Single entry point to start playback and show the player.
///
/// The player can sit under other screens: a channel or the details page
/// opened from the player's own suggestions are pushed ON TOP of it, and the
/// player keeps running below. Pushing `/player` again from there stacked a
/// second [PlayerScreen] while the first one — still mounted, still listening
/// to the queue — reloaded the same video: two players, two audio tracks.
/// [showPlayer] pops back to the existing player instead.
library;

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../state/queue.dart';

/// Route path of the player (see main.dart). go_router names a page after its
/// route path when the route has no explicit name, so this is also the
/// `settings.name` of the player's [Route].
const kPlayerPath = '/player';

/// How many player screens are mounted (0 or 1 once [showPlayer] is used
/// everywhere). Maintained by the player's initState/dispose.
class PlayerPresence {
  PlayerPresence._();

  static int _mounted = 0;

  static bool get isOpen => _mounted > 0;

  static void enter() => _mounted++;

  static void leave() {
    if (_mounted > 0) _mounted--;
  }
}

/// True for the player's route in the navigator stack.
bool isPlayerRoute(Route<dynamic> route) => route.settings.name == kPlayerPath;

/// Brings the player to the front: pops back to it when it already lives in
/// the stack, pushes it otherwise. Without a [BuildContext] (notification
/// taps), [router] pushes and [navigator] pops.
void showPlayer({
  BuildContext? context,
  NavigatorState? navigator,
  GoRouter? router,
}) {
  assert(context != null || router != null);
  if (PlayerPresence.isOpen) {
    final nav = navigator ?? (context == null ? null : Navigator.of(context));
    // `isFirst` bounds the walk should the player route ever be renamed.
    nav?.popUntil((route) => isPlayerRoute(route) || route.isFirst);
    return;
  }
  if (router != null) {
    router.push(kPlayerPath);
  } else {
    context!.push(kPlayerPath);
  }
}

/// Replaces the queue with [videos] (starting at [startIndex]) and shows the
/// player.
void openPlayer(
  BuildContext context,
  WidgetRef ref,
  List<Video> videos, {
  int startIndex = 0,
}) {
  ref.read(queueProvider.notifier).play(videos, startIndex: startIndex);
  showPlayer(context: context);
}
