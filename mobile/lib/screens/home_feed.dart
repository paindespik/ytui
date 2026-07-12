/// Home feed: followed channels' videos, pull-to-refresh, live badge.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../state/providers.dart';
import '../widgets/app_state_views.dart';
import '../widgets/video_tile.dart';

class HomeFeedScreen extends ConsumerWidget {
  const HomeFeedScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feed = ref.watch(feedProvider);
    final lives = ref.watch(livesProvider).valueOrNull ?? [];
    final liveIds = {for (final l in lives) l.video.videoId};

    return Scaffold(
      appBar: AppBar(
        title: const Text('ytui'),
        actions: [
          IconButton(
            icon: const Icon(Icons.search),
            onPressed: () => context.push('/search'),
          ),
          IconButton(
            icon: const Icon(Icons.history),
            onPressed: () => context.push('/history'),
          ),
          IconButton(
            icon: const Icon(Icons.playlist_play),
            onPressed: () => context.push('/playlists'),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () => context.push('/settings'),
          ),
        ],
      ),
      body: feed.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e, onRetry: () => ref.invalidate(feedProvider)),
        data: (result) {
          // Pin active lives at the top, like the desktop home feed.
          final liveVideos = lives.map((l) => l.video).toList();
          final rest = result.videos
              .where((v) => !liveIds.contains(v.videoId))
              .toList();
          final videos = [...liveVideos, ...rest];
          return RefreshIndicator(
            onRefresh: () async {
              await ref.read(feedProvider.notifier).refresh();
              ref.invalidate(livesProvider);
            },
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                for (final warning in result.warnings)
                  MaterialBanner(
                    content: Text(warning),
                    actions: const [SizedBox.shrink()],
                    backgroundColor:
                        Theme.of(context).colorScheme.errorContainer,
                  ),
                if (videos.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Text(
                      'No videos. Follow channels from Settings.',
                      textAlign: TextAlign.center,
                    ),
                  ),
                for (final video in videos)
                  VideoTile(video: video, live: liveIds.contains(video.videoId)),
              ],
            ),
          );
        },
      ),
    );
  }
}
