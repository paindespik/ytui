import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:ytui_mobile/widgets/open_player.dart';

/// Stand-in for PlayerScreen: registers with [PlayerPresence] like it does.
class _FakePlayer extends StatefulWidget {
  const _FakePlayer();

  @override
  State<_FakePlayer> createState() => _FakePlayerState();
}

class _FakePlayerState extends State<_FakePlayer> {
  @override
  void initState() {
    super.initState();
    PlayerPresence.enter();
  }

  @override
  void dispose() {
    PlayerPresence.leave();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => const Text('player');
}

class _Screen extends StatelessWidget {
  const _Screen(this.label);

  final String label;

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label),
          TextButton(
            onPressed: () => showPlayer(context: context),
            child: Text('play from $label'),
          ),
          TextButton(
            onPressed: () => context.push('/channel'),
            child: Text('channel from $label'),
          ),
        ],
      );
}

void main() {
  late GoRouter router;

  Future<void> pump(WidgetTester tester) async {
    router = GoRouter(routes: [
      GoRoute(
        path: '/',
        builder: (_, __) => const Scaffold(body: _Screen('home')),
      ),
      GoRoute(
        path: kPlayerPath,
        builder: (_, __) => const Scaffold(
          body: Column(children: [_FakePlayer(), _Screen('in player')]),
        ),
      ),
      GoRoute(
        path: '/channel',
        builder: (_, __) => const Scaffold(body: _Screen('channel')),
      ),
    ]);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
  }

  testWidgets('pushes the player when none is open', (tester) async {
    await pump(tester);
    await tester.tap(find.text('play from home'));
    await tester.pumpAndSettle();
    expect(find.text('player'), findsOneWidget);
    expect(PlayerPresence.isOpen, isTrue);
  });

  testWidgets('pops back to an open player instead of stacking another',
      (tester) async {
    await pump(tester);
    await tester.tap(find.text('play from home'));
    await tester.pumpAndSettle();
    // Channel opened from the player: the player stays mounted underneath.
    await tester.tap(find.text('channel from in player'));
    await tester.pumpAndSettle();
    expect(find.text('channel'), findsOneWidget);

    await tester.tap(find.text('play from channel'));
    await tester.pumpAndSettle();

    // Back on the one player: channel gone, player on stage, no second one.
    expect(find.text('channel', skipOffstage: false), findsNothing);
    expect(find.text('player'), findsOneWidget);
    expect(find.text('player', skipOffstage: false), findsOneWidget);
    expect(router.routerDelegate.currentConfiguration.last.matchedLocation,
        kPlayerPath);

    // Leaving the player leaves no presence behind.
    router.pop();
    await tester.pumpAndSettle();
    expect(PlayerPresence.isOpen, isFalse);
  });
}
