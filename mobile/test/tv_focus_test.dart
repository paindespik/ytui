/// TV focus traversal: the per-tile "Actions" button must stay reachable with
/// the D-pad. Flutter's directional traversal only keeps candidates whose
/// centre lies beyond the focused node's edge, so a button nested inside the
/// tile's InkWell is skipped and focus escapes to the app bar instead.
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

final _playlist = Video(
  videoId: 'PLabc',
  title: 'The Witcher 3 : Bob Lennon',
  channelTitle: 'Bob Lennon',
  kind: 'playlist',
);

Future<void> _pumpTile(WidgetTester tester, {required bool isTv}) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        watchedIdsProvider.overrideWith(_NoWatched.new),
        isTvProvider.overrideWithValue(isTv),
      ],
      child: MaterialApp(
        home: Scaffold(body: VideoTile(video: _playlist)),
      ),
    ),
  );
  await tester.pump();
}

void main() {
  testWidgets('D-pad right from a playlist tile reaches its Actions button',
      (tester) async {
    await _pumpTile(tester, isTv: true);

    final actions = find.byTooltip('Actions');
    expect(actions, findsOneWidget);

    // Tab focuses the tile itself, exactly like arriving on it from the list.
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pump();
    final tileFocus = tester.binding.focusManager.primaryFocus;
    expect(tileFocus, isNotNull);
    expect(
      find.descendant(
        of: actions,
        matching: find.byElementPredicate((e) => identical(e, tileFocus!.context)),
      ),
      findsNothing,
      reason: 'the tile, not the button, should hold focus first',
    );

    await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
    await tester.pump();

    final focused = tester.binding.focusManager.primaryFocus;
    expect(
      find.descendant(
        of: actions,
        matching: find.byElementPredicate((e) => identical(e, focused!.context)),
      ),
      findsOneWidget,
      reason: 'D-pad right must land inside the Actions button, not escape the row',
    );
  });

  testWidgets('phones keep the tile untouched (long-press only)',
      (tester) async {
    await _pumpTile(tester, isTv: false);
    expect(find.byTooltip('Actions'), findsNothing);
  });
}
