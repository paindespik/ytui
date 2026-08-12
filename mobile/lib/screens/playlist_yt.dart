/// YouTube playlist screen: entries + "play all" through the queue.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/playlist_import.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

class YtPlaylistScreen extends ConsumerWidget {
  final String playlistId;
  final String platform;

  const YtPlaylistScreen({
    super.key,
    required this.playlistId,
    this.platform = 'youtube',
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(ytPlaylistProvider((playlistId, platform)));
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          data.valueOrNull?.$2 ?? 'Playlist',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.playlist_add),
            tooltip: 'Import into a ytui playlist',
            onPressed: () async {
              final playlist = await showImportPlaylistSheet(
                context,
                ref,
                source: playlistId,
                platform: platform,
                defaultName: data.valueOrNull?.$2 ?? '',
              );
              if (playlist != null && context.mounted) {
                context.push('/playlists/${playlist.id}'
                    '?name=${Uri.encodeComponent(playlist.name)}');
              }
            },
          ),
        ],
      ),
      body: data.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () =>
                ref.invalidate(ytPlaylistProvider((playlistId, platform)))),
        data: (result) {
          if (result.$1.isEmpty) {
            return const AppEmpty(
              message: 'No videos in this playlist',
              icon: Icons.playlist_remove,
            );
          }
          final videos = result.$1;
          return ResponsiveCenter(
            child: ListView.builder(
              padding: const EdgeInsets.only(bottom: kGutter),
              itemCount: videos.length + 1,
              itemBuilder: (context, index) {
                if (index == 0) {
                  // Prominent "Play all" header
                  return Padding(
                    padding: const EdgeInsets.fromLTRB(
                        kGutter, kGutter, kGutter, 8),
                    child: FilledButton.icon(
                      onPressed: () {
                        ref.read(queueProvider.notifier).play(videos);
                        context.push('/player');
                      },
                      icon: const Icon(Icons.play_arrow),
                      label: Text('Play all ${videos.length} videos'),
                      style: FilledButton.styleFrom(
                        backgroundColor: colorScheme.primary,
                        foregroundColor: colorScheme.onPrimary,
                        minimumSize: const Size.fromHeight(48),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(kRadiusMd),
                        ),
                      ),
                    ),
                  );
                }
                final videoIndex = index - 1;
                return VideoTile(
                  video: videos[videoIndex],
                  onTap: () {
                    ref
                        .read(queueProvider.notifier)
                        .play(videos, startIndex: videoIndex);
                    context.push('/player');
                  },
                );
              },
            ),
          );
        },
      ),
    );
  }
}
