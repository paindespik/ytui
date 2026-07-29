import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/widgets/remote_controls.dart';

void main() {
  group('RemotePlayerSurface', () {
    testWidgets('turns D-pad and media keys into playback actions',
        (tester) async {
      final seeks = <Duration>[];
      final skips = <bool>[];
      var toggles = 0;
      var reveals = 0;
      final focus = FocusNode();
      addTearDown(focus.dispose);

      await tester.pumpWidget(
        MaterialApp(
          home: RemotePlayerSurface(
            focusNode: focus,
            onSeek: seeks.add,
            onTogglePlay: () => toggles++,
            onSkip: skips.add,
            onReveal: () => reveals++,
            child: const SizedBox.expand(),
          ),
        ),
      );
      await tester.pump();
      expect(focus.hasPrimaryFocus, isTrue);

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowLeft);
      expect(seeks, const [Duration(seconds: 10), Duration(seconds: -10)]);

      await tester.sendKeyEvent(LogicalKeyboardKey.select);
      expect(toggles, 1);

      await tester.sendKeyEvent(LogicalKeyboardKey.mediaTrackNext);
      expect(skips, const [true]);

      // Every handled key also surfaces the controls, including plain up/down.
      final before = reveals;
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
      expect(reveals, before + 1);
    });
  });

  group('RemotePlayerControls', () {
    Widget wrap({
      required FocusNode entryFocus,
      required ValueChanged<Duration> onSeek,
      VoidCallback? onInteract,
      VoidCallback? onAudioDelay,
      VoidCallback? onQuality,
      bool live = false,
    }) {
      return MaterialApp(
        home: Scaffold(
          body: buildControls(
            entryFocus: entryFocus,
            onSeek: onSeek,
            onInteract: onInteract,
            onAudioDelay: onAudioDelay,
            onQuality: onQuality,
            live: live,
          ),
        ),
      );
    }

    testWidgets('shows the progress clock and seeks from the focused bar',
        (tester) async {
      final seeks = <Duration>[];
      final entry = FocusNode();
      addTearDown(entry.dispose);

      await tester.pumpWidget(wrap(entryFocus: entry, onSeek: seeks.add));
      entry.requestFocus();
      await tester.pump();

      expect(find.text('1:00'), findsOneWidget);
      expect(find.text('4:30'), findsOneWidget);

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      expect(seeks, const [Duration(seconds: 10)]);
    });

    testWidgets('leaves the bar for the action row on down', (tester) async {
      final seeks = <Duration>[];
      var interactions = 0;
      final entry = FocusNode();
      addTearDown(entry.dispose);

      await tester.pumpWidget(wrap(
        entryFocus: entry,
        onSeek: seeks.add,
        onInteract: () => interactions++,
      ));
      entry.requestFocus();
      await tester.pump();

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
      await tester.pump();
      expect(entry.hasPrimaryFocus, isFalse);
      // Traversal keys are not consumed by the bar: they postpone the auto-hide.
      expect(interactions, greaterThan(0));

      // Arrows now walk the buttons instead of scrubbing.
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      expect(seeks, isEmpty);
    });

    /// Walks from the progress bar to the last button of the action row, the
    /// way a remote does: down into the row, then right until it stops moving.
    Future<void> focusLastAction(WidgetTester tester) async {
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
      await tester.pump();
      for (var i = 0; i < 8; i++) {
        await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
        await tester.pump();
      }
    }

    testWidgets('activates the focused action with the centre button',
        (tester) async {
      var delayTaps = 0;
      var qualityTaps = 0;
      final entry = FocusNode();
      addTearDown(entry.dispose);

      await tester.pumpWidget(wrap(
        entryFocus: entry,
        onSeek: (_) {},
        onAudioDelay: () => delayTaps++,
        onQuality: () => qualityTaps++,
      ));
      entry.requestFocus();
      await tester.pump();

      // The height-cap button is the last of the row, audio delay its neighbour.
      await focusLastAction(tester);
      expect(find.widgetWithText(RemoteButton, '1440p'), findsOneWidget);

      await tester.sendKeyEvent(LogicalKeyboardKey.select);
      expect(qualityTaps, 1);

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowLeft);
      await tester.pump();
      await tester.sendKeyEvent(LogicalKeyboardKey.select);
      expect(delayTaps, 1);
    });

    testWidgets('replaces the timeline with a live badge', (tester) async {
      final entry = FocusNode();
      addTearDown(entry.dispose);

      await tester.pumpWidget(
        wrap(entryFocus: entry, onSeek: (_) {}, live: true),
      );
      await tester.pump();

      expect(find.text('EN DIRECT'), findsOneWidget);
      expect(find.text('1:00'), findsNothing);
    });

    testWidgets('walks the action row without touching the video surface',
        (tester) async {
      // The surface fills the screen and owns the same seek shortcuts, so it
      // must stay out of the overlay's focus traversal.
      final seeks = <Duration>[];
      final surface = FocusNode();
      final entry = FocusNode();
      addTearDown(surface.dispose);
      addTearDown(entry.dispose);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Stack(
              fit: StackFit.expand,
              children: [
                RemotePlayerSurface(
                  focusNode: surface,
                  onSeek: seeks.add,
                  onTogglePlay: () {},
                  onSkip: (_) {},
                  onReveal: () {},
                  child: const SizedBox.expand(),
                ),
                buildControls(entryFocus: entry, onSeek: seeks.add),
              ],
            ),
          ),
        ),
      );
      entry.requestFocus();
      await tester.pump();

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
      await tester.pump();
      final firstButton = FocusManager.instance.primaryFocus;
      expect(firstButton, isNot(surface));

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      await tester.pump();
      expect(FocusManager.instance.primaryFocus, isNot(surface));
      expect(FocusManager.instance.primaryFocus, isNot(firstButton));
      expect(seeks, isEmpty);
    });

    testWidgets('marks the focused action even in touch highlight mode',
        (tester) async {
      // Android's default highlight mode is `touch`, which suppresses focus
      // rings — unusable when the remote is the only pointer.
      FocusManager.instance.highlightStrategy =
          FocusHighlightStrategy.alwaysTouch;
      addTearDown(() => FocusManager.instance.highlightStrategy =
          FocusHighlightStrategy.automatic);
      final entry = FocusNode();
      addTearDown(entry.dispose);

      await tester.pumpWidget(wrap(entryFocus: entry, onSeek: (_) {}));
      entry.requestFocus();
      await tester.pump();

      final button = find.widgetWithText(RemoteButton, '1440p');
      await focusLastAction(tester);

      final box = tester.widget<Container>(
        find.descendant(of: button, matching: find.byType(Container)).first,
      );
      expect(
        (box.decoration as BoxDecoration).color,
        Theme.of(tester.element(button)).colorScheme.primary,
      );
    });
  });
}

/// The overlay under test, with inert callbacks unless overridden.
Widget buildControls({
  required FocusNode entryFocus,
  required ValueChanged<Duration> onSeek,
  VoidCallback? onInteract,
  VoidCallback? onAudioDelay,
  VoidCallback? onQuality,
  bool live = false,
}) {
  return RemotePlayerControls(
    title: 'Une vidéo',
    position: const Duration(minutes: 1),
    duration: const Duration(minutes: 4, seconds: 30),
    playing: true,
    live: live,
    rateLabel: '1×',
    audioDelayLabel: '0 ms',
    qualityLabel: '1440p',
    hasSubtitles: false,
    hasNext: true,
    hasPrevious: false,
    onSeek: onSeek,
    onTogglePlay: () {},
    onSkip: (_) {},
    onRate: () {},
    onSubtitles: () {},
    onAudioDelay: onAudioDelay ?? () {},
    onQuality: onQuality ?? () {},
    onInteract: onInteract ?? () {},
    entryFocus: entryFocus,
  );
}
