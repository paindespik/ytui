import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/widgets/player_actions.dart';

void main() {
  /// A phone-narrow bar (the Fairphone is 372 dp wide) with more pills than
  /// fit, like the player's.
  Future<List<String>> pumpBar(WidgetTester tester,
      {VoidCallback? onLast}) async {
    final taps = <String>[];
    await tester.binding.setSurfaceSize(const Size(372, 200));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: PlayerActionBar(actions: [
          for (var i = 0; i < 10; i++)
            PlayerAction(
              icon: Icons.star,
              label: 'Action $i',
              tooltip: 'Action $i',
              onPressed: i == 9 ? (onLast ?? () {}) : () => taps.add('$i'),
            ),
          const PlayerAction(
            icon: Icons.block,
            label: 'Désactivée',
            tooltip: 'Désactivée',
            onPressed: null,
          ),
        ]),
      ),
    ));
    return taps;
  }

  testWidgets('scrolls sideways under the finger to reach every pill',
      (tester) async {
    var lastTaps = 0;
    await pumpBar(tester, onLast: () => lastTaps++);

    // Off-screen at first (not even built): the row is wider than the phone.
    expect(find.text('Action 9'), findsNothing);

    await tester.drag(find.byType(PlayerActionBar), const Offset(-2000, 0));
    await tester.pumpAndSettle();
    final right = tester.getTopRight(find.text('Action 9')).dx;
    expect(right, lessThan(372));

    await tester.tap(find.text('Action 9'));
    expect(lastTaps, 1);
  });

  testWidgets('taps fire their action; a disabled pill does nothing',
      (tester) async {
    final taps = await pumpBar(tester);
    await tester.tap(find.text('Action 0'));
    expect(taps, ['0']);

    await tester.drag(find.byType(PlayerActionBar), const Offset(-3000, 0));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Désactivée'), warnIfMissed: false);
    expect(taps, ['0']);
  });

  testWidgets('toggles expose their state to accessibility', (tester) async {
    final handle = tester.ensureSemantics();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: PlayerActionBar(actions: [
          PlayerAction(
            icon: Icons.repeat_one,
            label: 'Boucle',
            tooltip: 'Lecture en boucle : activée',
            selected: true,
            toggle: true,
            onPressed: () {},
          ),
        ]),
      ),
    ));
    expect(
      tester.getSemantics(find.bySemanticsLabel('Lecture en boucle : activée')),
      isSemantics(
        label: 'Lecture en boucle : activée',
        isButton: true,
        isEnabled: true,
        hasToggledState: true,
        isToggled: true,
        hasTapAction: true,
      ),
    );
    handle.dispose();
  });
}
