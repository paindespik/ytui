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
    if (video == null || _loadedVideoId != video.videoId) return;
    final position = player.state.position.inSeconds.toDouble();
    final duration = player.state.duration.inSeconds.toDouble();
    if (position <= 0) return;
    try {
      await ref.read(apiProvider).savePosition(video.videoId, position,
          duration: duration > 0 ? duration : null);
    } on ApiException {
      // best-effort heartbeat
    }
  }

  @override
  void dispose() {
    _heartbeat?.cancel();
    _completedSub?.cancel();
    _errorSub?.cancel();
    _savePosition();
    player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final queue = ref.watch(queueProvider);
    final video = queue.current;

    ref.listen(queueProvider, (previous, next) {
      final current = next.current;
      if (current != null && current.videoId != _loadedVideoId) {
        _retried = false;
        _load(current);
      }
    });

    if (video == null) {
      return const Scaffold(body: Center(child: Text('Nothing to play')));
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(video.title, maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
      body: Column(
        children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: _error != null
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text('Playback failed: $_error',
                          textAlign: TextAlign.center),
                    ),
                  )
                : mkv.Video(controller: controller),
          ),
          ListTile(
            title: Text(video.title),
            subtitle: Text(video.channelTitle),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                icon: const Icon(Icons.skip_previous),
                onPressed: queue.hasPrevious
                    ? () => ref.read(queueProvider.notifier).previous()
                    : null,
              ),
              IconButton(
                iconSize: 40,
                icon: const Icon(Icons.play_arrow),
                onPressed: () => player.playOrPause(),
              ),
              IconButton(
                icon: const Icon(Icons.skip_next),
                onPressed: queue.hasNext
                    ? () => ref.read(queueProvider.notifier).next()
                    : null,
              ),
            ],
          ),
          if (queue.items.length > 1)
            Expanded(
              child: ListView.builder(
                itemCount: queue.items.length,
                itemBuilder: (context, i) {
                  final item = queue.items[i];
                  return ListTile(
                    dense: true,
                    leading: i == queue.index
                        ? const Icon(Icons.play_arrow, size: 18)
                        : Text('${i + 1}'),
                    title: Text(item.title,
                        maxLines: 1, overflow: TextOverflow.ellipsis),
                    onTap: () => ref
                        .read(queueProvider.notifier)
                        .play(queue.items, startIndex: i),
                  );
                },
              ),
            ),
        ],
      ),
    );
  }
}
