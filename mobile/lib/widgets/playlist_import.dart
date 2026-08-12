/// Import a whole upstream playlist (YouTube…) into a local ytui playlist.
///
/// Shared by the YouTube playlist screen, the video tile menu and the local
/// playlists screen so every entry point behaves the same: pick an existing
/// playlist or create one, then copy every video server-side.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';

/// Ask for a playlist URL/id, then run the import flow. Returns the local
/// playlist when something was imported.
Future<LocalPlaylist?> promptImportPlaylist(
  BuildContext context,
  WidgetRef ref, {
  String platform = 'youtube',
}) async {
  final controller = TextEditingController();
  final source = await showDialog<String>(
    context: context,
    builder: (dialogContext) => AlertDialog(
      title: const Text('Import a YouTube playlist'),
      content: TextField(
        controller: controller,
        autofocus: true,
        decoration: const InputDecoration(hintText: 'Playlist URL or id'),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(dialogContext),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(dialogContext, controller.text.trim()),
          child: const Text('Continue'),
        ),
      ],
    ),
  );
  if (source == null || source.isEmpty || !context.mounted) return null;
  return showImportPlaylistSheet(context, ref, source: source, platform: platform);
}

/// Pick the destination (existing playlist or a new one) and import into it.
Future<LocalPlaylist?> showImportPlaylistSheet(
  BuildContext context,
  WidgetRef ref, {
  required String source,
  String platform = 'youtube',
  String defaultName = '',
}) async {
  if (source.isEmpty) return null;
  final messenger = ScaffoldMessenger.of(context);
  List<LocalPlaylist> playlists;
  try {
    playlists = await ref.read(apiProvider).playlists();
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text('$e')));
    return null;
  }
  if (!context.mounted) return null;

  final choice = await showModalBottomSheet<Object>(
    context: context,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(kRadiusLg)),
    ),
    builder: (sheetContext) {
      final colors = Theme.of(sheetContext).colorScheme;
      return SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 12, bottom: 8),
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: colors.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(kGutter, 0, kGutter, 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'Import playlist into…',
                  style: Theme.of(sheetContext).textTheme.titleSmall,
                ),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.playlist_add),
              title: const Text('New playlist'),
              onTap: () => Navigator.pop(sheetContext, 'new'),
            ),
            ...playlists.map((p) => ListTile(
                  leading: const Icon(Icons.playlist_play),
                  title: Text(p.name),
                  subtitle: Text('${p.count} items'),
                  onTap: () => Navigator.pop(sheetContext, p),
                )),
            const SizedBox(height: 8),
          ],
        ),
      );
    },
  );
  if (choice == null || !context.mounted) return null;

  String name = '';
  int? targetId;
  if (choice is LocalPlaylist) {
    targetId = choice.id;
  } else {
    final controller = TextEditingController(text: defaultName);
    final picked = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('New playlist'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'Playlist name'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(dialogContext, controller.text.trim()),
            child: const Text('Import'),
          ),
        ],
      ),
    );
    if (picked == null || !context.mounted) return null;
    name = picked; // empty: the server names it after the upstream playlist
  }

  messenger.showSnackBar(const SnackBar(content: Text('Importing playlist…')));
  try {
    final result = await ref.read(apiProvider).importPlaylist(
          source,
          platform: platform,
          name: name,
          targetId: targetId,
        );
    ref.invalidate(playlistsProvider);
    if (targetId != null) ref.invalidate(playlistItemsProvider(targetId));
    final skipped = result.skipped > 0 ? ', ${result.skipped} skipped' : '';
    messenger.showSnackBar(SnackBar(
      content: Text(
          'Imported ${result.added} videos into "${result.playlist.name}"$skipped'),
    ));
    return result.playlist;
  } on ApiException catch (e) {
    messenger.showSnackBar(SnackBar(
      content: Text(e.statusCode == 409
          ? 'Name "$name" already taken'
          : 'Import failed: ${e.detail}'),
    ));
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text('Import failed: $e')));
  }
  return null;
}
