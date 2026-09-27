/// "Musique" tab: the YouTube Music home shelves — parsing, card shapes, and
/// what a tap plays or opens.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:ytui_mobile/api/models.dart';
import 'package:ytui_mobile/screens/music.dart';
import 'package:ytui_mobile/state/providers.dart';
import 'package:ytui_mobile/state/queue.dart';
import 'package:ytui_mobile/state/settings.dart';
import 'package:ytui_mobile/widgets/open_player.dart';

class _NoWatched extends WatchedIdsNotifier {
  @override
  Future<Set<String>> build() async => <String>{};
}

class _FakeMusic extends MusicNotifier {
  _FakeMusic(this.home);

  final MusicHome home;

  @override
  Future<MusicHome> build() async => home;
}

// No thumbnails: widget tests have no network.
const _t1 = Video(videoId: 'track000001', title: 'Track one', channelTitle: 'Artist A');
const _t2 = Video(videoId: 'track000002', title: 'Track two', channelTitle: 'Artist B');
const _mix = Video(
    videoId: 'RDTMAK5uy_mix', title: 'My supermix', kind: 'playlist');
const _artist =
    Video(videoId: 'UCartist', title: 'Artist C', kind: 'channel');

const _home = MusicHome(shelves: [
  MusicShelf(title: 'Quick picks', items: [_t1, _mix, _t2]),
  MusicShelf(title: 'Artist A', strapline: 'related', items: [_artist]),
]);

void main() {
  group('MusicHome', () {
    test('parses shelves, straplines and warnings; drops empty shelves', () {
      final home = MusicHome.fromJson({
        'shelves': [
          {
            'title': 'Quick picks',
            'strapline': '',
            'items': [
              {'video_id': 'a', 'title': 'one'},
            ],
          },
          {'title': 'Empty', 'items': []},
          {'title': 'Skillet', 'strapline': 'RELATED', 'items': [
            {'video_id': 'UCx', 'title': 'x', 'kind': 'channel'},
          ]},
        ],
        'warnings': ['stale cookies'],
      });
      expect(home.shelves.map((s) => s.title), ['Quick picks', 'Skillet']);
      expect(home.shelves[1].strapline, 'RELATED');
      expect(home.shelves[1].items.single.kind, 'channel');
      expect(home.warnings, ['stale cookies']);
    });

    test('tolerates a payload without shelves', () {
      final home = MusicHome.fromJson({'warnings': ['no cookies']});
      expect(home.shelves, isEmpty);
    });
  });

  test('cover art is square, video frames and artists are not', () {
    const cover = Video(
        videoId: 'a',
        title: 'a',
        thumbnailUrl: 'https://yt3.googleusercontent.com/x=w544-h544-l90-rj');
    const frame = Video(
        videoId: 'b',
        title: 'b',
        thumbnailUrl: 'https://i.ytimg.com/vi/b/hq720.jpg');
    const artist = Video(
        videoId: 'UC',
        title: 'c',
        kind: 'channel',
        thumbnailUrl: 'https://yt3.googleusercontent.com/y=s576');
    expect(isCoverArt(cover), isTrue);
    expect(isCoverArt(frame), isFalse);
    expect(isCoverArt(artist), isFalse);
  });

  group('MusicTab', () {
    late GoRouter router;
    late ProviderContainer container;

    Future<void> pump(WidgetTester tester, MusicHome home) async {
      router = GoRouter(routes: [
        GoRoute(
            path: '/',
            builder: (_, __) => const Scaffold(body: MusicTab())),
        GoRoute(
            path: kPlayerPath,
            builder: (_, __) => const Scaffold(body: Text('player'))),
        GoRoute(
            path: '/ytplaylist/:id',
            builder: (_, state) =>
                Scaffold(body: Text('playlist ${state.pathParameters['id']}'))),
        GoRoute(
            path: '/channel/:id',
            builder: (_, state) =>
                Scaffold(body: Text('channel ${state.pathParameters['id']}'))),
      ]);
      container = ProviderContainer(overrides: [
        watchedIdsProvider.overrideWith(_NoWatched.new),
        isTvProvider.overrideWithValue(false),
        musicProvider.overrideWith(() => _FakeMusic(home)),
      ]);
      addTearDown(container.dispose);
      await tester.pumpWidget(UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(routerConfig: router),
      ));
      await tester.pumpAndSettle();
    }

    testWidgets('shows each shelf with its strapline and cards',
        (tester) async {
      await pump(tester, _home);
      expect(find.text('Quick picks'), findsOneWidget);
      expect(find.text('RELATED'), findsOneWidget);
      expect(find.text('Track one'), findsOneWidget);
      expect(find.text('My supermix'), findsOneWidget);
      // An artist card says what it is instead of repeating its own name.
      expect(find.text('Artiste'), findsOneWidget);
    });

    testWidgets('a track plays with the rest of the shelf queued',
        (tester) async {
      await pump(tester, _home);
      await tester.tap(find.text('Track two'));
      await tester.pumpAndSettle();
      expect(find.text('player'), findsOneWidget);
      final queue = container.read(queueProvider);
      expect(queue.items.map((v) => v.videoId), ['track000001', 'track000002']);
      expect(queue.index, 1);
    });

    testWidgets('a mix opens as a playlist, an artist as a channel',
        (tester) async {
      await pump(tester, _home);
      await tester.tap(find.text('My supermix'));
      await tester.pumpAndSettle();
      expect(find.text('playlist RDTMAK5uy_mix'), findsOneWidget);
      expect(container.read(queueProvider).items, isEmpty);

      router.pop();
      await tester.pumpAndSettle();
      await tester.tap(find.text('Artist C'));
      await tester.pumpAndSettle();
      expect(find.text('channel UCartist'), findsOneWidget);
    });

    testWidgets('no cookies: the warning plus an explanation, no shelves',
        (tester) async {
      await pump(
        tester,
        const MusicHome(shelves: [], warnings: ['YouTube Music needs cookies']),
      );
      expect(find.text('YouTube Music needs cookies'), findsOneWidget);
      expect(find.textContaining('ytui auth cookies'), findsOneWidget);
    });
  });
}
