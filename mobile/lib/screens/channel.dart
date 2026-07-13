/// Channel screen: latest videos + follow button.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/client.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

class ChannelScreen extends ConsumerWidget {
  final String channelId;
  final String platform;
  final String title;

  const ChannelScreen({
    super.key,
    required this.channelId,
    this.platform = 'youtube',
    this.title = '',
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(channelVideosProvider((channelId, platform)));
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          data.valueOrNull?.$2.isNotEmpty == true
              ? data.valueOrNull!.$2
              : title,
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.person_add),
            tooltip: 'Suivre',
            onPressed: () async {
              final messenger = ScaffoldMessenger.of(context);
              final ref_ = switch (platform) {
                'bitchute' => 'bitchute:$channelId',
                'odysee' => 'odysee:$channelId',
                _ => channelId,
              };
              try {
                await ref.read(apiProvider).followChannel(ref_);
                messenger.showSnackBar(
                    const SnackBar(content: Text('Channel followed')));
              } on ApiException catch (e) {
                messenger.showSnackBar(SnackBar(
                    content: Text(e.statusCode == 409
                        ? 'Already followed'
                        : e.toString())));
              }
            },
          ),
        ],
      ),
      body: data.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () =>
                ref.invalidate(channelVideosProvider((channelId, platform)))),
        data: (result) {
          if (result.$1.isEmpty) {
            return const AppEmpty(
              message: 'This channel has no videos',
              icon: Icons.videocam_off_outlined,
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
