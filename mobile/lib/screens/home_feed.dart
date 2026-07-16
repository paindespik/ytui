/// Home: "Abonnements" (followed channels feed) and "Suggestions"
/// (history-based recommendations) tabs, pull-to-refresh, live badge.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

class HomeFeedScreen extends ConsumerWidget {
  const HomeFeedScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
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
          bottom: const TabBar(
            tabs: [
              Tab(text: 'Abonnements'),
              Tab(text: 'Suggestions'),
            ],
          ),
        ),
        body: const TabBarView(
          children: [
            _SubscriptionsTab(),
            _SuggestionsTab(),
          ],
        ),
      ),
    );
  }
}

class _SubscriptionsTab extends ConsumerWidget {
  const _SubscriptionsTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feed = ref.watch(feedProvider);
    final lives = ref.watch(livesProvider).valueOrNull ?? [];
    final liveIds = {for (final l in lives) l.video.videoId};

    return feed.when(
      loading: () => const AppLoading(),
      error: (e, _) => AppError.from(e, onRetry: () => ref.invalidate(feedProvider)),
      data: (result) {
        // Pin active lives at the top, like the desktop home feed.
        final liveVideos = lives.map((l) => l.video).toList();
        final rest = result.videos
            .where((v) => !liveIds.contains(v.videoId))
            .toList();
        final videos = [...liveVideos, ...rest];
        return _VideoListView(
          videos: videos,
          warnings: result.warnings,
          liveIds: liveIds,
          onRefresh: () async {
            await ref.read(feedProvider.notifier).refresh();
            ref.invalidate(livesProvider);
          },
          emptyIcon: Icons.subscriptions_outlined,
          emptyMessage: 'No videos yet. Follow channels from Settings.',
        );
      },
    );
  }
}

class _SuggestionsTab extends ConsumerWidget {
  const _SuggestionsTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final suggestions = ref.watch(suggestionsProvider);

    return suggestions.when(
      loading: () => const AppLoading(),
      error: (e, _) =>
          AppError.from(e, onRetry: () => ref.invalidate(suggestionsProvider)),
      data: (result) => _VideoListView(
        videos: result.videos,
        // Empty history yields a backend warning; the empty state message
        // below already explains it, so don't show an error banner too.
        warnings: result.videos.isEmpty ? const [] : result.warnings,
        liveIds: const {},
        onRefresh: () => ref.read(suggestionsProvider.notifier).refresh(),
        emptyIcon: Icons.auto_awesome_outlined,
        emptyMessage:
            'Pas encore de suggestions. Regarde quelques vidéos pour en obtenir.',
      ),
    );
  }
}

class _VideoListView extends StatelessWidget {
  const _VideoListView({
    required this.videos,
    required this.warnings,
    required this.liveIds,
    required this.onRefresh,
    required this.emptyIcon,
    required this.emptyMessage,
  });

  final List<Video> videos;
  final List<String> warnings;
  final Set<String> liveIds;
  final Future<void> Function() onRefresh;
  final IconData emptyIcon;
  final String emptyMessage;

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ResponsiveCenter(
        child: ListView(
          padding: const EdgeInsets.symmetric(vertical: 8),
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            for (final warning in warnings)
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
                          color: Theme.of(context).colorScheme.onErrorContainer,
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
              Padding(
                padding: const EdgeInsets.only(top: 64),
                child: AppEmpty(icon: emptyIcon, message: emptyMessage),
              ),
            for (final video in videos)
              VideoTile(video: video, live: liveIds.contains(video.videoId)),
          ],
        ),
      ),
    );
  }
}
