/// Shared list tile for feed/search/channel/playlist entries.
library;

import 'dart:async';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../format.dart';
import '../state/providers.dart';
import '../state/settings.dart';
import '../state/queue.dart';
import '../theme.dart';
import 'open_player.dart';
import 'playlist_import.dart';

class VideoTile extends ConsumerWidget {
  final Video video;
  final bool live;
  final VoidCallback? onTap;

  /// Removes the entry from the list showing it (history, local playlist):
  /// offered in the actions sheet under [removeLabel]. Lists also bind it to a
  /// swipe, which a remote cannot do — the sheet is the TV way.
  final VoidCallback? onRemove;
  final String removeLabel;

  const VideoTile({
    super.key,
    required this.video,
    this.live = false,
    this.onTap,
    this.onRemove,
    this.removeLabel = 'Retirer',
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final watched = ref
            .watch(watchedIdsProvider)
            .valueOrNull
            ?.contains('${video.platform}:${video.videoId}') ??
        false;
    final colors = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;

    // A remote has no long-press: on TV the actions sheet needs a focusable
    // affordance. It must be a SIBLING of the tile, never a child: directional
    // focus traversal only considers nodes whose centre lies beyond the current
    // node's edge, so a button nested inside the row is unreachable with
    // D-pad right (focus jumps to the app bar instead).
    final isTv = ref.watch(isTvProvider);
    final showActionsButton = video.kind != 'channel' && isTv;
    // Read from a couch across the room: larger thumbnails on TV, in the
    // wider column ResponsiveCenter grants there.
    final thumbWidth = isTv ? 176.0 : 120.0;

    final tile = InkWell(
      onTap: onTap ?? () => _defaultTap(context, ref),
      onLongPress:
          video.kind == 'channel' ? null : () => showActions(context, ref),
      borderRadius: BorderRadius.circular(kRadiusSm),
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          kGutter,
          8,
          showActionsButton ? 4 : kGutter,
          8,
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Thumbnail: circular for channels, 16:9 for videos/playlists
            if (video.kind == 'channel')
              // Channel avatar - circular thumbnail centered in same width
              SizedBox(
                width: thumbWidth,
                child: Center(
                  child: SizedBox(
                    width: 68,
                    height: 68,
                    child: ClipOval(
                      child: video.thumbnailUrl.isNotEmpty
                          ? CachedNetworkImage(
                              imageUrl: video.thumbnailUrl,
                              fit: BoxFit.cover,
                              errorWidget: (_, __, ___) =>
                                  _channelPlaceholder(colors),
                            )
                          : _channelPlaceholder(colors),
                    ),
                  ),
                ),
              )
            else
              // Video/playlist: 16:9 aspect with overlays for LIVE/duration
              SizedBox(
                width: thumbWidth,
                child: AspectRatio(
                  aspectRatio: 16 / 9,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(kRadiusSm),
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        // Thumbnail or placeholder (live streams without a
                        // thumbnail get a colored "EN DIRECT" block)
                        if (video.thumbnailUrl.isNotEmpty)
                          CachedNetworkImage(
                            imageUrl: video.thumbnailUrl,
                            fit: BoxFit.cover,
                            errorWidget: (_, __, ___) => live
                                ? _livePlaceholder(colors)
                                : _placeholder(colors),
                          )
                        else
                          live
                              ? _livePlaceholder(colors)
                              : _placeholder(colors),
                        // LIVE pill top-left (driven by constructor flag only;
                        // Video model has no isLive field — LiveItem wrapper is
                        // used elsewhere but not available on Video itself)
                        if (live)
                          Positioned(
                            top: 4,
                            left: 4,
                            child: Semantics(
                              label: switch (video.platform) {
                                'twitch' => 'Twitch',
                                'tiktok' => 'TikTok',
                                _ => 'Live',
                              },
                              child: Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 6,
                                  vertical: 2,
                                ),
                                decoration: BoxDecoration(
                                  color: switch (video.platform) {
                                    'twitch' => kTwitchPurple,
                                    'tiktok' => kTikTokRed,
                                    _ => kBrandRed,
                                  },
                                  borderRadius: BorderRadius.circular(4),
                                ),
                                child: Text(
                                  switch (video.platform) {
                                    'twitch' => 'TWITCH',
                                    'tiktok' => 'TIKTOK',
                                    _ => 'LIVE',
                                  },
                                  style: textTheme.labelSmall?.copyWith(
                                    color: Colors.white,
                                    fontWeight: FontWeight.bold,
                                    height: 1,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        // Duration pill bottom-right
                        if (video.durationLabel.isNotEmpty && !live)
                          Positioned(
                            bottom: 4,
                            right: 4,
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 4,
                                vertical: 2,
                              ),
                              decoration: BoxDecoration(
                                color: Colors.black.withValues(alpha: 0.75),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                video.durationLabel,
                                style: textTheme.labelSmall?.copyWith(
                                  color: Colors.white,
                                  fontWeight: FontWeight.w500,
                                  height: 1,
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ),
            const SizedBox(width: 12),
            // Title + metadata
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Title row with optional watched check
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Text(
                          video.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.titleMedium?.copyWith(
                            color: watched ? colors.onSurfaceVariant : null,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                      if (watched) ...[
                        const SizedBox(width: 4),
                        Semantics(
                          label: 'Déjà vu',
                          child: Icon(
                            Icons.check,
                            size: 16,
                            color: colors.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 4),
                  // Metadata subtitle
                  Text(
                    [
                      video.channelTitle,
                      if (video.kind == 'video' && video.viewCount != null)
                        '${compactCount(video.viewCount!)} vues',
                      if (video.kind != 'video') video.kind,
                    ].where((s) => s.isNotEmpty).join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: textTheme.bodySmall?.copyWith(
                      color: colors.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );

    if (!showActionsButton) return tile;
    return _TvTileRow(
      tile: tile,
      onActions: () => showActions(context, ref),
    );
  }

  /// Live placeholder: platform-colored block with a centered "EN DIRECT",
  /// so a live stream without a thumbnail reads as live at a glance
  /// instead of a gray play icon.
  Widget _livePlaceholder(ColorScheme colors) {
    final color = switch (video.platform) {
      'twitch' => kTwitchPurple,
      'tiktok' => kTikTokRed,
      _ => kBrandRed,
    };
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            color.withValues(alpha: 0.85),
            color.withValues(alpha: 0.55),
          ],
        ),
      ),
      child: Center(
        child: Text(
          'EN DIRECT',
          style: TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
            fontSize: 12,
            letterSpacing: 0.8,
          ),
        ),
      ),
    );
  }

  /// Placeholder with kind icon centered on surfaceContainerHigh.
  Widget _placeholder(ColorScheme colors) {
    return Container(
      color: colors.surfaceContainerHigh,
      child: Center(
        child: Icon(
          switch (video.kind) {
            'channel' => Icons.person,
            'playlist' => Icons.playlist_play,
            _ => Icons.play_circle_outline,
          },
          color: colors.onSurfaceVariant,
          size: 32,
        ),
      ),
    );
  }

  /// Circular placeholder for channel avatars with tinted background.
  Widget _channelPlaceholder(ColorScheme colors) {
    return Container(
      decoration: BoxDecoration(
        color: colors.primaryContainer,
        shape: BoxShape.circle,
      ),
      child: Center(
        child: Icon(
          Icons.person,
          color: colors.onPrimaryContainer,
          size: 32,
        ),
      ),
    );
  }

  void _defaultTap(BuildContext context, WidgetRef ref) {
    switch (video.kind) {
      case 'channel':
        context.push(
            '/channel/${Uri.encodeComponent(video.videoId)}?platform=${video.platform}'
            '&title=${Uri.encodeComponent(video.title)}');
      case 'playlist':
        context.push('/ytplaylist/${Uri.encodeComponent(video.videoId)}'
            '?platform=${video.platform}');
      default:
        openPlayer(context, ref, [video]);
    }
  }

  /// The actions sheet (queue, details, save, open/follow channel\u2026). Public
  /// so other item widgets (the music shelves' cards) offer the same sheet.
  Future<void> showActions(BuildContext context, WidgetRef ref) async {
    // Flat-extracted playlist videos carry no channel id on the server: ask
    // the details endpoint for it so "Open/Follow channel" stay available
    // (graceful fallback to the short sheet on error).
    var channelId = video.channelId;
    if (video.kind == 'video' &&
        channelId.isEmpty &&
        video.channelTitle.isNotEmpty) {
      try {
        final resolved = await ref
            .read(resolvedChannelProvider(
                    (video.platform, video.videoId))
                .future);
        if (resolved != null) channelId = resolved;
      } catch (_) {
        // Leave the channel items out of the sheet.
      }
    }
    if (!context.mounted) return;
    // A bottom sheet does not take focus on its own: on TV the first entry
    // grabs it so the remote can act without a blind D-pad down.
    final tvFocus = ref.read(isTvProvider);
    showModalBottomSheet<void>(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(kRadiusLg)),
      ),
      builder: (sheetContext) {
        final sheetColors = Theme.of(sheetContext).colorScheme;
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Grab handle
              Padding(
                padding: const EdgeInsets.only(top: 12, bottom: 8),
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: sheetColors.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              if (video.kind == 'video') ...[
                _sheetTile(
                  sheetContext,
                  icon: Icons.queue,
                  label: 'Ajouter à la file',
                  autofocus: tvFocus,
                  onTap: () {
                    ref.read(queueProvider.notifier).enqueue(video);
                    Navigator.pop(sheetContext);
                  },
                ),
                _sheetTile(
                  sheetContext,
                  icon: Icons.info_outline,
                  label: 'Détails',
                  onTap: () {
                    Navigator.pop(sheetContext);
                    context.push('/detail/${Uri.encodeComponent(video.videoId)}'
                        '?platform=${video.platform}');
                  },
                ),
                _sheetTile(
                  sheetContext,
                  icon: Icons.playlist_add,
                  label: 'Enregistrer dans une playlist',
                  onTap: () {
                    Navigator.pop(sheetContext);
                    showSaveToPlaylistSheet(context, ref, video);
                  },
                ),
              ],
              if (video.kind == 'playlist')
                _sheetTile(
                  sheetContext,
                  icon: Icons.library_add,
                  label: 'Importer toutes les vidéos',
                  autofocus: tvFocus,
                  onTap: () {
                    Navigator.pop(sheetContext);
                    showImportPlaylistSheet(
                      context,
                      ref,
                      source: video.videoId,
                      platform: video.platform,
                      defaultName: video.title,
                    );
                  },
                ),
              if (video.kind != 'channel' && channelId.isNotEmpty)
                _sheetTile(
                  sheetContext,
                  icon: Icons.account_circle,
                  label: 'Ouvrir la chaîne',
                  onTap: () {
                    Navigator.pop(sheetContext);
                    context.push(
                        '/channel/${Uri.encodeComponent(channelId)}'
                        '?platform=${video.platform}'
                        '&title=${Uri.encodeComponent(video.channelTitle)}');
                  },
                ),
              if (onRemove != null)
                _sheetTile(
                  sheetContext,
                  icon: Icons.delete_outline,
                  label: removeLabel,
                  onTap: () {
                    Navigator.pop(sheetContext);
                    onRemove!();
                  },
                ),
              if (channelId.isNotEmpty)
                _sheetTile(
                  sheetContext,
                  icon: Icons.person_add,
                  label: 'Suivre la chaîne',
                  onTap: () async {
                    Navigator.pop(sheetContext);
                    final messenger = ScaffoldMessenger.of(context);
                    final ref_ = switch (video.platform) {
                      'bitchute' => 'bitchute:$channelId',
                      'odysee' => 'odysee:$channelId',
                      'crowdbunker' => 'crowdbunker:$channelId',
                      _ => channelId,
                    };
                    try {
                      await ref.read(apiProvider).followChannel(ref_);
                      messenger.showSnackBar(
                          SnackBar(content: Text('Chaîne suivie : ${video.channelTitle}')));
                    } catch (e) {
                      messenger.showSnackBar(SnackBar(content: Text('$e')));
                    }
                  },
                ),
              const SizedBox(height: 8),
            ],
          ),
        );
      },
    );
  }
}

/// Rounded list item for the bottom sheets.
Widget _sheetTile(
  BuildContext context, {
  required IconData icon,
  required String label,
  required VoidCallback onTap,
  bool autofocus = false,
}) {
  return Padding(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
    child: Material(
      color: Colors.transparent,
      borderRadius: BorderRadius.circular(kRadiusSm),
      child: InkWell(
        onTap: onTap,
        autofocus: autofocus,
        borderRadius: BorderRadius.circular(kRadiusSm),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Row(
            children: [
              Icon(icon, size: 24),
              const SizedBox(width: 16),
              Expanded(child: Text(label)),
            ],
          ),
        ),
      ),
    ),
  );
}

/// "Save to a local playlist" sheet for [video]: existing playlists plus
/// "Nouvelle playlist…". Shared by the tiles' actions sheet and the player.
Future<void> showSaveToPlaylistSheet(
    BuildContext context, WidgetRef ref, Video video) async {
  final messenger = ScaffoldMessenger.of(context);
  final List<LocalPlaylist> playlists;
  try {
    playlists = await ref.read(apiProvider).playlists();
  } on ApiException catch (e) {
    messenger.showSnackBar(SnackBar(content: Text(e.toString())));
    return;
  }
  if (!context.mounted) return;
  // A bottom sheet does not take focus on its own: on TV the first entry
  // grabs it so the remote can act without a blind D-pad down.
  final tvFocus = ref.read(isTvProvider);
  showModalBottomSheet<void>(
    context: context,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(kRadiusLg)),
    ),
    builder: (sheetContext) {
      final sheetColors = Theme.of(sheetContext).colorScheme;
      return SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Grab handle
            Padding(
              padding: const EdgeInsets.only(top: 12, bottom: 8),
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: sheetColors.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            if (playlists.isEmpty)
              Padding(
                padding: const EdgeInsets.all(kGutter),
                child: Text(
                  'Aucune playlist — créez-en une d’abord',
                  style: Theme.of(sheetContext).textTheme.bodyMedium?.copyWith(
                        color: sheetColors.onSurfaceVariant,
                      ),
                ),
              )
            else
              for (final (i, p) in playlists.indexed)
                _sheetTile(
                  sheetContext,
                  icon: Icons.playlist_play,
                  label: '${p.name} · ${pluralize(p.count, 'élément')}',
                  autofocus: tvFocus && i == 0,
                  onTap: () async {
                    Navigator.pop(sheetContext);
                    try {
                      final added = await ref
                          .read(apiProvider)
                          .addPlaylistItem(p.id, video);
                      messenger.showSnackBar(SnackBar(
                          content: Text(added
                              ? 'Ajouté à ${p.name}'
                              : 'Déjà dans ${p.name}')));
                    } on ApiException catch (e) {
                      messenger.showSnackBar(
                          SnackBar(content: Text(e.toString())));
                    }
                  },
                ),
            _sheetTile(
              sheetContext,
              icon: Icons.playlist_add,
              label: 'Nouvelle playlist…',
              autofocus: tvFocus && playlists.isEmpty,
              onTap: () async {
                Navigator.pop(sheetContext);
                final name = await promptNewPlaylistName(context);
                if (name == null || !context.mounted) return;
                final api = ref.read(apiProvider);
                try {
                  final created = await api.createPlaylist(name);
                  if (created == null) {
                    messenger.showSnackBar(const SnackBar(
                        content: Text('Ce nom est déjà utilisé')));
                    return;
                  }
                  final added =
                      await api.addPlaylistItem(created.id, video);
                  ref.invalidate(playlistsProvider);
                  messenger.showSnackBar(SnackBar(
                      content: Text(added
                          ? 'Ajouté à ${created.name}'
                          : 'Playlist « ${created.name} » créée')));
                } on ApiException catch (e) {
                  messenger.showSnackBar(SnackBar(
                      content: Text('Échec de la création : ${e.detail}')));
                }
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      );
    },
  );
}

/// Mounted TV rows, so a row's ⋮ button can hand vertical D-pad focus to the
/// neighbouring row's tile: the reading-order policy alone sticks to the ⋮
/// column (a DOWN pressed on a button focuses the next button, never the
/// video content). Neighbours are found by position (see _focusSiblingTile).
final List<_TvTileRowState> _tvTileRows = <_TvTileRowState>[];

/// Intercepts vertical [DirectionalFocusIntent]s issued from a row's ⋮
/// button and moves focus to the tile of the previous/next row instead of
/// the next ⋮ button.
class _ButtonRowRedirectAction extends DirectionalFocusAction {
  _ButtonRowRedirectAction({required this.moveUp, required this.moveDown});

  final bool Function() moveUp;
  final bool Function() moveDown;

  @override
  void invoke(DirectionalFocusIntent intent) {
    if (intent.direction == TraversalDirection.down && moveDown()) return;
    if (intent.direction == TraversalDirection.up && moveUp()) return;
    super.invoke(intent);
  }
}

/// TV layout for a [VideoTile]: the tile plus its ⋮ actions button, with
/// D-pad vertical focus redirected from the button to the tile of the
/// adjacent row.
class _TvTileRow extends StatefulWidget {
  const _TvTileRow({required this.tile, required this.onActions});

  final Widget tile;
  final VoidCallback onActions;

  @override
  State<_TvTileRow> createState() => _TvTileRowState();
}

class _TvTileRowState extends State<_TvTileRow> {
  /// Non-focusable probe above the tile. The tile's real focus node stays
  /// the InkWell's (unchanged visuals/activation); the probe only gives the
  /// row a stable handle to find that node in the focus tree.
  final FocusNode _tileProbe = FocusNode(canRequestFocus: false);

  FocusNode? get _tileNode {
    for (final d in _tileProbe.descendants) {
      if (d.canRequestFocus) return d;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _tvTileRows.add(this);
  }

  @override
  void dispose() {
    _tvTileRows.remove(this);
    _tileProbe.dispose();
    super.dispose();
  }

  /// Focuses the tile of the nearest row below/above this one, if any.
  ///
  /// Chosen by on-screen position, not registration order: a lazy list
  /// registers the rows built while scrolling back up AFTER the others. And
  /// only among rows of the same list on the same screen: the registry is
  /// global, and the screen underneath (the home feed under a channel) keeps
  /// its rows mounted.
  bool _focusSiblingTile(TraversalDirection direction) {
    final mine = context.findRenderObject();
    if (mine is! RenderBox || !mine.attached) return false;
    final route = ModalRoute.of(context);
    final origin = mine.localToGlobal(Offset.zero);
    final down = direction == TraversalDirection.down;
    FocusNode? best;
    double? bestDistance;
    for (final row in _tvTileRows) {
      if (identical(row, this) || !row.mounted) continue;
      if (ModalRoute.of(row.context) != route) continue;
      final box = row.context.findRenderObject();
      if (box is! RenderBox || !box.attached) continue;
      final offset = box.localToGlobal(Offset.zero);
      if ((offset.dx - origin.dx).abs() > 1) continue; // another column/tab
      final dy = offset.dy - origin.dy;
      if (down ? dy <= 0.5 : dy >= -0.5) continue;
      if (bestDistance != null && dy.abs() >= bestDistance) continue;
      final node = row._tileNode;
      if (node == null) continue;
      best = node;
      bestDistance = dy.abs();
    }
    if (best == null) return false;
    best.requestFocus();
    // requestFocus does not scroll (directional traversal would have): bring
    // a row sitting in the list's cache area into view.
    final target = best.context;
    if (target != null) {
      unawaited(Scrollable.ensureVisible(
        target,
        duration: const Duration(milliseconds: 150),
        alignmentPolicy: down
            ? ScrollPositionAlignmentPolicy.keepVisibleAtEnd
            : ScrollPositionAlignmentPolicy.keepVisibleAtStart,
      ));
    }
    return true;
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(child: Focus(focusNode: _tileProbe, child: widget.tile)),
        Padding(
          padding: const EdgeInsets.only(right: kGutter / 2, top: 8),
          child: Actions(
            actions: {
              DirectionalFocusIntent: _ButtonRowRedirectAction(
                moveDown: () => _focusSiblingTile(TraversalDirection.down),
                moveUp: () => _focusSiblingTile(TraversalDirection.up),
              ),
            },
            child: IconButton(
              icon: const Icon(Icons.more_vert),
              tooltip: 'Actions',
              onPressed: widget.onActions,
            ),
          ),
        ),
      ],
    );
  }
}
