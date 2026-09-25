/// Local (server-side) playlists: list, create, rename, delete, open.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../format.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/open_player.dart';
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
            tooltip: 'Importer une playlist YouTube',
            onPressed: () => promptImportPlaylist(context, ref),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _createOrRename(context, ref),
        tooltip: 'Nouvelle playlist',
        child: const Icon(Icons.add),
      ),
      body: ScreenFocus(child: playlists.when(
        loading: () => const AppLoading(),
        error: (e, _) =>
            AppError.from(e, onRetry: () => ref.invalidate(playlistsProvider)),
        data: (items) {
          if (items.isEmpty) {
            return const AppEmpty(
              message: 'Aucune playlist pour le moment',
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
                            pluralize(p.count, 'élément'),
                            style: TextStyle(
                              color: colorScheme.onSurfaceVariant,
                              fontSize: 13,
                            ),
                          ),
                          PopupMenuButton<String>(
                            tooltip: 'Menu',
                            onSelected: (action) async {
                              if (action == 'rename') {
                                await _createOrRename(context, ref, playlist: p);
                              } else if (action == 'delete') {
                                await _delete(context, ref, p);
                              }
                            },
                            itemBuilder: (_) => const [
                              PopupMenuItem(
                                  value: 'rename',
                                  child: Text('Renommer')),
                              PopupMenuItem(
                                  value: 'delete',
                                  child: Text('Supprimer')),
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
    final name = playlist == null
        // Shared helper: the same dialog is offered from the per-video
        // "save to playlist" sheet (no circular import this way).
        ? await promptNewPlaylistName(context)
        : await _renameDialog(context, playlist);
    if (name == null || name.isEmpty || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    final api = ref.read(apiProvider);
    final bool ok;
    try {
      ok = playlist == null
          ? await api.createPlaylist(name) != null
          : await api.renamePlaylist(playlist.id, name);
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(AppError.messageFor(e))));
      return;
    }
    if (!ok) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Ce nom est déjà utilisé')));
    }
    ref.invalidate(playlistsProvider);
  }

  Future<String?> _renameDialog(BuildContext context, LocalPlaylist playlist) {
    final controller = TextEditingController(text: playlist.name);
    return showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Renommer la playlist'),
        content: TextField(controller: controller, autofocus: true),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Annuler'),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(dialogContext, controller.text.trim()),
            child: const Text('Renommer'),
          ),
        ],
      ),
    );
  }

  Future<void> _delete(
      BuildContext context, WidgetRef ref, LocalPlaylist playlist) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Supprimer « ${playlist.name} » ?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Annuler'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Supprimer'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).deletePlaylist(playlist.id);
    } catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text('Échec de la suppression : ${AppError.messageFor(e)}')));
    }
    ref.invalidate(playlistsProvider);
  }
}

class LocalPlaylistScreen extends ConsumerStatefulWidget {
  final int playlistId;
  final String name;

  const LocalPlaylistScreen(
      {super.key, required this.playlistId, this.name = ''});

  @override
  ConsumerState<LocalPlaylistScreen> createState() =>
      _LocalPlaylistScreenState();
}

class _LocalPlaylistScreenState extends ConsumerState<LocalPlaylistScreen> {
  /// Entries removed here, hidden right away (see HistoryScreen: a dismissed
  /// Dismissible must not come back while the list re-fetches). A video is in
  /// a playlist at most once, so the platform-qualified id is a unique key.
  final Set<String> _removed = {};

  /// Removals run one after the other: the backend compacts positions after
  /// each one, so two quick swipes on stale positions deleted the neighbour.
  Future<void> _removals = Future<void>.value();

  static String _key(Video video) => '${video.platform}:${video.videoId}';

  void _remove(Video video) {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _removed.add(_key(video)));
    _removals = _removals.then((_) async {
      final api = ref.read(apiProvider);
      try {
        // The position is read fresh for this very removal.
        final items = await api.playlistItems(widget.playlistId);
        final index = items.indexWhere((i) => _key(i.video) == _key(video));
        if (index >= 0) {
          await api.removePlaylistItem(widget.playlistId, items[index].position);
        }
      } catch (e) {
        if (mounted) setState(() => _removed.remove(_key(video)));
        messenger.showSnackBar(SnackBar(
          content: Text('Échec de la suppression : ${AppError.messageFor(e)}'),
        ));
      }
      if (!mounted) return;
      ref.invalidate(playlistItemsProvider(widget.playlistId));
      ref.invalidate(playlistsProvider);
    });
  }

  @override
  Widget build(BuildContext context) {
    final playlistId = widget.playlistId;
    final name = widget.name;
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
        data: (all) {
          final entries = [
            for (final entry in all)
              if (!_removed.contains(_key(entry.video))) entry,
          ];
          if (entries.isEmpty) {
            return const AppEmpty(
              message: 'Cette playlist est vide',
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
                          : () => openPlayer(context, ref, videos),
                      icon: const Icon(Icons.play_arrow),
                      label: Text('Lire les ${videos.length} vidéos'),
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
                // Tapping a video plays the playlist from there on, like the
                // "Play all" button does from the top.
                final start = videos.indexOf(entry.video);
                return Dismissible(
                  key: ValueKey(_key(entry.video)),
                  direction: DismissDirection.endToStart,
                  background: Container(
                    color: colorScheme.error,
                    alignment: Alignment.centerRight,
                    padding: const EdgeInsets.only(right: kGutter),
                    child: Icon(Icons.delete, color: colorScheme.onError),
                  ),
                  onDismissed: (_) => _remove(entry.video),
                  child: VideoTile(
                    video: entry.video,
                    onTap: start < 0
                        ? null
                        : () => openPlayer(context, ref, videos,
                            startIndex: start),
                    onRemove: () => _remove(entry.video),
                    removeLabel: 'Retirer de la playlist',
                  ),
                );
              },
            ),
          );
        },
      )),
    );
  }
}
