/// Integrated player: resolves streams just before playback (URLs expire),
/// plays through media_kit, records history and heartbeats the position.
/// Shows YouTube suggestions below the video, autoplays the next one, and
/// keeps playing in the background (screen off) via a foreground service.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart' as mkv;

import '../api/client.dart';
import '../api/models.dart';
import '../services/background_playback.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

/// Maps raw playback errors to user-friendly French messages.
String _friendlyError(String raw) {
  final lower = raw.toLowerCase();
  if (lower.contains('auth') || lower.contains('403')) {
    return 'Authentification requise pour cette vidéo.';
  }
  if (lower.contains('not found') || lower.contains('404')) {
    return 'Vidéo introuvable ou supprimée.';
  }
  if (lower.contains('unavailable') || lower.contains('private')) {
    return 'Cette vidéo n\'est pas disponible.';
  }
  if (lower.contains('network') || lower.contains('connection')) {
    return 'Erreur réseau — vérifiez votre connexion.';
  }
  // Default fallback for expired streams / unknown libmpv errors
  return 'Impossible de lire cette vidéo — le flux a peut-être expiré.';
}

class PlayerScreen extends ConsumerStatefulWidget {
  const PlayerScreen({super.key});

  @override
  ConsumerState<PlayerScreen> createState() => _PlayerScreenState();
}

class _PlayerScreenState extends ConsumerState<PlayerScreen> {
  late final Player player = Player();
  late final mkv.VideoController controller = mkv.VideoController(player);

  String? _loadedVideoId;
  bool _retried = false;
  String? _error;
  Timer? _heartbeat;
  StreamSubscription<void>? _completedSub;
  StreamSubscription<String>? _errorSub;
  StreamSubscription<bool>? _playingSub;

  @override
  void initState() {
    super.initState();
    _completedSub = player.stream.completed.where((done) => done).listen((_) {
      final queue = ref.read(queueProvider);
      if (queue.hasNext) {
        ref.read(queueProvider.notifier).next();
      } else {
        // End of queue: chain into the first suggestion ("À suivre").
        _autoplayNext();
      }
    });
    _errorSub = player.stream.error.listen((message) async {
      // Expired/broken stream URL: re-resolve once, then surface the error.
      if (_retried) {
        if (mounted) setState(() => _error = message);
        return;
      }
      _retried = true;
      final video = ref.read(queueProvider).current;
      if (video != null) _load(video, resume: false);
    });
    // Keep the background notification in sync with play/pause state.
    _playingSub = player.stream.playing.listen((playing) {
      final video = ref.read(queueProvider).current;
      if (video == null) return;
      updatePlaybackNotification(
        title: video.title,
        text: playing ? '▶ ${video.channelTitle}' : '⏸ ${video.channelTitle}',
      );
    });
    _heartbeat = Timer.periodic(const Duration(seconds: 10), (_) => _savePosition());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final video = ref.read(queueProvider).current;
      if (video != null) _load(video);
    });
  }

  /// Plays the first suggestion for the current video (autoplay at end of queue).
  Future<void> _autoplayNext() async {
    final video = ref.read(queueProvider).current;
    if (video == null) return;
    try {
      final related =
          await ref.read(relatedProvider((video.videoId, video.platform)).future);
      if (!mounted || related.isEmpty) return;
      ref.read(queueProvider.notifier).playAppend(related.first);
    } catch (_) {}
  }

  Future<void> _load(Video video, {bool resume = true}) async {
    _loadedVideoId = video.videoId;
    setState(() => _error = null);
    final api = ref.read(apiProvider);
    try {
      // Record the watch and fetch resume position + fresh stream URLs.
      unawaited(api.recordWatch(video).catchError((_) {}));
      ref.read(watchedIdsProvider.notifier).markWatched(video.videoId);
      // Keep playing when the screen turns off (foreground service + wakelock).
      unawaited(startPlaybackService(
        title: video.title,
        text: '▶ ${video.channelTitle}',
      ).catchError((_) {}));
      double start = 0;
      if (resume) {
        final info = await api.resume(video.videoId).catchError((_) => null);
        if (info != null) start = resumeStart(info.position, info.duration);
      }
      final streams =
          await api.videoStreams(video.videoId, platform: video.platform);
      if (!mounted || _loadedVideoId != video.videoId) return;
      final isSplit = streams.kind == 'split' && streams.audioUrl != null;
      final media = Media(isSplit ? (streams.videoUrl ?? streams.url) : streams.url,
          start: Duration(seconds: start.toInt()));
      await player.open(media);
      if (isSplit) {
        // DASH: separate video/audio URLs — attach the audio as an external track.
        await player.setAudioTrack(AudioTrack.uri(streams.audioUrl!));
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  Future<void> _savePosition() async {
    final video = ref.read(queueProvider).current;
    if (video == null) return;
    final pos = player.state.position;
    final dur = player.state.duration;
    if (dur.inSeconds == 0) return;
    try {
      await ref.read(apiProvider).savePosition(
            video.videoId,
            pos.inSeconds.toDouble(),
            duration: dur.inSeconds.toDouble(),
          );
    } catch (_) {}
  }

  @override
  void dispose() {
    _heartbeat?.cancel();
    _completedSub?.cancel();
    _errorSub?.cancel();
    _playingSub?.cancel();
    unawaited(stopPlaybackService());
    player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final queue = ref.watch(queueProvider);
    final video = queue.current;
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    ref.listen(queueProvider, (previous, next) {
      final current = next.current;
      if (current != null && current.videoId != _loadedVideoId) {
        _retried = false;
        _load(current);
      }
    });

    if (video == null) {
      return Scaffold(
        body: Center(
          child: Text(
            'Nothing to play',
            style: theme.textTheme.bodyLarge?.copyWith(
              color: colors.onSurfaceVariant,
            ),
          ),
        ),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(
          video.title,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: Column(
        children: [
          // Video player area
          AspectRatio(
            aspectRatio: 16 / 9,
            child: _error != null
                ? Container(
                    color: colors.surfaceContainerHighest,
                    alignment: Alignment.center,
                    padding: const EdgeInsets.all(kGutter),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.error_outline,
                          size: 32,
                          color: colors.error,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          _friendlyError(_error!),
                          textAlign: TextAlign.center,
                          style: theme.textTheme.titleSmall?.copyWith(
                            color: colors.error,
                          ),
                        ),
                        const SizedBox(height: 12),
                        FilledButton.icon(
                          onPressed: () {
                            final current = ref.read(queueProvider).current;
                            if (current != null) {
                              setState(() {
                                _retried = false;
                                _error = null;
                              });
                              _load(current, resume: false);
                            }
                          },
                          icon: const Icon(Icons.refresh, size: 18),
                          label: const Text('Réessayer'),
                        ),
                      ],
                    ),
                  )
                : mkv.Video(
                    controller: controller,
                    // Keep playing when the screen turns off; the foreground
                    // service (background_playback.dart) holds the process alive.
                    pauseUponEnteringBackgroundMode: false,
                  ),
          ),

          // Now playing info + controls + queue — centered on wide screens
          Expanded(
            child: ResponsiveCenter(
              child: Column(
                children: [
                  // Now playing info
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: kGutter,
                      vertical: 12,
                    ),
                    decoration: BoxDecoration(
                      color: colors.surfaceContainer,
                      border: Border(
                        bottom: BorderSide(
                          color: colors.outlineVariant.withValues(alpha: 0.3),
                          width: 1,
                        ),
                      ),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                video.title,
                                style: theme.textTheme.titleSmall?.copyWith(
                                  fontWeight: FontWeight.w600,
                                ),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                              const SizedBox(height: 2),
                              Text(
                                video.channelTitle,
                                style: theme.textTheme.bodySmall?.copyWith(
                                  color: colors.onSurfaceVariant,
                                ),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),

                  // Playback controls
                  Container(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    decoration: BoxDecoration(
                      color: colors.surfaceContainerLow,
                      borderRadius: const BorderRadius.vertical(
                        bottom: Radius.circular(kRadiusMd),
                      ),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        IconButton(
                          icon: Icon(
                            Icons.skip_previous_rounded,
                            color: queue.hasPrevious
                                ? colors.onSurface
                                : colors.onSurfaceVariant.withValues(alpha: 0.4),
                          ),
                          tooltip: 'Previous',
                          onPressed: queue.hasPrevious
                              ? () => ref.read(queueProvider.notifier).previous()
                              : null,
                        ),
                        const SizedBox(width: 8),
                        Container(
                          decoration: BoxDecoration(
                            color: colors.primaryContainer,
                            shape: BoxShape.circle,
                          ),
                          child: StreamBuilder<bool>(
                            stream: player.stream.playing,
                            initialData: player.state.playing,
                            builder: (context, snapshot) {
                              final playing = snapshot.data ?? false;
                              return IconButton(
                                iconSize: 32,
                                icon: Icon(
                                  playing
                                      ? Icons.pause_rounded
                                      : Icons.play_arrow_rounded,
                                  color: colors.onPrimaryContainer,
                                ),
                                tooltip: playing ? 'Pause' : 'Lecture',
                                onPressed: () => player.playOrPause(),
                              );
                            },
                          ),
                        ),
                        const SizedBox(width: 8),
                        IconButton(
                          icon: Icon(
                            Icons.skip_next_rounded,
                            color: queue.hasNext
                                ? colors.onSurface
                                : colors.onSurfaceVariant.withValues(alpha: 0.4),
                          ),
                          tooltip: 'Next',
                          onPressed: queue.hasNext
                              ? () => ref.read(queueProvider.notifier).next()
                              : null,
                        ),
                      ],
                    ),
                  ),

                  // Queue + YouTube suggestions (scrollable)
                  Expanded(
                    child: ListView(
                      padding: const EdgeInsets.only(top: 8, bottom: 16),
                      children: [
                        if (queue.items.length > 1) ...[
                          _sectionHeader(theme, colors, 'File d\'attente'),
                          for (var i = 0; i < queue.items.length; i++)
                            _queueTile(theme, colors, queue.items[i], i,
                                i == queue.index),
                        ],
                        _SuggestionsSection(video: video),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _sectionHeader(ThemeData theme, ColorScheme colors, String label) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kGutter, 12, kGutter, 6),
      child: Text(
        label,
        style: theme.textTheme.titleSmall?.copyWith(
          fontWeight: FontWeight.w600,
          color: colors.onSurfaceVariant,
        ),
      ),
    );
  }

  Widget _queueTile(ThemeData theme, ColorScheme colors, Video item, int i,
      bool isActive) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: isActive
            ? colors.primaryContainer.withValues(alpha: 0.3)
            : colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(kRadiusSm),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(kRadiusSm),
        onTap: () => ref
            .read(queueProvider.notifier)
            .play(ref.read(queueProvider).items, startIndex: i),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(
            children: [
              SizedBox(
                width: 24,
                child: isActive
                    ? Icon(Icons.play_arrow_rounded,
                        size: 18, color: colors.primary)
                    : Text(
                        '${i + 1}',
                        style: theme.textTheme.labelMedium?.copyWith(
                          color: colors.onSurfaceVariant,
                        ),
                        textAlign: TextAlign.center,
                      ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  item.title,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: isActive ? colors.primary : colors.onSurface,
                    fontWeight: isActive ? FontWeight.w600 : null,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// YouTube suggestions under the video: the first one is flagged "À suivre"
/// (played automatically at the end of the queue); tapping any plays it next.
class _SuggestionsSection extends ConsumerWidget {
  final Video video;

  const _SuggestionsSection({required this.video});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final async = ref.watch(relatedProvider((video.videoId, video.platform)));

    return async.when(
      loading: () => const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (_, __) => Padding(
        padding: const EdgeInsets.all(kGutter),
        child: Text(
          'Suggestions indisponibles.',
          style: theme.textTheme.bodySmall?.copyWith(
            color: colors.onSurfaceVariant,
          ),
        ),
      ),
      data: (items) {
        if (items.isEmpty) return const SizedBox.shrink();
        final upNext = items.first;
        final rest = items.skip(1);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(kGutter, 12, kGutter, 6),
              child: Text(
                'À suivre',
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w600,
                  color: colors.primary,
                ),
              ),
            ),
            VideoTile(
              video: upNext,
              onTap: () => ref.read(queueProvider.notifier).playAppend(upNext),
            ),
            if (rest.isNotEmpty) ...[
              Padding(
                padding: const EdgeInsets.fromLTRB(kGutter, 10, kGutter, 6),
                child: Text(
                  'Suggestions',
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: colors.onSurfaceVariant,
                  ),
                ),
              ),
              for (final item in rest)
                VideoTile(
                  video: item,
                  onTap: () =>
                      ref.read(queueProvider.notifier).playAppend(item),
                ),
            ],
          ],
        );
      },
    );
  }
}
