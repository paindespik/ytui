/// Watch history: replay on tap, swipe to remove.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/open_player.dart';
import '../widgets/responsive.dart';
import '../widgets/screen_focus.dart';
import '../widgets/video_tile.dart';

class HistoryScreen extends ConsumerStatefulWidget {
  const HistoryScreen({super.key});

  @override
  ConsumerState<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends ConsumerState<HistoryScreen> {
  /// Entries removed here, hidden right away: the provider only drops them
  /// after the DELETE and a re-fetch, and a rebuild in between would put a
  /// dismissed Dismissible back in the tree (assertion in debug, flicker in
  /// release). Platform-qualified: the same id can exist on two platforms.
  final Set<String> _removed = {};

  static String _key(Video video) => '${video.platform}:${video.videoId}';

  Future<void> _remove(Video video) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _removed.add(_key(video)));
    try {
      await ref
          .read(apiProvider)
          .removeWatch(video.videoId, platform: video.platform);
      ref.invalidate(historyProvider);
    } catch (e) {
      if (mounted) setState(() => _removed.remove(_key(video)));
      messenger.showSnackBar(SnackBar(
        content: Text('Échec de la suppression : ${AppError.messageFor(e)}'),
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    final history = ref.watch(historyProvider);
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Historique',
          style: TextStyle(fontWeight: FontWeight.bold),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Actualiser',
            onPressed: () => ref.invalidate(historyProvider),
          ),
        ],
      ),
      body: ScreenFocus(child: history.when(
        loading: () => const AppLoading(),
        error: (e, _) =>
            AppError.from(e, onRetry: () => ref.invalidate(historyProvider)),
        data: (all) {
          final entries = [
            for (final entry in all)
              if (!_removed.contains(_key(entry.video))) entry,
          ];
          if (entries.isEmpty) {
            return const AppEmpty(
              message: 'Aucun historique de visionnage',
              icon: Icons.history,
            );
          }
          return ResponsiveCenter(
            child: ListView.builder(
              padding: const EdgeInsets.only(bottom: kGutter),
              itemCount: entries.length,
              itemBuilder: (context, i) {
                final entry = entries[i];
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
                    onTap: () => openPlayer(context, ref, [entry.video]),
                    onRemove: () => _remove(entry.video),
                    removeLabel: 'Retirer de l\'historique',
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
