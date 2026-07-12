/// Video details: description, views, likes + Like / Comment actions.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../state/providers.dart';

class DetailScreen extends ConsumerWidget {
  final String videoId;
  final String platform;

  const DetailScreen({super.key, required this.videoId, this.platform = 'youtube'});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final details = ref.watch(videoDetailsProvider((videoId, platform)));

    return Scaffold(
      appBar: AppBar(title: const Text('Details')),
      body: details.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('$e')),
        data: (d) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(d.title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(d.channelTitle,
                style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 8),
            Wrap(
              spacing: 16,
              children: [
                if (d.viewCount != null) Text('${d.viewCount} views'),
                if (d.likeCount != null) Text('${d.likeCount} likes'),
                if (d.uploadDate.isNotEmpty) Text(d.uploadDate),
              ],
            ),
            const SizedBox(height: 16),
            if (platform == 'youtube')
              Row(
                children: [
                  FilledButton.icon(
                    icon: const Icon(Icons.thumb_up),
                    label: const Text('Like'),
                    onPressed: () => _like(context, ref),
                  ),
                  const SizedBox(width: 12),
                  OutlinedButton.icon(
                    icon: const Icon(Icons.comment),
                    label: const Text('Comment'),
                    onPressed: () => _comment(context, ref),
                  ),
                ],
              ),
            const SizedBox(height: 16),
            Text(d.description),
          ],
        ),
      ),
    );
  }

  Future<void> _like(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).likeVideo(videoId);
      messenger.showSnackBar(const SnackBar(content: Text('Liked 👍')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.statusCode == 409
              ? 'Not authenticated — run `ytui auth push` from the desktop'
              : e.toString())));
    }
  }

  Future<void> _comment(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Comment'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 3,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, controller.text.trim()),
            child: const Text('Post'),
          ),
        ],
      ),
    );
    if (text == null || text.isEmpty || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).commentVideo(videoId, text);
      messenger.showSnackBar(const SnackBar(content: Text('Comment posted')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.statusCode == 409
              ? 'Not authenticated — run `ytui auth push` from the desktop'
              : e.toString())));
    }
  }
}
