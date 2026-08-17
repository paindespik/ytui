/// ScreenFocus: initial D-pad focus must land on the first focusable widget
/// of the screen body (never the AppBar), retrying until the loading state
/// turns into data, and never stealing focus the user already moved.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/widgets/screen_focus.dart';

Widget _harness({required Widget body}) {
  return MaterialApp(
    home: Scaffold(
      appBar: AppBar(title: const Text('Title')),
      body: body,
    ),
  );
}

FocusNode? _focused(WidgetTester tester) =>
    tester.binding.focusManager.primaryFocus;

/// True when the widget found by [finder] sits inside the focused element
/// (a button's Text is *below* its internal focus node in the tree).
bool _focusOn(WidgetTester tester, FocusNode? node, Finder finder) {
  final context = node?.context;
  if (context == null) return false;
  return find
      .descendant(
        of: find.byElementPredicate((e) => identical(e, context)),
        matching: finder,
      )
      .evaluate()
      .isNotEmpty;
}

/// A few frames, so the zero-timer focus request and its post-frame
/// verification have both run.
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  testWidgets('focuses the first focusable body widget on open', (tester) async {
    await tester.pumpWidget(_harness(
      body: ScreenFocus(
        child: Column(
          children: [
            FilledButton(onPressed: () {}, child: const Text('first')),
            FilledButton(onPressed: () {}, child: const Text('second')),
          ],
        ),
      ),
    ));
    await _settle(tester);

    expect(_focusOn(tester, _focused(tester), find.text('first')), isTrue,
        reason: 'the first body widget, not the AppBar, should hold focus');
  });

  testWidgets('waits for the loading state to turn into data', (tester) async {
    final gate = ValueNotifier<bool>(false);
    late StateSetter setGate;

    await tester.pumpWidget(StatefulBuilder(
      builder: (context, setState) {
        setGate = setState;
        return _harness(
          body: ScreenFocus(
            child: gate.value
                ? FilledButton(
                    onPressed: () {},
                    child: const Text('data button'),
                  )
                : const Center(child: CircularProgressIndicator()),
          ),
        );
      },
    ));
    // No focusable widget yet: nothing can be focused.
    await _settle(tester);
    expect(_focusOn(tester, _focused(tester), find.text('data button')),
        isFalse);

    gate.value = true;
    setGate(() {});
    await tester.pump(); // rebuild with the button
    await _settle(tester); // covers one retry tick

    expect(_focusOn(tester, _focused(tester), find.text('data button')), isTrue,
        reason: 'once the data lands, focus must move to the first widget');
  });

  testWidgets('takes over the OS auto-focus when the feed is slow',
      (tester) async {
    // Device race seen on the projector: at launch the system auto-focuses
    // the first focusable node (a tab / app bar button) before the feed's
    // data lands; the initial focus must still end up on the first body
    // widget once it exists — the auto-focus is not a user decision.
    final gate = ValueNotifier<bool>(false);
    final earlyNode = FocusNode();
    late StateSetter setGate;

    await tester.pumpWidget(_harness(
      body: StatefulBuilder(
        builder: (context, setState) {
          setGate = setState;
          return Column(
            children: [
              FilledButton(
                focusNode: earlyNode,
                onPressed: () {},
                child: const Text('early button'),
              ),
              ScreenFocus(
                child: gate.value
                    ? FilledButton(
                        onPressed: () {},
                        child: const Text('data button'),
                      )
                    : const Center(child: CircularProgressIndicator()),
              ),
            ],
          );
        },
      ),
    ));
    await _settle(tester);

    // Simulate the system auto-focus landing on the sibling (an
    // "app bar"-like) button above the body, without any key press.
    earlyNode.requestFocus();
    await tester.pump();

    // The feed lands late: the retry must take the focus to the body.
    gate.value = true;
    setGate(() {});
    await tester.pump();
    await _settle(tester);

    expect(_focusOn(tester, _focused(tester), find.text('data button')),
        isTrue,
        reason: 'a slow feed must not leave the OS auto-focus in place');
  });

  testWidgets('does not steal focus the user already moved elsewhere',
      (tester) async {
    final gate = ValueNotifier<bool>(false);
    final appBarButton = FocusNode();
    late StateSetter setGate;

    // The StatefulBuilder sits BELOW MaterialApp: the rebuild only swaps the
    // body, so the app bar (and its focused button) stay attached.
    await tester.pumpWidget(MaterialApp(
      home: StatefulBuilder(
        builder: (context, setState) {
          setGate = setState;
          return Scaffold(
            appBar: AppBar(
              title: const Text('Title'),
              actions: [
                IconButton(
                  focusNode: appBarButton,
                  icon: const Icon(Icons.search),
                  tooltip: 'Search',
                  onPressed: () {},
                ),
              ],
            ),
            body: ScreenFocus(
              child: gate.value
                  ? FilledButton(
                      onPressed: () {},
                      child: const Text('body button'),
                    )
                  : const Center(child: CircularProgressIndicator()),
            ),
          );
        },
      ),
    ));
    // The user (or the OS) already moved focus to an AppBar action while the
    // body was still loading: the retry must not yank it back to the body.
    // A key press is what marks "the user is in control" (the OS auto-focus
    // on an AppBar button at launch is not: ScreenFocus may still take over
    // until the body's data lands).
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
    await tester.pump();
    appBarButton.requestFocus();
    await tester.pump();

    gate.value = true;
    setGate(() {});
    await tester.pump();
    await _settle(tester); // several retries

    // The Search tooltip sits *above* the app bar node's own focus element,
    // hence find.ancestor instead of _focusOn's find.descendant.
    final context = _focused(tester)?.context;
    expect(
        context != null &&
            find
                .ancestor(
                  of: find.byElementPredicate((e) => identical(e, context)),
                  matching: find.byTooltip('Search'),
                )
                .evaluate()
                .isNotEmpty,
        isTrue,
        reason: 'focus must stay where the user put it');
  });
}
