/// Video details: description, views, likes + Like / Comment actions.
/// Odysee: read-only comments listed below the description.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../format.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';

class DetailScreen extends ConsumerWidget {
  final String videoId;
  final String platform;

  const DetailScreen({super.key, required this.videoId, this.platform = 'youtube'});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final details = ref.watch(videoDetailsProvider((videoId, platform)));
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Details')),
      body: details.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () =>
                ref.invalidate(videoDetailsProvider((videoId, platform)))),
        data: (d) => ResponsiveCenter(
          child: ListView(
            padding: const EdgeInsets.all(kGutter),
            children: [
            // Title
            Text(
              d.title,
              style: theme.textTheme.titleLarge?.copyWith(
                fontWeight: FontWeight.w600,
                height: 1.3,
              ),
            ),
            const SizedBox(height: 8),

            // Channel
            Text(
              d.channelTitle,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: colors.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 12),

            // Metadata chips row
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (d.viewCount != null)
                  _MetadataChip(
                    icon: Icons.visibility_outlined,
                    label: '${compactCount(d.viewCount!)} views',
                  ),
                if (d.likeCount != null)
                  _MetadataChip(
                    icon: Icons.thumb_up_outlined,
                    label: '${compactCount(d.likeCount!)} likes',
                  ),
                if (d.uploadDate.isNotEmpty)
                  _MetadataChip(
                    icon: Icons.calendar_today_outlined,
                    label: d.uploadDate,
                  ),
              ],
            ),
            const SizedBox(height: 16),

            // Action buttons (YouTube only)
            if (platform == 'youtube')
              Row(
                children: [
                  FilledButton.icon(
                    icon: const Icon(Icons.thumb_up, size: 18),
                    label: const Text('Like'),
                    onPressed: () => _like(context, ref),
                  ),
                  const SizedBox(width: 12),
                  FilledButton.tonalIcon(
                    icon: const Icon(Icons.comment_outlined, size: 18),
                    label: const Text('Comment'),
                    onPressed: () => _comment(context, ref),
                  ),
                ],
              ),

            // Odysee read-only notice
            if (platform == 'odysee')
              Card(
                color: colors.surfaceContainerHighest,
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 10,
                  ),
                  child: Row(
                    children: [
                      Icon(
                        Icons.lock_outline,
                        size: 16,
                        color: colors.onSurfaceVariant,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Odysee likes/comments are read-only',
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: colors.onSurfaceVariant,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 16),

            // Description card
            Card(
              color: colors.surfaceContainerHigh,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(kRadiusMd),
              ),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Text(
                  d.description,
                  maxLines: 12,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    height: 1.6,
                    color: colors.onSurfaceVariant,
                  ),
                ),
              ),
            ),

            // Odysee comments section
            if (platform == 'odysee') ...[
              const SizedBox(height: 24),
              _OdyseeComments(videoId: videoId),
            ],
          ],
          ),
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

/// Compact metadata chip with icon + label.
class _MetadataChip extends StatelessWidget {
  final IconData icon;
  final String label;

  const _MetadataChip({required this.icon, required this.label});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(kRadiusSm),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: colors.onSurfaceVariant),
          const SizedBox(width: 6),
          Text(
            label,
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  color: colors.onSurfaceVariant,
                ),
          ),
        ],
      ),
    );
  }
}

class _OdyseeComments extends ConsumerWidget {
  final String videoId;

  const _OdyseeComments({required this.videoId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final comments = ref.watch(commentsProvider((videoId, 'odysee')));
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    return comments.when(
      loading: () => Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: colors.primary,
          ),
        ),
      ),
      error: (e, _) => Text(
        'Comments unavailable: $e',
        style: theme.textTheme.bodySmall?.copyWith(
          color: colors.onSurfaceVariant,
        ),
      ),
      data: (page) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Comments (${page.total})',
            style: theme.textTheme.titleMedium?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 12),
          if (page.items.isEmpty)
            Text(
              'No comments yet',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: colors.onSurfaceVariant,
              ),
            ),
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
    final colors = theme.colorScheme;

    // Extract initial for avatar
    final initial = comment.channelName.isNotEmpty
        ? comment.channelName[0].toUpperCase()
        : '?';

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      color: colors.surfaceContainer,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(kRadiusMd),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Avatar circle with initial
            Container(
              width: 36,
              height: 36,
              decoration: BoxDecoration(
                color: colors.primaryContainer,
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: Text(
                initial,
                style: theme.textTheme.labelLarge?.copyWith(
                  color: colors.onPrimaryContainer,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            const SizedBox(width: 12),

            // Comment content
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Header row: author + date + pinned badge
                  Row(
                    children: [
                      if (comment.isPinned) ...[
                        Icon(
                          Icons.push_pin,
                          size: 12,
                          color: colors.primary,
                        ),
                        const SizedBox(width: 4),
                      ],
                      Flexible(
                        child: Text(
                          comment.channelName,
                          style: theme.textTheme.labelMedium?.copyWith(
                            fontWeight: FontWeight.w600,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (comment.timestamp != null) ...[
                        const SizedBox(width: 8),
                        Text(
                          _relativeDate(comment.timestamp!),
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: colors.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 6),

                  // Comment body
                  Text(
                    comment.text,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      height: 1.4,
                    ),
                  ),

                  // Engagement row (likes/replies)
                  if (comment.likes > 0 || comment.replies > 0) ...[
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        if (comment.likes > 0) ...[
                          Icon(
                            Icons.thumb_up_outlined,
                            size: 12,
                            color: colors.onSurfaceVariant,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            '${comment.likes}',
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: colors.onSurfaceVariant,
                            ),
                          ),
                        ],
                        if (comment.likes > 0 && comment.replies > 0)
                          const SizedBox(width: 12),
                        if (comment.replies > 0) ...[
                          Icon(
                            Icons.reply_outlined,
                            size: 12,
                            color: colors.onSurfaceVariant,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            '${comment.replies} ${comment.replies == 1 ? 'reply' : 'replies'}',
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: colors.onSurfaceVariant,
                            ),
                          ),
                        ],
                      ],
                    ),
                  ],
                ],
              ),
            ),
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
