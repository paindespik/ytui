/// TV D-pad focus:
///  * DOWN from the tab bar lands on the first tile (single press);
///  * DOWN/UP from a row's ⋮ actions button land on the neighbouring
///    row's tile — the reading-order policy alone sticks to the ⋮ column
///    (a DOWN on a button would focus the next button, never the content).
///
/// Focus is always obtained with the keyboard, never with a mouse tap: a
/// tap-then-key press sequence swallows the first directional press in the
/// test binding, which does not happen with real D-pad use.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/models.dart';
import 'package:ytui_mobile/state/providers.dart';
import 'package:ytui_mobile/state/settings.dart';
import 'package:ytui_mobile/widgets/video_tile.dart';

class _NoWatched extends WatchedIdsNotifier {
  @override
  Future<Set<String>> build() async => <String>{};
}

final _videos = List.generate(
  4,
  (i) => Video(
    videoId: 'v$i',
    title: 'Titre $i',
    channelTitle: 'Chaîne $i',
    kind: 'video',
  ),
);

Future<void> _pump(WidgetTester tester) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        watchedIdsProvider.overrideWith(_NoWatched.new),
        isTvProvider.overrideWithValue(true),
      ],
      child: MaterialApp(
        home: DefaultTabController(
          length: 2,
          child: Scaffold(
            appBar: AppBar(
              bottom: const TabBar(
                tabs: [Tab(text: 'Abonnements'), Tab(text: 'Suggestions')],
              ),
            ),
            body: TabBarView(
              children: [
                ListView(
                  children: [for (final v in _videos) VideoTile(video: v)],
                ),
                const Center(child: Text('Suggestions')),
              ],
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
}

/// 0-based row of the focused tile, +100 when focus is on that row's ⋮
/// button, -2 when focus is elsewhere.
int _focusedRow(WidgetTester tester) {
  final ctx = tester.binding.focusManager.primaryFocus?.context;
  final renderObject = ctx?.findRenderObject();
  if (ctx == null || renderObject is! RenderBox) return -2;
  final center =
      renderObject.localToGlobal(renderObject.size.center(Offset.zero));
  final isButton = ctx.findAncestorWidgetOfExactType<IconButton>() != null;
  final tiles = find.byType(VideoTile).evaluate().toList();
  for (var i = 0; i < tiles.length; i++) {
    final box = tiles[i].renderObject! as RenderBox;
    final r = box.localToGlobal(Offset.zero) & box.size;
    if (r.contains(center)) return isButton ? i + 100 : i;
  }
  return -2;
}

Future<void> _press(WidgetTester tester, LogicalKeyboardKey key) async {
  await tester.sendKeyEvent(key);
  await tester.pump();
}

/// Keyboard-only setup: from a cold screen, DOWN×2 reaches the first tile
/// (the first press focuses the tab bar, the second the first tile).
Future<void> _focusFirstTile(WidgetTester tester) async {
  await _press(tester, LogicalKeyboardKey.arrowDown);
  await _press(tester, LogicalKeyboardKey.arrowDown);
  expect(_focusedRow(tester), 0);
}

/// Move focus from the first tile up to the tab bar.
Future<void> _focusTab(WidgetTester tester) async {
  await _press(tester, LogicalKeyboardKey.arrowUp);
  await _press(tester, LogicalKeyboardKey.arrowUp);
  expect(_focusedRow(tester), -2, reason: 'focus should leave the list');
}

void main() {
  testWidgets('DOWN from the tab bar lands on the first tile',
      (tester) async {
    await _pump(tester);
    await _focusFirstTile(tester);
    await _focusTab(tester);
    await _press(tester, LogicalKeyboardKey.arrowDown);
    expect(_focusedRow(tester), 0);
  });

  testWidgets('RIGHT on a tile reaches its ⋮ button (unchanged)',
      (tester) async {
    await _pump(tester);
    await _focusFirstTile(tester);
    await _press(tester, LogicalKeyboardKey.arrowRight);
    expect(_focusedRow(tester), 100, reason: 'row 0, on the ⋮ button');
  });

  testWidgets('DOWN from the ⋮ button lands on the NEXT tile, not next ⋮',
      (tester) async {
    await _pump(tester);
    await _focusFirstTile(tester);
    await _press(tester, LogicalKeyboardKey.arrowRight);
    expect(_focusedRow(tester), 100);
    await _press(tester, LogicalKeyboardKey.arrowDown);
    expect(_focusedRow(tester), 1,
        reason: 'DOWN from a ⋮ must reach the next row\'s tile');
    await _press(tester, LogicalKeyboardKey.arrowDown);
    expect(_focusedRow(tester), 2,
        reason: 'and consecutive DOWNs keep walking the tiles');
  });

  testWidgets('UP from a ⋮ button lands on the PREVIOUS tile',
      (tester) async {
    await _pump(tester);
    await _focusFirstTile(tester);
    await _press(tester, LogicalKeyboardKey.arrowDown);
    await _press(tester, LogicalKeyboardKey.arrowRight);
    expect(_focusedRow(tester), 101);
    await _press(tester, LogicalKeyboardKey.arrowUp);
    expect(_focusedRow(tester), 0,
        reason: 'UP from row 1 ⋮ must reach row 0\'s tile');
  });

  testWidgets('UP from the first ⋮ button falls back to default traversal',
      (tester) async {
    await _pump(tester);
    await _focusFirstTile(tester);
    await _press(tester, LogicalKeyboardKey.arrowRight);
    expect(_focusedRow(tester), 100);
    await _press(tester, LogicalKeyboardKey.arrowUp);
    expect(_focusedRow(tester), -2,
        reason: 'no previous row: default edge behavior (tab bar), not row 0');
  });

  testWidgets('a live tile without thumbnail shows an EN DIRECT block',
      (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          watchedIdsProvider.overrideWith(_NoWatched.new),
          isTvProvider.overrideWithValue(true),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: VideoTile(
              video: const Video(
                videoId: 'live1',
                title: 'Direct',
                channelTitle: 'Chaîne live',
                kind: 'video',
              ),
              live: true,
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('EN DIRECT'), findsWidgets);
  });
}
