/// VideoTile metadata line: the view count is shown for videos that carry
/// one, and stays out of the way when it is null (RSS without counts,
/// playlists, channels).
library;

import 'package:flutter/material.dart';
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

Future<void> _pumpTile(WidgetTester tester, Video video) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        watchedIdsProvider.overrideWith(_NoWatched.new),
        isTvProvider.overrideWithValue(false),
      ],
      child: MaterialApp(home: Scaffold(body: VideoTile(video: video))),
    ),
  );
  await tester.pump();
}

void main() {
  testWidgets('shows the view count for a video that carries one',
      (tester) async {
    await _pumpTile(
      tester,
      Video(
        videoId: 'dQw4w9WgXcQ',
        title: 'Never Gonna Give You Up',
        channelTitle: 'Rick Astley',
        published: DateTime.utc(2009, 10, 25),
        duration: 212,
        viewCount: 1234567,
      ),
    );
    expect(find.text('Rick Astley · 1.2M vues'), findsOneWidget);
  });

  testWidgets('omits the views segment when the count is null',
      (tester) async {
    await _pumpTile(
      tester,
      Video(
        videoId: 'dQw4w9WgXcQ',
        title: 'Never Gonna Give You Up',
        channelTitle: 'Rick Astley',
      ),
    );
    expect(find.text('Rick Astley'), findsOneWidget);
  });

  testWidgets('keeps the kind label for playlists and shows no views',
      (tester) async {
    await _pumpTile(
      tester,
      Video(
        videoId: 'PLabc',
        title: 'My Awesome Playlist',
        channelTitle: 'Bob Lennon',
        kind: 'playlist',
        viewCount: 55, // ignored for non-video kinds
      ),
    );
    expect(find.text('Bob Lennon · playlist'), findsOneWidget);
  });
}
