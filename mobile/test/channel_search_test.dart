import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/client.dart';
import 'package:ytui_mobile/screens/channel.dart';
import 'package:ytui_mobile/state/providers.dart';

/// Answers the channel listing; a `q` filters the two videos by title.
/// Anything else (watched ids…) gets an empty payload.
class _ChannelAdapter implements HttpClientAdapter {
  final List<Object?> queries = [];
  final List<String> playlistPlatforms = [];

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? _,
      Future<void>? __) async {
    Map<String, Object?> body = const {'items': <Object?>[]};
    if (options.path.endsWith('/videos')) {
      final q = options.queryParameters['q'] as String?;
      queries.add(q);
      const titles = ['Rocket launch', 'Cooking pasta'];
      final items = [
        for (final t in titles)
          if (q == null || t.toLowerCase().contains(q.toLowerCase()))
            {'video_id': t.hashCode.toString(), 'title': t},
      ];
      body = {
        'items': items,
        'channel': {'title': 'Chan'},
        'has_more': false,
      };
    } else if (options.path.endsWith('/playlists')) {
      final platform = options.queryParameters['platform'] as String;
      playlistPlatforms.add(platform);
      body = {
        'items': [
          {
            'video_id': 'PL1',
            'title': 'Best of',
            'kind': 'playlist',
            'platform': platform,
          },
        ],
        'channel': {'title': 'Chan'},
        'has_more': false,
      };
    }
    return ResponseBody.fromString(
      jsonEncode(body),
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

Widget _app(_ChannelAdapter adapter, {String platform = 'youtube'}) {
  final dio = Dio(BaseOptions(baseUrl: 'https://server.test'))
    ..httpClientAdapter = adapter;
  return ProviderScope(
    overrides: [
      apiProvider.overrideWithValue(
          YtuiApi(baseUrl: 'https://server.test', token: 'sekret', dio: dio)),
    ],
    child: MaterialApp(
        home: ChannelScreen(channelId: 'UC1', platform: platform)),
  );
}

void main() {
  testWidgets('searching the channel forwards q and shows the matches',
      (tester) async {
    final adapter = _ChannelAdapter();
    await tester.pumpWidget(_app(adapter));
    await tester.pumpAndSettle();

    // Full listing first: no q, both videos.
    expect(adapter.queries, [null]);
    expect(find.text('Rocket launch'), findsOneWidget);
    expect(find.text('Cooking pasta'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'rocket');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();

    expect(adapter.queries, [null, 'rocket']);
    expect(find.text('Rocket launch'), findsOneWidget);
    expect(find.text('Cooking pasta'), findsNothing);

    // Leaving search mode restores the unfiltered listing.
    await tester.tap(find.byIcon(Icons.close));
    await tester.pumpAndSettle();
    expect(find.text('Cooking pasta'), findsOneWidget);
  });

  testWidgets('a query with no match shows the empty state', (tester) async {
    final adapter = _ChannelAdapter();
    await tester.pumpWidget(_app(adapter));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'zzz');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();

    expect(find.text('Aucune vidéo pour « zzz »'), findsOneWidget);
  });

  testWidgets('the Playlists tab lists the channel playlists on demand',
      (tester) async {
    final adapter = _ChannelAdapter();
    await tester.pumpWidget(_app(adapter, platform: 'bitchute'));
    await tester.pumpAndSettle();

    expect(find.text('Vidéos'), findsOneWidget);
    expect(adapter.playlistPlatforms, isEmpty); // not fetched up front

    await tester.tap(find.text('Playlists'));
    await tester.pumpAndSettle();
    expect(adapter.playlistPlatforms, ['bitchute']);
    expect(find.text('Best of'), findsOneWidget);
    expect(find.text('Rocket launch'), findsNothing);

    // Back and forth: the tab is kept, not fetched again.
    await tester.tap(find.text('Vidéos'));
    await tester.pumpAndSettle();
    expect(find.text('Rocket launch'), findsOneWidget);
    await tester.tap(find.text('Playlists'));
    await tester.pumpAndSettle();
    expect(adapter.playlistPlatforms, ['bitchute']);

    // Searching brings the videos back to the front.
    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();
    expect(find.text('Rocket launch'), findsOneWidget);
  });

  testWidgets('no tabs on platforms without channel playlists',
      (tester) async {
    final adapter = _ChannelAdapter();
    await tester.pumpWidget(_app(adapter, platform: 'odysee'));
    await tester.pumpAndSettle();

    expect(find.byType(TabBar), findsNothing);
    expect(find.text('Rocket launch'), findsOneWidget);
    expect(adapter.playlistPlatforms, isEmpty);
  });
}
