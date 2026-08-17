/// Local (server-side) playlists: list, create, rename, delete, open.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../format.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/playlist_import.dart';
import '../widgets/responsive.dart';
import '../widgets/screen_focus.dart';
import '../widgets/video_tile.dart';

class LocalPlaylistsScreen extends ConsumerWidget {
  const LocalPlaylistsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final playlists = ref.watch(playlistsProvider);
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Playlists',
          style: TextStyle(fontWeight: FontWeight.bold),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.download),
            tooltip: 'Import a YouTube playlist',
            onPressed: () => promptImportPlaylist(context, ref),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _createOrRename(context, ref),
        tooltip: 'New playlist',
        child: const Icon(Icons.add),
      ),
      body: ScreenFocus(child: playlists.when(
        loading: () => const AppLoading(),
        error: (e, _) =>
            AppError.from(e, onRetry: () => ref.invalidate(playlistsProvider)),
        data: (items) {
          if (items.isEmpty) {
            return const AppEmpty(
              message: 'No playlists yet',
              icon: Icons.playlist_add,
            );
          }
          return ResponsiveCenter(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(
                horizontal: kGutter,
                vertical: kGutter / 2,
              ),
              itemCount: items.length,
              itemBuilder: (context, index) {
                final p = items[index];
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Card(
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(kRadiusMd),
                    ),
                    clipBehavior: Clip.antiAlias,
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: kGutter,
                        vertical: 4,
                      ),
                      leading: Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          color: colorScheme.primaryContainer,
                          shape: BoxShape.circle,
                        ),
                        child: Icon(
                          Icons.playlist_play,
                          color: colorScheme.onPrimaryContainer,
                        ),
                      ),
                      title: Text(
                        p.name,
                        style: const TextStyle(fontWeight: FontWeight.w500),
                      ),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            pluralize(p.count, 'item'),
                            style: TextStyle(
                              color: colorScheme.onSurfaceVariant,
                              fontSize: 13,
                            ),
                          ),
                          PopupMenuButton<String>(
                            tooltip: 'Show menu',
                            onSelected: (action) async {
                              if (action == 'rename') {
                                await _createOrRename(context, ref, playlist: p);
                              } else if (action == 'delete') {
                                await _delete(context, ref, p);
                              }
                            },
                            itemBuilder: (_) => const [
                              PopupMenuItem(
                                  value: 'rename', child: Text('Rename')),
                              PopupMenuItem(
                                  value: 'delete', child: Text('Delete')),
                            ],
                          ),
                        ],
                      ),
                      onTap: () => context.push('/playlists/${p.id}'
                          '?name=${Uri.encodeComponent(p.name)}'),
                    ),
                  ),
                );
              },
            ),
          );
        },
      )),
    );
  }

  Future<void> _createOrRename(BuildContext context, WidgetRef ref,
      {LocalPlaylist? playlist}) async {
    final controller = TextEditingController(text: playlist?.name ?? '');
    final name = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(playlist == null ? 'New playlist' : 'Rename playlist'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: InputDecoration(
            hintText: playlist == null ? 'Playlist name' : null,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(dialogContext, controller.text.trim()),
            child: Text(playlist == null ? 'Create' : 'Rename'),
          ),
        ],
      ),
    );
    if (name == null || name.isEmpty || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    final api = ref.read(apiProvider);
    final ok = playlist == null
        ? await api.createPlaylist(name) != null
        : await api.renamePlaylist(playlist.id, name);
    if (!ok) {
      messenger.showSnackBar(
          SnackBar(content: Text('Name "$name" already taken')));
    }
    ref.invalidate(playlistsProvider);
  }

  Future<void> _delete(
      BuildContext context, WidgetRef ref, LocalPlaylist playlist) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Delete "${playlist.name}"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await ref.read(apiProvider).deletePlaylist(playlist.id);
    ref.invalidate(playlistsProvider);
  }
}

class LocalPlaylistScreen extends ConsumerWidget {
  final int playlistId;
  final String name;

  const LocalPlaylistScreen(
      {super.key, required this.playlistId, this.name = ''});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(playlistItemsProvider(playlistId));
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          name.isEmpty ? 'Playlist' : name,
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
      ),
      body: ScreenFocus(child: items.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () => ref.invalidate(playlistItemsProvider(playlistId))),
        data: (entries) {
          if (entries.isEmpty) {
            return const AppEmpty(
              message: 'This playlist is empty',
              icon: Icons.playlist_remove,
            );
          }
          final videos = entries
              .map((e) => e.video)
              .where((v) => v.kind == 'video')
              .toList();
          return ResponsiveCenter(
            child: ListView.builder(
              padding: const EdgeInsets.only(bottom: kGutter),
              itemCount: entries.length + 1,
              itemBuilder: (context, index) {
                if (index == 0) {
                  // Prominent "Play all" header
                  return Padding(
                    padding:
                        const EdgeInsets.fromLTRB(kGutter, kGutter, kGutter, 8),
                    child: FilledButton.icon(
                      onPressed: videos.isEmpty
                          ? null
                          : () {
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
                final entry = entries[index - 1];
                return Dismissible(
                  key: ValueKey('${entry.position}-${entry.video.videoId}'),
                  direction: DismissDirection.endToStart,
                  background: Container(
                    color: colorScheme.error,
                    alignment: Alignment.centerRight,
                    padding: const EdgeInsets.only(right: kGutter),
                    child: Icon(Icons.delete, color: colorScheme.onError),
                  ),
                  onDismissed: (_) async {
                    await ref
                        .read(apiProvider)
                        .removePlaylistItem(playlistId, entry.position);
                    ref.invalidate(playlistItemsProvider(playlistId));
                    ref.invalidate(playlistsProvider);
                  },
                  child: VideoTile(video: entry.video),
                );
              },
            ),
          );
        },
      )),
    );
  }
}
