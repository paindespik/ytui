/// Video details: description, views, likes + Like / Comment actions.
/// Odysee: read-only comments listed below the description.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../api/models.dart';
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
            if (platform == 'odysee')
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(
                    children: [
                      const Icon(Icons.lock_outline, size: 18),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text('Odysee likes/comments are read-only',
                            style: Theme.of(context).textTheme.bodySmall),
                      ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 16),
            Text(d.description),
            if (platform == 'odysee') ...[
              const Divider(height: 32),
              _OdyseeComments(videoId: videoId),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _like(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).likeVideo(videoId, platform: platform);
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
      await ref.read(apiProvider).commentVideo(videoId, text, platform: platform);
      messenger.showSnackBar(const SnackBar(content: Text('Comment posted')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.statusCode == 409
              ? 'Not authenticated — run `ytui auth push` from the desktop'
              : e.toString())));
    }
  }
}

class _OdyseeComments extends ConsumerWidget {
  final String videoId;

  const _OdyseeComments({required this.videoId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final comments = ref.watch(commentsProvider((videoId, 'odysee')));

    return comments.when(
      loading: () => const Center(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: CircularProgressIndicator(),
        ),
      ),
      error: (e, _) => Text('Comments unavailable: $e',
          style: Theme.of(context).textTheme.bodySmall),
      data: (page) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Comments (${page.total})',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          if (page.items.isEmpty) const Text('No comments'),
          for (final c in page.items) _CommentCard(comment: c),
        ],
      ),
    );
  }
}

class _CommentCard extends StatelessWidget {
  final Comment comment;

  const _CommentCard({required this.comment});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final meta = [
      if (comment.isPinned) '📌',
      comment.channelName,
      if (comment.timestamp != null) _relativeDate(comment.timestamp!),
      if (comment.likes > 0) '👍 ${comment.likes}',
      if (comment.replies > 0)
        '${comment.replies} ${comment.replies == 1 ? 'reply' : 'replies'}',
    ].where((s) => s.isNotEmpty).join(' · ');

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(meta, style: theme.textTheme.bodySmall),
            const SizedBox(height: 4),
            Text(comment.text),
          ],
        ),
      ),
    );
  }

  static String _relativeDate(int epochSeconds) {
    final date = DateTime.fromMillisecondsSinceEpoch(epochSeconds * 1000);
    final diff = DateTime.now().difference(date);
    if (diff.inDays >= 365) return '${diff.inDays ~/ 365}y ago';
    if (diff.inDays >= 30) return '${diff.inDays ~/ 30}mo ago';
    if (diff.inDays >= 1) return '${diff.inDays}d ago';
    if (diff.inHours >= 1) return '${diff.inHours}h ago';
    if (diff.inMinutes >= 1) return '${diff.inMinutes}m ago';
    return 'just now';
  }
}
