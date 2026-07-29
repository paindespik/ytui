/// Integrated player: resolves streams just before playback (URLs expire),
/// plays through media_kit, records history and heartbeats the position.
/// Shows YouTube suggestions below the video, autoplays the next one, and
/// keeps playing in the background (screen off) via a foreground service.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart' as mkv;

import '../api/client.dart';
import '../api/models.dart';
import '../services/background_playback.dart';
import '../state/providers.dart';
import '../state/settings.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/remote_controls.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

/// Granularity of the audio-delay control: fine enough to chase a projector's
/// speaker latency, coarse enough to reach ±1 s with a remote.
const _kAudioDelayStepMs = 25;

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
  /// `logLevel: info` keeps libmpv's "AO: [...]" banner reachable through
  /// [Player.stream.log] — the only way to tell which audio backend (and thus
  /// which latency reporting) a given TV/projector ended up with.
  late final Player player = Player(
    configuration: const PlayerConfiguration(logLevel: MPVLogLevel.info),
  );
  late final mkv.VideoController controller = mkv.VideoController(player);

  /// Applied once before the first [Player.open]; see [_configureMpv].
  late final Future<void> _mpvConfigured = _configureMpv();

  String? _loadedVideoId;
  bool _retried = false;
  bool _errorCheckPending = false;
  String? _error;
  Timer? _heartbeat;
  List<SponsorSegment> _segments = const [];
  DateTime _lastSkip = DateTime.fromMillisecondsSinceEpoch(0);
  double _rate = 1.0;
  StreamInfo? _streams;
  String? _subUrl;
  StreamSubscription<void>? _completedSub;
  StreamSubscription<String>? _errorSub;
  StreamSubscription<bool>? _playingSub;
  StreamSubscription<Duration>? _positionSub;
  StreamSubscription<PlayerLog>? _logSub;
  bool _immersive = false;

  /// Remote/TV state: the controls overlay is hidden until a key is pressed,
  /// then auto-hides again while playing. [RemotePlayerSurface] keeps its own
  /// node out of focus traversal, so once the overlay is up the arrow keys walk
  /// its bar and action row instead of falling back to the video surface.
  final FocusNode _surfaceFocus = FocusNode(debugLabel: 'player surface');
  final FocusNode _controlsFocus = FocusNode(debugLabel: 'player controls');
  bool _controlsVisible = false;
  Timer? _hideControls;

  /// Pending seek target while arrow keys are still coming in: presses
  /// accumulate and a single seek is issued once they stop, so holding the
  /// remote scrubs instead of queueing dozens of decoder seeks.
  Duration? _seekTarget;
  Timer? _seekDebounce;

  /// Landscape → fullscreen: hide the system bars (immersive) while the video
  /// fills the screen, mirroring the YouTube app. Idempotent so it can be
  /// called on every build.
  void _applyImmersive(bool on) {
    if (_immersive == on) return;
    _immersive = on;
    if (on) {
      SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    } else {
      SystemChrome.setEnabledSystemUIMode(
        SystemUiMode.manual,
        overlays: SystemUiOverlay.values,
      );
    }
  }

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
      // libmpv also surfaces non-fatal decoder noise while playback keeps
      // running (e.g. h264 "Late SEI" on TikTok FLV lives). Genuine load
      // failures fire before playback starts (playing=false, handled below);
      // a mid-stream death flips `playing` to false moments later — so when
      // the error arrives mid-playback, defer and recheck instead of either
      // trusting or swallowing it.
      if (player.state.playing) {
        if (_errorCheckPending) return;
        _errorCheckPending = true;
        await Future<void>.delayed(const Duration(seconds: 1));
        _errorCheckPending = false;
        if (!mounted || player.state.playing) return;
      }
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
    _positionSub = player.stream.position.listen(_maybeSkipSponsor);
    _logSub = player.stream.log.listen((log) {
      // Only the audio-output banner: it names the backend and, for opensles,
      // the device latency it managed to query — the two things that decide
      // A/V sync on TV/projector audio paths.
      if (log.text.startsWith('AO:') || log.prefix == 'ao') {
        debugPrint('ytui mpv/${log.prefix}: ${log.text.trim()}');
      }
    });
    _heartbeat = Timer.periodic(const Duration(seconds: 10), (_) => _savePosition());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final video = ref.read(queueProvider).current;
      if (video != null) _load(video);
    });
  }

  /// libmpv tuning that media_kit does not expose through [PlayerConfiguration].
  ///
  /// media_kit pins `ao=opensles`, which only queries the sink latency once at
  /// init (and gets 0 when the platform refuses): on a TV/projector — HDMI,
  /// internal DSP, Bluetooth — the unreported latency shows up as sound running
  /// ahead of the picture. mpv's AudioTrack output tracks the live device
  /// latency like every other Android player does, so prefer it and keep
  /// opensles as a fallback in case it fails to initialise.
  Future<void> _configureMpv() async {
    final platform = player.platform;
    if (platform is! NativePlayer) return;
    await platform.setProperty('ao', 'audiotrack,opensles');
    await _applyAudioDelay(ref.read(audioDelayProvider));
  }

  /// Pushes the user's audio offset to libmpv: positive delays the sound,
  /// negative delays the picture (mpv's `--audio-delay` convention).
  Future<void> _applyAudioDelay(int milliseconds) async {
    final platform = player.platform;
    if (platform is! NativePlayer) return;
    await platform.setProperty(
      'audio-delay',
      (milliseconds / 1000).toStringAsFixed(3),
    );
    // The offset has no on-screen effect to check against, so read back what
    // libmpv kept whenever the user is running with one.
    if (milliseconds != 0) {
      debugPrint(
        'ytui mpv/audio-delay: ${await platform.getProperty('audio-delay')}',
      );
    }
  }

  /// Plays the first suggestion for the current video (autoplay at end of queue).
  Future<void> _autoplayNext() async {
    final video = ref.read(queueProvider).current;
    if (video == null) return;
    try {
      final related =
          await ref.read(relatedProvider((video.videoId, video.platform)).future);
      if (!mounted || related.isEmpty) return;
      final notifier = ref.read(queueProvider.notifier);
      notifier.enqueue(related.first);
      notifier.next();
    } catch (_) {}
  }

  /// Composite "channel:broadcast_id" ids (Twitch/TikTok) are live streams:
  /// unseekable (a resume-seek stalls FLV lives) with no meaningful resume
  /// position (mpv reports the rolling live buffer as duration).
  static bool _isLiveId(Video video) =>
      (video.platform == 'twitch' || video.platform == 'tiktok') &&
      video.videoId.contains(':');

  Future<void> _load(Video video, {bool resume = true}) async {
    _loadedVideoId = video.videoId;
    setState(() {
      _error = null;
      _segments = const [];
      _streams = null;
      _subUrl = null;
    });
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
      if (resume && !_isLiveId(video)) {
        final info = await api.resume(video.videoId).catchError((_) => null);
        if (info != null) start = resumeStart(info.position, info.duration);
      }
      final streams =
          await api.videoStreams(video.videoId, platform: video.platform);
      if (!mounted || _loadedVideoId != video.videoId) return;
      // The audio backend must be picked before playback starts.
      await _mpvConfigured;
      final isSplit = streams.kind == 'split' && streams.audioUrl != null;
      final media = Media(isSplit ? (streams.videoUrl ?? streams.url) : streams.url,
          start: Duration(seconds: start.toInt()));
      await player.open(media);
      if (isSplit) {
        // DASH: separate video/audio URLs — attach the audio as an external track.
        await player.setAudioTrack(AudioTrack.uri(streams.audioUrl!));
      }
      if (mounted) setState(() => _streams = streams);
      // Rate persists across open() in media_kit; re-apply defensively.
      if (_rate != 1.0) unawaited(player.setRate(_rate));
      unawaited(_fetchSegments(video));
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  Future<void> _fetchSegments(Video video) async {
    if (video.platform != 'youtube') return;
    try {
      final segs = await ref.read(apiProvider).sponsorSegments(video.videoId);
      if (mounted && _loadedVideoId == video.videoId) {
        setState(() => _segments = segs);
      }
    } catch (_) {}
  }

  void _maybeSkipSponsor(Duration position) {
    if (_segments.isEmpty || !ref.read(sponsorblockProvider)) return;
    if (DateTime.now().difference(_lastSkip).inMilliseconds < 1000) return;
    final pos = position.inMilliseconds / 1000.0;
    for (final seg in _segments) {
      if (pos >= seg.start && pos < seg.end - 0.5) {
        _lastSkip = DateTime.now();
        unawaited(player.seek(Duration(milliseconds: (seg.end * 1000).round())));
        break;
      }
    }
  }

  static String _fmtRate(double rate) =>
      rate == rate.roundToDouble() ? '${rate.toInt()}×' : '$rate×';

  static String _fmtAudioDelay(int milliseconds) =>
      milliseconds == 0 ? '0 ms' : '${milliseconds > 0 ? '+' : ''}$milliseconds ms';

  /// Remote/TV only: reveals the controls overlay and re-arms the auto-hide.
  /// [pinned] keeps it up while a bottom sheet is open on top of it.
  void _revealControls({bool pinned = false}) {
    if (!ref.read(isTvProvider)) return;
    _hideControls?.cancel();
    _hideControls = null;
    if (!_controlsVisible) {
      setState(() => _controlsVisible = true);
      // `autofocus` would be ignored: the video surface already owns the focus
      // of this scope, so move it onto the overlay once it is laid out.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _controlsVisible) _controlsFocus.requestFocus();
      });
    }
    if (pinned) return;
    _hideControls = Timer(const Duration(seconds: 5), () {
      // Keep the controls up while paused, as every TV player does.
      if (!player.state.playing) {
        _revealControls();
        return;
      }
      _dismissControls();
    });
  }

  void _dismissControls() {
    _hideControls?.cancel();
    _hideControls = null;
    if (!_controlsVisible) return;
    setState(() => _controlsVisible = false);
    // Hand the keys back to the video surface.
    _surfaceFocus.requestFocus();
  }

  /// Accumulates arrow-key presses into a single seek: the bar previews the
  /// pending target while keys keep coming, then one seek is issued.
  void _seekBy(Duration offset) {
    final video = ref.read(queueProvider).current;
    if (video == null || _isLiveId(video)) return;
    final duration = player.state.duration;
    var target = (_seekTarget ?? player.state.position) + offset;
    if (target < Duration.zero) target = Duration.zero;
    if (duration > Duration.zero && target > duration) target = duration;
    setState(() => _seekTarget = target);
    _seekDebounce?.cancel();
    _seekDebounce = Timer(const Duration(milliseconds: 400), () async {
      final pending = _seekTarget;
      if (pending == null) return;
      await player.seek(pending);
      if (mounted) setState(() => _seekTarget = null);
    });
  }

  void _skip(bool forward) {
    final queue = ref.read(queueProvider);
    final notifier = ref.read(queueProvider.notifier);
    if (forward) {
      if (queue.hasNext) notifier.next();
    } else if (queue.hasPrevious) {
      notifier.previous();
    }
  }

  /// Option sheets are shared by touch and remote: the current value gets
  /// `autofocus` so a D-pad lands on it, and the controls overlay stays pinned
  /// underneath until the sheet closes.
  Future<void> _showSpeedSheet() async {
    const rates = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
    _revealControls(pinned: true);
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            for (final rate in rates)
              ListTile(
                autofocus: rate == _rate,
                title: Text(_fmtRate(rate)),
                trailing: rate == _rate ? const Icon(Icons.check) : null,
                onTap: () {
                  setState(() => _rate = rate);
                  unawaited(player.setRate(rate));
                  Navigator.pop(sheetContext);
                },
              ),
          ],
        ),
      ),
    );
    if (mounted) _revealControls();
  }

  Future<void> _showSubtitleSheet() async {
    final tracks = _streams?.subtitles ?? const [];
    _revealControls(pinned: true);
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            ListTile(
              autofocus: _subUrl == null,
              title: const Text('Désactivés'),
              trailing: _subUrl == null ? const Icon(Icons.check) : null,
              onTap: () {
                unawaited(player.setSubtitleTrack(SubtitleTrack.no()));
                setState(() => _subUrl = null);
                Navigator.pop(sheetContext);
              },
            ),
            for (final track in tracks)
              ListTile(
                autofocus: _subUrl == track.url,
                title: Text(track.label.isNotEmpty ? track.label : track.lang),
                trailing: _subUrl == track.url ? const Icon(Icons.check) : null,
                onTap: () {
                  unawaited(player.setSubtitleTrack(SubtitleTrack.uri(
                    track.url,
                    title: track.label,
                    language: track.lang,
                  )));
                  setState(() => _subUrl = track.url);
                  Navigator.pop(sheetContext);
                },
              ),
          ],
        ),
      ),
    );
    if (mounted) _revealControls();
  }

  /// Audio/video offset: a projector's own speakers (or an HDMI/Bluetooth sink)
  /// add a latency nothing reports, so it is dialled in by ear — the value
  /// applies live, while the video keeps playing behind the sheet — and
  /// remembered per device.
  Future<void> _showAudioDelaySheet() async {
    _revealControls(pinned: true);
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Consumer(
          builder: (context, ref, _) {
            final delay = ref.watch(audioDelayProvider);
            return Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const ListTile(
                  title: Text('Décalage audio'),
                  subtitle: Text(
                    'Ajustez jusqu\'à ce que le son colle à l\'image : valeur '
                    'négative si le son arrive en retard.',
                  ),
                ),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    IconButton.filled(
                      autofocus: true,
                      iconSize: 32,
                      icon: const Icon(Icons.remove),
                      tooltip: '-25 ms',
                      onPressed: () => _nudgeAudioDelay(-_kAudioDelayStepMs),
                    ),
                    SizedBox(
                      width: 160,
                      child: Text(
                        _fmtAudioDelay(delay),
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                    IconButton.filled(
                      iconSize: 32,
                      icon: const Icon(Icons.add),
                      tooltip: '+25 ms',
                      onPressed: () => _nudgeAudioDelay(_kAudioDelayStepMs),
                    ),
                  ],
                ),
                TextButton(
                  onPressed: delay == 0 ? null : () => _setAudioDelay(0),
                  child: const Text('Réinitialiser'),
                ),
                const SizedBox(height: 8),
              ],
            );
          },
        ),
      ),
    );
    if (mounted) _revealControls();
  }

  void _nudgeAudioDelay(int deltaMs) => _setAudioDelay(
      (ref.read(audioDelayProvider) + deltaMs).clamp(-1000, 1000));

  void _setAudioDelay(int milliseconds) {
    unawaited(ref.read(audioDelayProvider.notifier).setDelay(milliseconds));
    unawaited(_applyAudioDelay(milliseconds));
  }

  Future<void> _savePosition() async {
    final video = ref.read(queueProvider).current;
    if (video == null || _isLiveId(video)) return;
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
    _positionSub?.cancel();
    _logSub?.cancel();
    _hideControls?.cancel();
    _seekDebounce?.cancel();
    _surfaceFocus.dispose();
    _controlsFocus.dispose();
    if (_immersive) {
      SystemChrome.setEnabledSystemUIMode(
        SystemUiMode.manual,
        overlays: SystemUiOverlay.values,
      );
    }
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
      _applyImmersive(false);
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
    // Landscape → fullscreen video, YouTube-style: fill the screen with the
    // video surface and hide the app chrome + system bars.
    final fullscreen =
        MediaQuery.of(context).orientation == Orientation.landscape &&
            _error == null;
    _applyImmersive(fullscreen);

    if (fullscreen) {
      if (!ref.watch(isTvProvider)) {
        return Scaffold(
          backgroundColor: Colors.black,
          body: SizedBox.expand(child: _videoSurface()),
        );
      }
      // TV/projector: no touchscreen, so the remote drives playback and the
      // controls overlay doubles as the progress bar. Back closes the overlay
      // before it leaves the player.
      return Scaffold(
        backgroundColor: Colors.black,
        body: PopScope(
          canPop: !_controlsVisible,
          onPopInvokedWithResult: (didPop, _) {
            if (!didPop) _dismissControls();
          },
          child: SizedBox.expand(
            child: Stack(
              fit: StackFit.expand,
              children: [
                RemotePlayerSurface(
                  focusNode: _surfaceFocus,
                  onSeek: _seekBy,
                  onTogglePlay: player.playOrPause,
                  onSkip: _skip,
                  onReveal: _revealControls,
                  child: _videoSurface(),
                ),
                if (_controlsVisible)
                  _remoteControls(
                    video,
                    hasNext: queue.hasNext,
                    hasPrevious: queue.hasPrevious,
                    audioDelayMs: ref.watch(audioDelayProvider),
                  ),
              ],
            ),
          ),
        ),
      );
    }

    final inLives = (ref.watch(livesProvider).valueOrNull ?? const <LiveItem>[])
        .any((l) => l.video.videoId == video.videoId);
    final chatEnabled =
        (video.platform == 'youtube' || video.platform == 'twitch') &&
            (_isLiveId(video) || inLives);

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
                : _videoSurface(),
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
                        const SizedBox(width: 8),
                        TextButton(
                          onPressed: _showSpeedSheet,
                          child: Text(
                            _fmtRate(_rate),
                            style: TextStyle(color: colors.onSurface),
                          ),
                        ),
                        IconButton(
                          icon: Icon(
                            _subUrl != null
                                ? Icons.subtitles
                                : Icons.subtitles_outlined,
                          ),
                          tooltip: 'Sous-titres',
                          onPressed: (_streams?.subtitles.isEmpty ?? true)
                              ? null
                              : _showSubtitleSheet,
                        ),
                        IconButton(
                          icon: const Icon(Icons.av_timer_rounded),
                          tooltip: 'Décalage audio',
                          onPressed: _showAudioDelaySheet,
                        ),
                      ],
                    ),
                  ),

                  // Queue + suggestions, or the live chat panel for lives
                  Expanded(
                    child: chatEnabled
                        ? _LiveChatSection(
                            video: video,
                            key: ValueKey(video.videoId),
                          )
                        : ListView(
                            padding: const EdgeInsets.only(top: 8, bottom: 16),
                            children: [
                              if (queue.index + 1 < queue.items.length) ...[
                                _sectionHeader(theme, colors, 'File d\'attente'),
                                for (var i = queue.index + 1;
                                    i < queue.items.length;
                                    i++)
                                  _queueTile(theme, colors, queue.items[i], i),
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

  /// The media_kit video surface, shared by the portrait 16:9 box and the
  /// landscape fullscreen layout.
  Widget _videoSurface() {
    return mkv.Video(
      controller: controller,
      // On TV the touch overlay is dead weight (and its buttons would compete
      // for D-pad focus): [RemotePlayerControls] replaces it.
      controls: ref.read(isTvProvider)
          ? mkv.NoVideoControls
          : mkv.AdaptiveVideoControls,
      // Keep playing when the screen turns off; the foreground service
      // (background_playback.dart) holds the process alive.
      pauseUponEnteringBackgroundMode: false,
    );
  }

  /// Remote overlay, fed by the playback streams: [_seekTarget] wins over the
  /// reported position so a pending scrub is what the bar shows.
  Widget _remoteControls(
    Video video, {
    required bool hasNext,
    required bool hasPrevious,
    required int audioDelayMs,
  }) {
    return StreamBuilder<bool>(
      stream: player.stream.playing,
      initialData: player.state.playing,
      builder: (context, playingSnapshot) => StreamBuilder<Duration>(
        stream: player.stream.position,
        initialData: player.state.position,
        builder: (context, positionSnapshot) => RemotePlayerControls(
          title: video.title,
          position:
              _seekTarget ?? positionSnapshot.data ?? player.state.position,
          duration: player.state.duration,
          playing: playingSnapshot.data ?? false,
          live: _isLiveId(video),
          rateLabel: _fmtRate(_rate),
          audioDelayLabel: _fmtAudioDelay(audioDelayMs),
          hasSubtitles: _streams?.subtitles.isNotEmpty ?? false,
          hasNext: hasNext,
          hasPrevious: hasPrevious,
          // Every action postpones the auto-hide: the overlay must not vanish
          // mid-scrub.
          onSeek: (offset) {
            _revealControls();
            _seekBy(offset);
          },
          onTogglePlay: () {
            _revealControls();
            player.playOrPause();
          },
          onSkip: (forward) {
            _revealControls();
            _skip(forward);
          },
          onRate: _showSpeedSheet,
          onSubtitles: _showSubtitleSheet,
          onAudioDelay: _showAudioDelaySheet,
          onInteract: _revealControls,
          entryFocus: _controlsFocus,
        ),
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

  /// A single upcoming queue entry. [i] is its index into the full queue;
  /// [position] is its 1-based rank among the upcoming items. Tapping jumps to
  /// it and plays it now (the others follow it in order).
  Widget _queueTile(ThemeData theme, ColorScheme colors, Video item, int i) {
    final position = i - ref.read(queueProvider).index;
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(kRadiusSm),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(kRadiusSm),
        onTap: () => ref.read(queueProvider.notifier).jumpTo(i),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(
            children: [
              SizedBox(
                width: 24,
                child: Text(
                  '$position',
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
                    color: colors.onSurface,
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
              onTap: () => ref.read(queueProvider.notifier).enqueue(upNext),
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
                  onTap: () => ref.read(queueProvider.notifier).enqueue(item),
                ),
            ],
          ],
        );
      },
    );
  }
}

/// Read-only live chat panel: polls the backend every 3 s while the live
/// plays, appends new messages (capped at 200) and auto-scrolls to the end.
class _LiveChatSection extends ConsumerStatefulWidget {
  final Video video;

  const _LiveChatSection({required this.video, super.key});

  @override
  ConsumerState<_LiveChatSection> createState() => _LiveChatSectionState();
}

class _LiveChatSectionState extends ConsumerState<_LiveChatSection> {
  final List<ChatMessage> _messages = [];
  int _cursor = 0;
  bool _active = true;
  Timer? _timer;
  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _poll());
    _poll();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      if (!mounted) return;
      final page = await ref.read(apiProvider).liveChat(
            widget.video.videoId,
            platform: widget.video.platform,
            cursor: _cursor,
          );
      if (!mounted) return;
      setState(() {
        _messages.addAll(page.messages);
        if (_messages.length > 200) {
          _messages.removeRange(0, _messages.length - 200);
        }
        _cursor = page.cursor;
        _active = page.active;
      });
      if (!page.active) _timer?.cancel();
      if (page.messages.isNotEmpty) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (_scroll.hasClients) {
            _scroll.jumpTo(_scroll.position.maxScrollExtent);
          }
        });
      }
    } on ApiException {
      // Transient error — skip this tick.
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(kGutter, 12, kGutter, 6),
          child: Text(
            'Chat en direct',
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: colors.primary,
            ),
          ),
        ),
        Expanded(
          child: _messages.isEmpty
              ? Padding(
                  padding: const EdgeInsets.all(kGutter),
                  child: Text(
                    _active ? 'En attente de messages…' : 'Le direct est terminé.',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: colors.onSurfaceVariant,
                    ),
                  ),
                )
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.only(top: 4, bottom: 16),
                  itemCount: _messages.length,
                  itemBuilder: (context, i) {
                    final m = _messages[i];
                    final authorColor =
                        (m.color != null && m.color!.length == 7)
                            ? Color(
                                int.parse('FF${m.color!.substring(1)}', radix: 16))
                            : kTwitchPurple;
                    return Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: kGutter, vertical: 3),
                      child: Text.rich(
                        TextSpan(
                          children: [
                            TextSpan(
                              text: '${m.author}  ',
                              style: theme.textTheme.bodySmall?.copyWith(
                                fontWeight: FontWeight.bold,
                                color: authorColor,
                              ),
                            ),
                            TextSpan(
                              text: m.text,
                              style: theme.textTheme.bodySmall?.copyWith(
                                color: colors.onSurface,
                              ),
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }
}
