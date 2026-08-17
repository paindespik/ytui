/// Closing the per-video actions sheet with BACK must restore focus to the
/// ⋮ button that opened it (verified on Flutter 3.44: Navigator restores the
/// previous route's focused node — no extra work needed, guarded here).
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

final _video = Video(
  videoId: 'v0',
  title: 'Titre 0',
  channelTitle: 'Chaîne 0',
  kind: 'video',
);

void main() {
  testWidgets('focus returns to the ⋮ button after BACK on the sheet',
      (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          watchedIdsProvider.overrideWith(_NoWatched.new),
          isTvProvider.overrideWithValue(true),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: ListView(
              children: [
                VideoTile(video: _video),
                VideoTile(
                  video: Video(
                    videoId: 'v1',
                    title: 'Titre 1',
                    channelTitle: 'Chaîne 1',
                    kind: 'video',
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    // D-pad only: DOWN×2 reaches the first tile, RIGHT its ⋮ button.
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
    await tester.pump();
    final beforeCtx = tester.binding.focusManager.primaryFocus?.context;
    expect(beforeCtx, isNotNull);
    expect(
      beforeCtx!.findAncestorWidgetOfExactType<IconButton>(),
      isNotNull,
      reason: 'focus must be on the ⋮ button before opening the sheet',
    );

    // Open the sheet (a remote OK press fires the same onPressed).
    await tester.tap(find.byTooltip('Actions').first);
    await tester.pumpAndSettle();
    expect(find.text('Ajouter à la file'), findsOneWidget);

    // BACK closes the sheet; focus must come back to the button.
    await tester.sendKeyEvent(LogicalKeyboardKey.escape);
    await tester.pumpAndSettle();
    expect(find.text('Ajouter à la file'), findsNothing);
    final afterCtx = tester.binding.focusManager.primaryFocus?.context;
    expect(afterCtx, isNotNull);
    expect(
      afterCtx!.findAncestorWidgetOfExactType<IconButton>(),
      isNotNull,
      reason: 'focus must return to the ⋮ button after BACK',
    );
  });
}
