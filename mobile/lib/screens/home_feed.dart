/// Home feed: followed channels' videos, pull-to-refresh, live badge.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';
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
        title: Text.rich(
          TextSpan(
            children: [
              const TextSpan(
                text: 'yt',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              TextSpan(
                text: 'ui',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: Theme.of(context).colorScheme.primary,
                ),
              ),
            ],
          ),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.search),
            tooltip: 'Rechercher',
            onPressed: () => context.push('/search'),
          ),
          IconButton(
            icon: const Icon(Icons.history),
            tooltip: 'Historique',
            onPressed: () => context.push('/history'),
          ),
          IconButton(
            icon: const Icon(Icons.playlist_play),
            tooltip: 'Playlists',
            onPressed: () => context.push('/playlists'),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Réglages',
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
            child: ResponsiveCenter(
              child: ListView(
              padding: const EdgeInsets.symmetric(vertical: 8),
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                for (final warning in result.warnings)
                  Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: kGutter,
                      vertical: 4,
                    ),
                    child: Material(
                      color: Theme.of(context).colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(kRadiusMd),
                      child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: Row(
                          children: [
                            Icon(
                              Icons.warning_amber_rounded,
                              color:
                                  Theme.of(context).colorScheme.onErrorContainer,
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Text(
                                warning,
                                style: TextStyle(
                                  color: Theme.of(context)
                                      .colorScheme
                                      .onErrorContainer,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                if (videos.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: 64),
                    child: AppEmpty(
                      icon: Icons.subscriptions_outlined,
                      message: 'No videos yet. Follow channels from Settings.',
                    ),
                  ),
                for (final video in videos)
                  VideoTile(video: video, live: liveIds.contains(video.videoId)),
              ],
              ),
            ),
          );
        },
      ),
    );
  }
}
