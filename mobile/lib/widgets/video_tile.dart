/// Shared list tile for feed/search/channel/playlist entries.
library;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../state/queue.dart';

class VideoTile extends ConsumerWidget {
  final Video video;
  final bool live;
  final VoidCallback? onTap;

  const VideoTile({super.key, required this.video, this.live = false, this.onTap});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final watched =
        ref.watch(watchedIdsProvider).valueOrNull?.contains(video.videoId) ?? false;

    return ListTile(
      leading: SizedBox(
        width: 96,
        child: video.thumbnailUrl.isNotEmpty
            ? ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: CachedNetworkImage(
                  imageUrl: video.thumbnailUrl,
                  fit: BoxFit.cover,
                  errorWidget: (_, __, ___) => _kindIcon(),
                ),
              )
            : _kindIcon(),
      ),
      title: Row(
        children: [
          if (live)
            Padding(
              padding: const EdgeInsets.only(right: 4),
              child: Semantics(
                label: 'Live',
                child: const Icon(Icons.circle, color: Colors.red, size: 10),
              ),
            ),
          Expanded(
            child: Text(
              video.title,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: watched
                  ? TextStyle(color: Theme.of(context).disabledColor)
                  : null,
            ),
          ),
          if (watched)
            Semantics(
                label: 'Already watched',
                child: const Icon(Icons.check, size: 14)),
        ],
      ),
      subtitle: Text(
        [
          video.channelTitle,
          if (video.durationLabel.isNotEmpty) video.durationLabel,
          if (video.kind != 'video') video.kind,
        ].where((s) => s.isNotEmpty).join(' · '),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      onTap: onTap ?? () => _defaultTap(context, ref),
      onLongPress: video.kind == 'video' ? () => _showActions(context, ref) : null,
    );
  }

  Widget _kindIcon() => Icon(switch (video.kind) {
        'channel' => Icons.person,
        'playlist' => Icons.playlist_play,
        _ => Icons.play_circle_outline,
      });

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
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.queue),
              title: const Text('Add to queue'),
              onTap: () {
                ref.read(queueProvider.notifier).enqueue(video);
                Navigator.pop(sheetContext);
              },
            ),
            ListTile(
              leading: const Icon(Icons.info_outline),
              title: const Text('Details'),
              onTap: () {
                Navigator.pop(sheetContext);
                context.push('/detail/${Uri.encodeComponent(video.videoId)}'
                    '?platform=${video.platform}');
              },
            ),
            ListTile(
              leading: const Icon(Icons.playlist_add),
              title: const Text('Save to playlist'),
              onTap: () {
                Navigator.pop(sheetContext);
                _pickPlaylist(context, ref);
              },
            ),
            if (video.channelId.isNotEmpty)
              ListTile(
                leading: const Icon(Icons.person_add),
                title: const Text('Follow channel'),
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
          ],
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
      builder: (sheetContext) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            for (final p in playlists)
              ListTile(
                leading: const Icon(Icons.playlist_play),
                title: Text(p.name),
                subtitle: Text('${p.count} items'),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  final added =
                      await ref.read(apiProvider).addPlaylistItem(p.id, video);
                  messenger.showSnackBar(SnackBar(
                      content: Text(added
                          ? 'Saved to ${p.name}'
                          : 'Already in ${p.name}')));
                },
              ),
            if (playlists.isEmpty)
              const ListTile(title: Text('No playlists — create one first')),
          ],
        ),
      ),
    );
  }
}
