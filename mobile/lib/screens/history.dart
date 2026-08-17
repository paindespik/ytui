/// Watch history: replay on tap, swipe to remove.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

class HistoryScreen extends ConsumerWidget {
  const HistoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(historyProvider);
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'History',
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
      body: history.when(
        loading: () => const AppLoading(),
        error: (e, _) =>
            AppError.from(e, onRetry: () => ref.invalidate(historyProvider)),
        data: (entries) {
          if (entries.isEmpty) {
            return const AppEmpty(
              message: 'No watch history yet',
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
                  key: ValueKey(entry.video.videoId),
                  direction: DismissDirection.endToStart,
                  background: Container(
                    color: colorScheme.error,
                    alignment: Alignment.centerRight,
                    padding: const EdgeInsets.only(right: kGutter),
                    child: Icon(Icons.delete, color: colorScheme.onError),
                  ),
                  onDismissed: (_) async {
                    await ref.read(apiProvider).removeWatch(
                        entry.video.videoId,
                        platform: entry.video.platform);
                    ref.invalidate(historyProvider);
                  },
                  child: VideoTile(
                    video: entry.video,
                    onTap: () {
                      ref.read(queueProvider.notifier).play([entry.video]);
                      context.push('/player');
                    },
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}
