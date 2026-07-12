/// YouTube playlist screen: entries + "play all" through the queue.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../state/providers.dart';
import '../state/queue.dart';
import '../widgets/video_tile.dart';
import '../widgets/app_state_views.dart';

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

    return Scaffold(
      appBar: AppBar(
        title: Text(data.valueOrNull?.$2 ?? 'Playlist'),
        actions: [
          IconButton(
            icon: const Icon(Icons.playlist_play),
            tooltip: 'Play all',
            onPressed: data.valueOrNull == null ||
                    data.valueOrNull!.$1.isEmpty
                ? null
                : () {
                    ref
                        .read(queueProvider.notifier)
                        .play(data.valueOrNull!.$1);
                    context.push('/player');
                  },
          ),
        ],
      ),
      body: data.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () =>
                ref.invalidate(ytPlaylistProvider((playlistId, platform)))),
        data: (result) => result.$1.isEmpty
            ? const AppEmpty(message: 'No videos in this playlist')
            : ListView(
                children: [
                  for (var i = 0; i < result.$1.length; i++)
                    VideoTile(
                      video: result.$1[i],
                      onTap: () {
                        ref
                            .read(queueProvider.notifier)
                            .play(result.$1, startIndex: i);
                        context.push('/player');
                      },
                    ),
                ],
              ),
      ),
    );
  }
}
