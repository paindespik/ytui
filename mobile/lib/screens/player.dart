/// Integrated player: resolves streams just before playback (URLs expire),
/// plays through media_kit, records history and heartbeats the position.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart' as mkv;

import '../api/client.dart';
import '../api/models.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/responsive.dart';

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

  @override
  void initState() {
    super.initState();
    _completedSub = player.stream.completed.where((done) => done).listen((_) {
      final queue = ref.read(queueProvider);
      if (queue.hasNext) {
        ref.read(queueProvider.notifier).next();
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
    _heartbeat = Timer.periodic(const Duration(seconds: 10), (_) => _savePosition());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final video = ref.read(queueProvider).current;
      if (video != null) _load(video);
    });
  }

  Future<void> _load(Video video, {bool resume = true}) async {
    _loadedVideoId = video.videoId;
    setState(() => _error = null);
    final api = ref.read(apiProvider);
    try {
      // Record the watch and fetch resume position + fresh stream URLs.
      unawaited(api.recordWatch(video).catchError((_) {}));
      ref.read(watchedIdsProvider.notifier).markWatched(video.videoId);
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
                : mkv.Video(controller: controller),
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
                          child: IconButton(
                            iconSize: 32,
                            icon: Icon(
                              Icons.play_arrow_rounded,
                              color: colors.onPrimaryContainer,
                            ),
                            tooltip: 'Play/Pause',
                            onPressed: () => player.playOrPause(),
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

                  // Queue list
                  if (queue.items.length > 1)
                    Expanded(
                      child: Container(
                        margin: const EdgeInsets.only(top: 8),
                        child: ListView.builder(
                          padding: const EdgeInsets.symmetric(horizontal: 8),
                          itemCount: queue.items.length,
                          itemBuilder: (context, i) {
                            final item = queue.items[i];
                            final isActive = i == queue.index;

                            return Container(
                              margin: const EdgeInsets.only(bottom: 4),
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
                                    .play(queue.items, startIndex: i),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 12,
                                    vertical: 10,
                                  ),
                                  child: Row(
                                    children: [
                                      // Index or playing indicator
                                      SizedBox(
                                        width: 24,
                                        child: isActive
                                            ? Icon(
                                                Icons.play_arrow_rounded,
                                                size: 18,
                                                color: colors.primary,
                                              )
                                            : Text(
                                                '${i + 1}',
                                                style:
                                                    theme.textTheme.labelMedium?.copyWith(
                                                  color: colors.onSurfaceVariant,
                                                ),
                                                textAlign: TextAlign.center,
                                              ),
                                      ),
                                      const SizedBox(width: 12),
                                      // Title
                                      Expanded(
                                        child: Text(
                                          item.title,
                                          style: theme.textTheme.bodyMedium?.copyWith(
                                            color: isActive
                                                ? colors.primary
                                                : colors.onSurface,
                                            fontWeight:
                                                isActive ? FontWeight.w600 : null,
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
                          },
                        ),
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
}
