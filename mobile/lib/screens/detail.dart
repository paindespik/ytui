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
import '../widgets/comment_card.dart';
import '../widgets/open_player.dart';
import '../widgets/responsive.dart';
import '../widgets/screen_focus.dart';

/// Shown when an account action returns 409: no OAuth token on the server.
const kNotAuthenticated =
    'Non authentifié — lancez `ytui auth push` depuis le bureau';

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
      appBar: AppBar(title: const Text('Détails')),
      body: ScreenFocus(child: details.when(
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
                    label: '${compactCount(d.viewCount!)} vues',
                  ),
                if (d.likeCount != null)
                  _MetadataChip(
                    icon: Icons.thumb_up_outlined,
                    label: '${compactCount(d.likeCount!)} j’aime',
                  ),
                if (d.uploadDate.isNotEmpty)
                  _MetadataChip(
                    icon: Icons.calendar_today_outlined,
                    label: formatDateFr(d.uploadDate),
                  ),
              ],
            ),
            const SizedBox(height: 16),

            // Actions: play on every platform (also the first thing a remote
            // can focus here), like/comment on YouTube.
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  icon: const Icon(Icons.play_arrow_rounded),
                  label: const Text('Lire'),
                  onPressed: () => openPlayer(context, ref, [
                    Video(
                      videoId: videoId,
                      title: d.title,
                      channelTitle: d.channelTitle,
                      channelId: d.channelId,
                      duration: d.duration,
                      platform: platform,
                    ),
                  ]),
                ),
                if (platform == 'youtube') ...[
                  _LikeButton(videoId: videoId),
                  FilledButton.tonalIcon(
                    icon: const Icon(Icons.comment_outlined, size: 18),
                    label: const Text('Commentaire'),
                    onPressed: () => _comment(context, ref),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 12),

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
                          'Les likes/commentaires Odysee sont en lecture seule',
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
                child: _Description(text: d.description),
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
      )),
    );
  }

  Future<void> _comment(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Commentaire'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 3,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Annuler'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, controller.text.trim()),
            child: const Text('Publier'),
          ),
        ],
      ),
    );
    if (text == null || text.isEmpty || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).commentVideo(videoId, text, platform: platform);
      messenger.showSnackBar(
          const SnackBar(content: Text('Commentaire publié')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.statusCode == 409 ? kNotAuthenticated : e.toString())));
    }
  }
}

/// Like button reflecting — and able to undo — the account's current rating.
class _LikeButton extends ConsumerStatefulWidget {
  final String videoId;

  const _LikeButton({required this.videoId});

  @override
  ConsumerState<_LikeButton> createState() => _LikeButtonState();
}

class _LikeButtonState extends ConsumerState<_LikeButton> {
  String _rating = 'none';
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _fetchRating();
  }

  Future<void> _fetchRating() async {
    try {
      final rating = await ref.read(apiProvider).videoRating(widget.videoId);
      if (mounted) setState(() => _rating = rating);
    } on ApiException {
      // No account or no network: leave the button neutral.
    }
  }

  Future<void> _toggle() async {
    final next = _rating == 'like' ? 'none' : 'like';
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).likeVideo(widget.videoId, rating: next);
      if (!mounted) return;
      setState(() {
        _rating = next;
        _busy = false;
      });
      messenger.showSnackBar(SnackBar(
        content: Text(next == 'like' ? 'J’aime 👍' : 'Like retiré'),
      ));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      messenger.showSnackBar(SnackBar(
        content: Text(e.statusCode == 409 ? kNotAuthenticated : e.toString()),
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    final liked = _rating == 'like';
    return FilledButton.icon(
      icon: Icon(liked ? Icons.thumb_up : Icons.thumb_up_outlined, size: 18),
      label: const Text('J’aime'),
      onPressed: _busy ? null : _toggle,
    );
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
        'Commentaires indisponibles : $e',
        style: theme.textTheme.bodySmall?.copyWith(
          color: colors.onSurfaceVariant,
        ),
      ),
      data: (page) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Commentaires (${page.total})',
            style: theme.textTheme.titleMedium?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 12),
          if (page.items.isEmpty)
            Text(
              'Aucun commentaire pour le moment',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: colors.onSurfaceVariant,
              ),
            ),
          for (final c in page.items) CommentCard(comment: c),
        ],
      ),
    );
  }
}


/// Description, folded to [_foldedLines] lines with an "Afficher plus"
/// toggle when longer: the fold used to be final, the rest unreachable.
class _Description extends StatefulWidget {
  const _Description({required this.text});

  final String text;

  @override
  State<_Description> createState() => _DescriptionState();
}

class _DescriptionState extends State<_Description> {
  static const _foldedLines = 12;
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = theme.textTheme.bodyMedium?.copyWith(
      height: 1.6,
      color: theme.colorScheme.onSurfaceVariant,
    );
    if (widget.text.isEmpty) {
      return Text('Pas de description.', style: style);
    }
    return LayoutBuilder(builder: (context, constraints) {
      final painter = TextPainter(
        text: TextSpan(text: widget.text, style: style),
        maxLines: _foldedLines,
        textDirection: Directionality.of(context),
        textScaler: MediaQuery.textScalerOf(context),
      )..layout(maxWidth: constraints.maxWidth);
      final foldable = painter.didExceedMaxLines;
      painter.dispose();
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.text,
            maxLines: _expanded ? null : _foldedLines,
            overflow: _expanded ? TextOverflow.visible : TextOverflow.ellipsis,
            style: style,
          ),
          if (foldable)
            TextButton(
              onPressed: () => setState(() => _expanded = !_expanded),
              child: Text(_expanded ? 'Afficher moins' : 'Afficher plus'),
            ),
        ],
      );
    });
  }
}
