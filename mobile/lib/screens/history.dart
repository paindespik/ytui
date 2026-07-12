/// Watch history: replay on tap, swipe to remove.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../state/providers.dart';
import '../state/queue.dart';
import '../widgets/video_tile.dart';
import '../widgets/app_state_views.dart';

class HistoryScreen extends ConsumerWidget {
  const HistoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(historyProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('History')),
      body: history.when(
        loading: () => const AppLoading(),
        error: (e, _) =>
            AppError.from(e, onRetry: () => ref.invalidate(historyProvider)),
        data: (entries) => entries.isEmpty
            ? const Center(child: Text('No history yet'))
            : ListView.builder(
                itemCount: entries.length,
                itemBuilder: (context, i) {
                  final entry = entries[i];
                  return Dismissible(
                    key: ValueKey(entry.video.videoId),
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
                          .removeWatch(entry.video.videoId);
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
      ),
    );
  }
}
