/// Shared list tile for feed/search/channel/playlist entries.
library;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';

class VideoTile extends ConsumerWidget {
  final Video video;
  final bool live;
  final VoidCallback? onTap;

  const VideoTile({super.key, required this.video, this.live = false, this.onTap});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final watched =
        ref.watch(watchedIdsProvider).valueOrNull?.contains(video.videoId) ?? false;
    final colors = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;

    return InkWell(
      onTap: onTap ?? () => _defaultTap(context, ref),
      onLongPress: video.kind == 'video' ? () => _showActions(context, ref) : null,
      borderRadius: BorderRadius.circular(kRadiusSm),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: kGutter, vertical: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Thumbnail: circular for channels, 16:9 for videos/playlists
            if (video.kind == 'channel')
              // Channel avatar - circular thumbnail centered in same width
              SizedBox(
                width: 120,
                child: Center(
                  child: SizedBox(
                    width: 68,
                    height: 68,
                    child: ClipOval(
                      child: video.thumbnailUrl.isNotEmpty
                          ? CachedNetworkImage(
                              imageUrl: video.thumbnailUrl,
                              fit: BoxFit.cover,
                              errorWidget: (_, __, ___) =>
                                  _channelPlaceholder(colors),
                            )
                          : _channelPlaceholder(colors),
                    ),
                  ),
                ),
              )
            else
              // Video/playlist: 16:9 aspect with overlays for LIVE/duration
              SizedBox(
                width: 120,
                child: AspectRatio(
                  aspectRatio: 16 / 9,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(kRadiusSm),
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        // Thumbnail or placeholder
                        if (video.thumbnailUrl.isNotEmpty)
                          CachedNetworkImage(
                            imageUrl: video.thumbnailUrl,
                            fit: BoxFit.cover,
                            errorWidget: (_, __, ___) => _placeholder(colors),
                          )
                        else
                          _placeholder(colors),
                        // LIVE pill top-left (driven by constructor flag only;
                        // Video model has no isLive field — LiveItem wrapper is
                        // used elsewhere but not available on Video itself)
                        if (live)
                          Positioned(
                            top: 4,
                            left: 4,
                            child: Semantics(
                              label: 'Live',
                              child: Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 6,
                                  vertical: 2,
                                ),
                                decoration: BoxDecoration(
                                  color: kBrandRed,
                                  borderRadius: BorderRadius.circular(4),
                                ),
                                child: Text(
                                  'LIVE',
                                  style: textTheme.labelSmall?.copyWith(
                                    color: Colors.white,
                                    fontWeight: FontWeight.bold,
                                    height: 1,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        // Duration pill bottom-right
                        if (video.durationLabel.isNotEmpty && !live)
                          Positioned(
                            bottom: 4,
                            right: 4,
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 4,
                                vertical: 2,
                              ),
                              decoration: BoxDecoration(
                                color: Colors.black.withValues(alpha: 0.75),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                video.durationLabel,
                                style: textTheme.labelSmall?.copyWith(
                                  color: Colors.white,
                                  fontWeight: FontWeight.w500,
                                  height: 1,
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ),
            const SizedBox(width: 12),
            // Title + metadata
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Title row with optional watched check
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Text(
                          video.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.titleMedium?.copyWith(
                            color: watched ? colors.onSurfaceVariant : null,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                      if (watched) ...[
                        const SizedBox(width: 4),
                        Semantics(
                          label: 'Already watched',
                          child: Icon(
                            Icons.check,
                            size: 16,
                            color: colors.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 4),
                  // Metadata subtitle
                  Text(
                    [
                      video.channelTitle,
                      if (video.kind != 'video') video.kind,
                    ].where((s) => s.isNotEmpty).join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: textTheme.bodySmall?.copyWith(
                      color: colors.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Placeholder with kind icon centered on surfaceContainerHigh.
  Widget _placeholder(ColorScheme colors) {
    return Container(
      color: colors.surfaceContainerHigh,
      child: Center(
        child: Icon(
          switch (video.kind) {
            'channel' => Icons.person,
            'playlist' => Icons.playlist_play,
            _ => Icons.play_circle_outline,
          },
          color: colors.onSurfaceVariant,
          size: 32,
        ),
      ),
    );
  }

  /// Circular placeholder for channel avatars with tinted background.
  Widget _channelPlaceholder(ColorScheme colors) {
    return Container(
      decoration: BoxDecoration(
        color: colors.primaryContainer,
        shape: BoxShape.circle,
      ),
      child: Center(
        child: Icon(
          Icons.person,
          color: colors.onPrimaryContainer,
          size: 32,
        ),
      ),
    );
  }

  void _defaultTap(BuildContext context, WidgetRef ref) {
    switch (video.kind) {
      case 'channel':
        context.push(
            '/channel/${Uri.encodeComponent(video.videoId)}?platform=${video.platform}'
            '&title=${Uri.encodeComponent(video.title)}');
      case 'playlist':
        context.push('/ytplaylist/${Uri.encodeComponent(video.videoId)}'
            '?platform=${video.platform}');
      default:
        ref.read(queueProvider.notifier).play([video]);
        context.push('/player');
    }
  }

  void _showActions(BuildContext context, WidgetRef ref) {
    showModalBottomSheet<void>(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(kRadiusLg)),
      ),
      builder: (sheetContext) {
        final sheetColors = Theme.of(sheetContext).colorScheme;
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Grab handle
              Padding(
                padding: const EdgeInsets.only(top: 12, bottom: 8),
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: sheetColors.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              _sheetTile(
                sheetContext,
                icon: Icons.queue,
                label: 'Add to queue',
                onTap: () {
                  ref.read(queueProvider.notifier).enqueue(video);
                  Navigator.pop(sheetContext);
                },
              ),
              _sheetTile(
                sheetContext,
                icon: Icons.info_outline,
                label: 'Details',
                onTap: () {
                  Navigator.pop(sheetContext);
                  context.push('/detail/${Uri.encodeComponent(video.videoId)}'
                      '?platform=${video.platform}');
                },
              ),
              _sheetTile(
                sheetContext,
                icon: Icons.playlist_add,
                label: 'Save to playlist',
                onTap: () {
                  Navigator.pop(sheetContext);
                  _pickPlaylist(context, ref);
                },
              ),
              if (video.channelId.isNotEmpty)
                _sheetTile(
                  sheetContext,
                  icon: Icons.person_add,
                  label: 'Follow channel',
                  onTap: () async {
                    Navigator.pop(sheetContext);
                    final messenger = ScaffoldMessenger.of(context);
                    final ref_ = switch (video.platform) {
                      'bitchute' => 'bitchute:${video.channelId}',
                      'odysee' => 'odysee:${video.channelId}',
                      _ => video.channelId,
                    };
                    try {
                      await ref.read(apiProvider).followChannel(ref_);
                      messenger.showSnackBar(
                          SnackBar(content: Text('Following ${video.channelTitle}')));
                    } catch (e) {
                      messenger.showSnackBar(SnackBar(content: Text('$e')));
                    }
                  },
                ),
              const SizedBox(height: 8),
            ],
          ),
        );
      },
    );
  }

  /// Rounded list item for the bottom sheet.
  Widget _sheetTile(
    BuildContext context, {
    required IconData icon,
    required String label,
    required VoidCallback onTap,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(kRadiusSm),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(kRadiusSm),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Row(
              children: [
                Icon(icon, size: 24),
                const SizedBox(width: 16),
                Expanded(child: Text(label)),
              ],
            ),
          ),
        ),
      ),
    );
  }

  void _pickPlaylist(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    final playlists = await ref.read(apiProvider).playlists();
    if (!context.mounted) return;
    showModalBottomSheet<void>(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(kRadiusLg)),
      ),
      builder: (sheetContext) {
        final sheetColors = Theme.of(sheetContext).colorScheme;
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Grab handle
              Padding(
                padding: const EdgeInsets.only(top: 12, bottom: 8),
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: sheetColors.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              if (playlists.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(kGutter),
                  child: Text(
                    'No playlists — create one first',
                    style: Theme.of(sheetContext).textTheme.bodyMedium?.copyWith(
                          color: sheetColors.onSurfaceVariant,
                        ),
                  ),
                )
              else
                ...playlists.map((p) => _sheetTile(
                      sheetContext,
                      icon: Icons.playlist_play,
                      label: '${p.name} · ${p.count} items',
                      onTap: () async {
                        Navigator.pop(sheetContext);
                        final added =
                            await ref.read(apiProvider).addPlaylistItem(p.id, video);
                        messenger.showSnackBar(SnackBar(
                            content: Text(added
                                ? 'Saved to ${p.name}'
                                : 'Already in ${p.name}')));
                      },
                    )),
              const SizedBox(height: 8),
            ],
          ),
        );
      },
    );
  }
}
