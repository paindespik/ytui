/// Local (server-side) playlists: list, create, rename, delete, open.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../widgets/video_tile.dart';
import '../widgets/app_state_views.dart';

class LocalPlaylistsScreen extends ConsumerWidget {
  const LocalPlaylistsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final playlists = ref.watch(playlistsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Playlists')),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _createOrRename(context, ref),
        child: const Icon(Icons.add),
      ),
      body: playlists.when(
        loading: () => const AppLoading(),
        error: (e, _) =>
            AppError.from(e, onRetry: () => ref.invalidate(playlistsProvider)),
        data: (items) => items.isEmpty
            ? const Center(child: Text('No playlists'))
            : ListView(
                children: [
                  for (final p in items)
                    ListTile(
                      leading: const Icon(Icons.playlist_play),
                      title: Text(p.name),
                      subtitle: Text('${p.count} items'),
                      onTap: () => context.push('/playlists/${p.id}'
                          '?name=${Uri.encodeComponent(p.name)}'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (action) async {
                          if (action == 'rename') {
                            await _createOrRename(context, ref, playlist: p);
                          } else if (action == 'delete') {
                            await _delete(context, ref, p);
                          }
                        },
                        itemBuilder: (_) => const [
                          PopupMenuItem(value: 'rename', child: Text('Rename')),
                          PopupMenuItem(value: 'delete', child: Text('Delete')),
                        ],
                      ),
                    ),
                ],
              ),
      ),
    );
  }

  Future<void> _createOrRename(BuildContext context, WidgetRef ref,
      {LocalPlaylist? playlist}) async {
    final controller = TextEditingController(text: playlist?.name ?? '');
    final name = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(playlist == null ? 'New playlist' : 'Rename playlist'),
        content: TextField(controller: controller, autofocus: true),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, controller.text.trim()),
            child: const Text('OK'),
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

    return Scaffold(
      appBar: AppBar(
        title: Text(name.isEmpty ? 'Playlist' : name),
        actions: [
          IconButton(
            icon: const Icon(Icons.playlist_play),
            tooltip: 'Play all',
            onPressed: items.valueOrNull == null || items.valueOrNull!.isEmpty
                ? null
                : () {
                    final videos = items.valueOrNull!
                        .map((e) => e.video)
                        .where((v) => v.kind == 'video')
                        .toList();
                    if (videos.isEmpty) return;
                    ref.read(queueProvider.notifier).play(videos);
                    context.push('/player');
                  },
          ),
        ],
      ),
      body: items.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () => ref.invalidate(playlistItemsProvider(playlistId))),
        data: (entries) => entries.isEmpty
            ? const Center(child: Text('Empty playlist'))
            : ListView.builder(
                itemCount: entries.length,
                itemBuilder: (context, i) {
                  final entry = entries[i];
                  return Dismissible(
                    key: ValueKey('${entry.position}-${entry.video.videoId}'),
                    direction: DismissDirection.endToStart,
                    background: Container(
                      color: Theme.of(context).colorScheme.error,
                      alignment: Alignment.centerRight,
                      padding: const EdgeInsets.only(right: 16),
                      child: const Icon(Icons.delete),
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
      ),
    );
  }
}
