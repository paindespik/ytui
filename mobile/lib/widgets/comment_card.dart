/// One comment, shared by the details screen and the player's comments panel.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../theme.dart';

class CommentCard extends StatelessWidget {
  final Comment comment;

  /// Tapping the reply count expands the thread; null keeps the count inert.
  final VoidCallback? onToggleReplies;
  final bool repliesExpanded;

  const CommentCard({
    super.key,
    required this.comment,
    this.onToggleReplies,
    this.repliesExpanded = false,
  });

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
                        if (comment.replies > 0 && onToggleReplies != null)
                          InkWell(
                            onTap: onToggleReplies,
                            borderRadius: BorderRadius.circular(kRadiusSm),
                            child: Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 4,
                                vertical: 2,
                              ),
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Icon(
                                    repliesExpanded
                                        ? Icons.expand_less
                                        : Icons.reply_outlined,
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
                              ),
                            ),
                          )
                        else if (comment.replies > 0) ...[
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
