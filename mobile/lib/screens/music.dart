/// "Musique" home tab: the account's YouTube Music home, shelf by shelf
/// ("Sélection rapide", "Mixés pour vous", "Albums pour vous"…), each shelf a
/// horizontal carousel like on music.youtube.com.
library;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../state/settings.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/open_player.dart';
import '../widgets/video_tile.dart';

/// Square cover art (track, album, mix) rather than a 16:9 video frame: only
/// video thumbnails come from i.ytimg.com.
bool isCoverArt(Video video) =>
    video.kind != 'channel' &&
    video.thumbnailUrl.isNotEmpty &&
    !RegExp(r'(^|[/.])ytimg\.com/').hasMatch(video.thumbnailUrl);

class MusicTab extends ConsumerWidget {
  const MusicTab({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final music = ref.watch(musicProvider);
    return music.when(
      loading: () => const AppLoading(),
      error: (e, _) =>
          AppError.from(e, onRetry: () => ref.invalidate(musicProvider)),
      data: (home) {
        final headers = <Widget>[
          for (final warning in home.warnings) _Notice(warning),
          if (home.shelves.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 64),
              child: AppEmpty(
                icon: Icons.library_music_outlined,
                message: home.warnings.isEmpty
                    ? "YouTube Music n'a rien renvoyé."
                    : 'Les recommandations musicales ont besoin du compte '
                        'YouTube (ytui auth cookies).',
              ),
            ),
        ];
        return RefreshIndicator(
          onRefresh: () => ref.read(musicProvider.notifier).refresh(),
          // Shelves are built lazily: 16 carousels of cover art is a lot of
          // decoding for the projector's SoC.
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(vertical: 8),
            physics: const AlwaysScrollableScrollPhysics(),
            itemCount: headers.length + home.shelves.length,
            itemBuilder: (context, i) => i < headers.length
                ? headers[i]
                : MusicShelfView(shelf: home.shelves[i - headers.length]),
          ),
        );
      },
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: kGutter, vertical: 4),
      child: Material(
        color: colors.errorContainer,
        borderRadius: BorderRadius.circular(kRadiusMd),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Icon(Icons.warning_amber_rounded, color: colors.onErrorContainer),
              const SizedBox(width: 12),
              Expanded(
                child: Text(text,
                    style: TextStyle(color: colors.onErrorContainer)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// One shelf: strapline + title, then its items in a horizontal carousel.
class MusicShelfView extends ConsumerWidget {
  const MusicShelfView({super.key, required this.shelf});

  final MusicShelf shelf;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final textTheme = Theme.of(context).textTheme;
    final colors = Theme.of(context).colorScheme;
    // Read from a couch across the room: bigger cards on TV.
    final scale = ref.watch(isTvProvider) ? 1.3 : 1.0;
    final tracks = shelf.items.where((v) => v.kind == 'video').toList();
    // A horizontal list needs a fixed height: the tallest thumbnail plus
    // two title lines and a subtitle line.
    final thumbHeight = shelf.items
        .map((v) => _MusicCard.thumbSize(v, scale).height)
        .fold<double>(0, (a, b) => a > b ? a : b);
    final rowHeight =
        thumbHeight + MediaQuery.textScalerOf(context).scale(70);

    return Padding(
      padding: const EdgeInsets.only(top: 12, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: kGutter),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (shelf.strapline.isNotEmpty)
                  Text(
                    shelf.strapline.toUpperCase(),
                    style: textTheme.labelSmall?.copyWith(
                      color: colors.onSurfaceVariant,
                      letterSpacing: 0.8,
                    ),
                  ),
                Text(
                  shelf.title,
                  style: textTheme.titleLarge
                      ?.copyWith(fontWeight: FontWeight.bold),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          SizedBox(
            height: rowHeight,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: kGutter - 4),
              itemCount: shelf.items.length,
              separatorBuilder: (_, __) => const SizedBox(width: 4),
              itemBuilder: (context, i) {
                final video = shelf.items[i];
                return _MusicCard(
                  video: video,
                  scale: scale,
                  onTap: () => _open(context, ref, video, tracks),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  void _open(
      BuildContext context, WidgetRef ref, Video video, List<Video> tracks) {
    switch (video.kind) {
      case 'channel':
        context.push(
            '/channel/${Uri.encodeComponent(video.videoId)}?platform=${video.platform}'
            '&title=${Uri.encodeComponent(video.title)}');
      case 'playlist':
        // Albums and mixes open their track list (listed with the account,
        // so personal mixes are the account's own).
        context.push('/ytplaylist/${Uri.encodeComponent(video.videoId)}'
            '?platform=${video.platform}');
      default:
        // Like the site, playback carries on with the rest of the shelf.
        openPlayer(context, ref, tracks, startIndex: tracks.indexOf(video));
    }
  }
}

class _MusicCard extends ConsumerWidget {
  const _MusicCard({
    required this.video,
    required this.scale,
    required this.onTap,
  });

  final Video video;
  final double scale;
  final VoidCallback onTap;

  /// Cover art and artists are square (artists drawn round), clips 16:9.
  static Size thumbSize(Video video, double scale) {
    if (video.kind == 'channel' || isCoverArt(video)) {
      return Size(150 * scale, 150 * scale);
    }
    return Size(240 * scale, 135 * scale);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;
    final watched = ref
            .watch(watchedIdsProvider)
            .valueOrNull
            ?.contains('${video.platform}:${video.videoId}') ??
        false;
    final size = thumbSize(video, scale);
    final isArtist = video.kind == 'channel';
    final dpr = MediaQuery.devicePixelRatioOf(context);

    Widget image = video.thumbnailUrl.isEmpty
        ? _placeholder(colors)
        : CachedNetworkImage(
            imageUrl: video.thumbnailUrl,
            fit: BoxFit.cover,
            // Decode at display size: the covers are served at 544 px.
            memCacheWidth: (size.width * dpr).round(),
            errorWidget: (_, __, ___) => _placeholder(colors),
          );
    image = isArtist
        ? ClipOval(child: image)
        : ClipRRect(
            borderRadius: BorderRadius.circular(kRadiusSm),
            child: Stack(
              fit: StackFit.expand,
              children: [
                image,
                if (video.kind == 'video' && video.durationLabel.isNotEmpty)
                  Positioned(
                    bottom: 4,
                    right: 4,
                    child: _Pill(video.durationLabel),
                  ),
                if (video.kind == 'playlist')
                  const Positioned(
                    bottom: 4,
                    right: 4,
                    child: _Pill('', icon: Icons.playlist_play),
                  ),
              ],
            ),
          );

    final subtitle = isArtist ? 'Artiste' : video.channelTitle;
    return SizedBox(
      width: size.width + 8,
      child: InkWell(
        onTap: onTap,
        onLongPress: isArtist
            ? null
            : () => VideoTile(video: video).showActions(context, ref),
        borderRadius: BorderRadius.circular(kRadiusMd),
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Column(
            crossAxisAlignment:
                isArtist ? CrossAxisAlignment.center : CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: size.width,
                height: size.height,
                child: Opacity(opacity: watched ? 0.55 : 1, child: image),
              ),
              const SizedBox(height: 6),
              Text(
                video.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                textAlign: isArtist ? TextAlign.center : TextAlign.start,
                style: textTheme.bodyMedium?.copyWith(
                  fontWeight: FontWeight.w600,
                  color: watched ? colors.onSurfaceVariant : null,
                ),
              ),
              if (subtitle.isNotEmpty)
                Text(
                  subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: textTheme.bodySmall
                      ?.copyWith(color: colors.onSurfaceVariant),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _placeholder(ColorScheme colors) => Container(
        color: colors.surfaceContainerHigh,
        child: Center(
          child: Icon(
            switch (video.kind) {
              'channel' => Icons.person,
              'playlist' => Icons.queue_music,
              _ => Icons.music_note,
            },
            color: colors.onSurfaceVariant,
            size: 32,
          ),
        ),
      );
}

class _Pill extends StatelessWidget {
  const _Pill(this.text, {this.icon});

  final String text;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.75),
        borderRadius: BorderRadius.circular(4),
      ),
      child: icon != null
          ? Icon(icon, size: 14, color: Colors.white)
          : Text(
              text,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: Colors.white,
                    fontWeight: FontWeight.w500,
                    height: 1,
                  ),
            ),
    );
  }
}
