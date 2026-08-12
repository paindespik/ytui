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
import '../widgets/comment_card.dart';
import '../widgets/remote_controls.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

/// Granularity of the audio-delay control: fine enough to chase a projector's
/// speaker latency, coarse enough to reach ±1 s with a remote.
const _kAudioDelayStepMs = 25;

/// Shown whenever an account action comes back as 409: the server holds no
/// YouTube OAuth token (pushed from the desktop with `ytui auth push`).
const _kNotConnected = 'Compte YouTube non connecté (ytui auth push)';

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
  /// media_kit's Android default (`vo=gpu`, `hwdec=auto-safe`) decodes in
  /// hardware but copies every frame back through the CPU before Flutter
  /// uploads it as a texture. On the projector's TV SoC that readback saturates
  /// a core at 1080p and playback collapses to ~10 fps (measured: 100 ms median
  /// between presented frames, against 33 ms of content). `mediacodec_embed`
  /// lets the decoder render straight into the Android surface — same footage
  /// then presents at a flat 33 ms with the app at half a core.
  ///
  /// Kept to leanback: it costs mpv-drawn OSD (subtitles still render, they go
  /// through media_kit's Dart-side [SubtitleView], fed by libmpv's `sub-text`)
  /// and it puts video on a SurfaceView, which the touch overlay and rotation
  /// of the phone build have no need to risk.
  late final mkv.VideoController controller = mkv.VideoController(
    player,
    configuration: ref.read(isTvProvider)
        ? const mkv.VideoControllerConfiguration(
            vo: 'mediacodec_embed',
            hwdec: 'mediacodec',
          )
        : const mkv.VideoControllerConfiguration(),
  );

  /// Applied once before the first [Player.open]; see [_configureMpv].
  late final Future<void> _mpvConfigured = _configureMpv();

  String? _loadedVideoId;

  /// Snapshot of what [_savePosition] needs, kept usable from [dispose] (where
  /// `ref.read` is no longer allowed).
  Video? _playing;
  YtuiApi? _api;
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

  /// The account's rating for the playing video ("like"/"dislike"/"none") and
  /// whether the comments panel replaced the queue/suggestions list. Both
  /// survive nothing but the current screen; playback is never touched by them.
  String _rating = 'none';
  bool _ratingBusy = false;
  bool _showComments = false;

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

  Future<void> _load(Video video,
      {bool resume = true, Duration? startAt}) async {
    _loadedVideoId = video.videoId;
    // Nothing to flush until the new media is actually open: a heartbeat firing
    // mid-load would otherwise store the outgoing media's position under this id.
    _playing = null;
    setState(() {
      _error = null;
      _segments = const [];
      _streams = null;
      _subUrl = null;
      _rating = 'none';
    });
    final api = ref.read(apiProvider);
    _api = api;
    try {
      // Record the watch and fetch resume position + fresh stream URLs.
      unawaited(api.recordWatch(video).catchError((_) {}));
      ref.read(watchedIdsProvider.notifier).markWatched(video.videoId);
      // Keep playing when the screen turns off (foreground service + wakelock).
      unawaited(startPlaybackService(
        title: video.title,
        text: '▶ ${video.channelTitle}',
      ).catchError((_) {}));
      double start = startAt?.inSeconds.toDouble() ?? 0;
      if (startAt == null && resume && !_isLiveId(video)) {
        final info = await api.resume(video.videoId).catchError((_) => null);
        if (info != null) start = resumeStart(info.position, info.duration);
      }
      final streams = await api.videoStreams(video.videoId,
          platform: video.platform, maxHeight: ref.read(maxHeightProvider));
      if (!mounted || _loadedVideoId != video.videoId) return;
      // The audio backend must be picked before playback starts.
      await _mpvConfigured;
      final isSplit = streams.kind == 'split' && streams.audioUrl != null;
      final media =
          Media(isSplit ? (streams.videoUrl ?? streams.url) : streams.url);
      await player.open(media);

      if (isSplit) {
        // DASH: separate video/audio URLs — attach the audio as an external track.
        await player.setAudioTrack(AudioTrack.uri(streams.audioUrl!));
      }
      if (mounted) setState(() => _streams = streams);
      // media_kit's Media(start:) only sets mpv's `start` property, which was
      // measured to either be ignored (playback restarts at 0) or leave the
      // player stuck buffering. Seek explicitly once the media is seekable.
      if (start > 0) {
        // Stay unflushable until the seek lands, or a heartbeat would store the
        // pre-seek position (~0) and wipe the resume point.
        unawaited(_seekToResume(video.videoId, Duration(seconds: start.toInt()))
            .whenComplete(() {
          if (_loadedVideoId == video.videoId) _playing = video;
        }));
      } else {
        _playing = video;
      }
      // Rate persists across open() in media_kit; re-apply defensively.
      if (_rate != 1.0) unawaited(player.setRate(_rate));
      unawaited(_fetchSegments(video));
      unawaited(_fetchRating(video));
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  /// Applies the resume position once the freshly opened media is seekable.
  /// Gives up (plays from the start) if no duration shows up within 20 s.
  Future<void> _seekToResume(String videoId, Duration target) async {
    // player.open() returns before the demuxer reports a duration, and the
    // duration stream may have emitted already — poll the state instead.
    for (var i = 0; i < 100; i++) {
      if (!mounted || _loadedVideoId != videoId) return;
      if (player.state.duration > Duration.zero) break;
      await Future<void>.delayed(const Duration(milliseconds: 200));
    }
    if (!mounted ||
        _loadedVideoId != videoId ||
        player.state.duration <= target) {
      return;
    }
    await player.seek(target);
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

  /// Reads the account's rating so the like button starts in the right state.
  /// A missing token (409) just leaves the button on "not liked".
  Future<void> _fetchRating(Video video) async {
    if (video.platform != 'youtube') return;
    try {
      final rating = await ref.read(apiProvider).videoRating(video.videoId);
      if (mounted && _loadedVideoId == video.videoId) {
        setState(() => _rating = rating);
      }
    } on ApiException {
      // No account or no network: leave the button neutral.
    }
  }

  /// Likes the playing video, or drops the like when it is already there.
  Future<void> _toggleLike(Video video) async {
    final next = _rating == 'like' ? 'none' : 'like';
    setState(() => _ratingBusy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).likeVideo(
            video.videoId,
            platform: video.platform,
            rating: next,
          );
      if (!mounted) return;
      setState(() {
        _rating = next;
        _ratingBusy = false;
      });
      messenger.showSnackBar(SnackBar(
        content: Text(next == 'like' ? 'Vidéo aimée 👍' : 'Like retiré'),
      ));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _ratingBusy = false);
      messenger.showSnackBar(SnackBar(
        content: Text(e.statusCode == 409 ? _kNotConnected : e.toString()),
      ));
    }
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

  /// Height cap applied to every platform. Changing it re-resolves the stream
  /// and resumes where playback was (a live restarts at its live edge).
  Future<void> _showQualitySheet() async {
    final video = ref.read(queueProvider).current;
    _revealControls(pinned: true);
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Consumer(
          builder: (context, ref, _) {
            final current = ref.watch(maxHeightProvider);
            final served = _streams?.height;
            return ListView(
              shrinkWrap: true,
              children: [
                ListTile(
                  title: const Text('Qualité maximale'),
                  subtitle: Text(served == null
                      ? 'La meilleure piste sous le plafond est choisie.'
                      : 'Actuellement servi : ${served}p'),
                ),
                for (final height in kQualityLadder)
                  ListTile(
                    autofocus: height == current,
                    title: Text('${height}p'),
                    trailing: height == current ? const Icon(Icons.check) : null,
                    onTap: () {
                      Navigator.pop(sheetContext);
                      if (height != current && video != null) {
                        unawaited(_setMaxHeight(height, video));
                      }
                    },
                  ),
              ],
            );
          },
        ),
      ),
    );
    if (mounted) _revealControls();
  }

  /// Live for resume purposes: a composite Twitch/TikTok id, or a video the
  /// server currently reports as live (a YouTube live keeps a plain id). Seeking
  /// into a sliding live window stalls the player, so it restarts at the edge.
  bool _isLivePlayback(Video video) =>
      _isLiveId(video) ||
      (ref.read(livesProvider).valueOrNull ?? const <LiveItem>[])
          .any((l) => l.video.videoId == video.videoId);

  Future<void> _setMaxHeight(int height, Video video) async {
    await ref.read(maxHeightProvider.notifier).setHeight(height);
    if (!mounted) return;
    final position = _isLivePlayback(video) ? null : player.state.position;
    await _load(video, resume: false, startAt: position);
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

  /// Pushes the playing position for [video] (defaults to the queue's current
  /// one — pass it explicitly when the queue has already moved on).
  ///
  /// Reads no provider through [ref]: it also runs from [dispose], where the
  /// element is already unmounted and `ref.read` throws.
  Future<void> _savePosition([Video? video]) async {
    final target = video ?? _playing;
    final api = _api;
    if (target == null || api == null || _isLiveId(target)) return;
    final pos = player.state.position;
    final dur = player.state.duration;
    if (dur.inSeconds == 0) return;
    try {
      await api.savePosition(
        target.videoId,
        pos.inSeconds.toDouble(),
        duration: dur.inSeconds.toDouble(),
      );
    } catch (_) {}
  }

  @override
  void dispose() {
    _heartbeat?.cancel();
    // Final flush: the 10 s heartbeat would otherwise drop the last seconds
    // watched before leaving the player.
    unawaited(_savePosition());
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
        // Save where the outgoing video stopped before the player reopens.
        final leaving = previous?.current;
        if (leaving != null && leaving.videoId == _loadedVideoId) {
          unawaited(_savePosition(leaving));
        }
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
                    maxHeight: ref.watch(maxHeightProvider),
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
    // Comments only exist where the backend can list them, and a live shows its
    // chat in that slot instead.
    final commentsAvailable = !chatEnabled &&
        (video.platform == 'youtube' || video.platform == 'odysee');

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
                        if (video.platform == 'youtube')
                          IconButton(
                            icon: Icon(
                              _rating == 'like'
                                  ? Icons.thumb_up_rounded
                                  : Icons.thumb_up_outlined,
                              color: _rating == 'like' ? colors.primary : null,
                            ),
                            tooltip: _rating == 'like' ? 'Retirer le like' : 'J\'aime',
                            onPressed:
                                _ratingBusy ? null : () => _toggleLike(video),
                          ),
                        if (commentsAvailable)
                          IconButton(
                            icon: Icon(
                              _showComments
                                  ? Icons.comment_rounded
                                  : Icons.comment_outlined,
                              color: _showComments ? colors.primary : null,
                            ),
                            tooltip: _showComments
                                ? 'Revenir à la file d\'attente'
                                : 'Commentaires',
                            onPressed: () =>
                                setState(() => _showComments = !_showComments),
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
                        TextButton(
                          onPressed: _showQualitySheet,
                          child: Text(
                            '${ref.watch(maxHeightProvider)}p',
                            style: TextStyle(color: colors.onSurface),
                          ),
                        ),
                      ],
                    ),
                  ),

                  // Queue + suggestions, replaced by the comments panel on
                  // demand (playback is untouched) or by the chat on a live.
                  Expanded(
                    child: chatEnabled
                        ? _LiveChatSection(
                            video: video,
                            key: ValueKey(video.videoId),
                          )
                        : _showComments && commentsAvailable
                            ? _CommentsSection(
                                video: video,
                                key: ValueKey('comments:${video.videoId}'),
                              )
                            : ListView(
                                padding:
                                    const EdgeInsets.only(top: 8, bottom: 16),
                                children: [
                                  if (queue.index + 1 < queue.items.length) ...[
                                    _sectionHeader(
                                        theme, colors, 'File d\'attente'),
                                    for (var i = queue.index + 1;
                                        i < queue.items.length;
                                        i++)
                                      _queueTile(
                                          theme, colors, queue.items[i], i),
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
    required int maxHeight,
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
          qualityLabel: '${maxHeight}p',
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
          onQuality: _showQualitySheet,
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

/// Comments panel shown in place of the queue/suggestions list while the video
/// keeps playing. Pages through the backend cursor and, on YouTube, posts a
/// comment from the composer at the bottom.
class _CommentsSection extends ConsumerStatefulWidget {
  final Video video;

  const _CommentsSection({required this.video, super.key});

  @override
  ConsumerState<_CommentsSection> createState() => _CommentsSectionState();
}

class _CommentsSectionState extends ConsumerState<_CommentsSection> {
  final List<Comment> _items = [];
  final TextEditingController _composer = TextEditingController();

  /// Reply threads by parent comment id, created on first expand.
  final Map<String, _ReplyThread> _threads = {};
  String? _cursor;
  int _total = 0;
  bool _loading = true;
  bool _exhausted = false;
  bool _disabled = false;
  bool _posting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadMore();
  }

  @override
  void dispose() {
    _composer.dispose();
    for (final thread in _threads.values) {
      thread.composer.dispose();
    }
    super.dispose();
  }

  bool get _canPost => widget.video.platform == 'youtube' && !_disabled;

  Future<void> _loadMore() async {
    if (_exhausted) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await ref.read(apiProvider).videoComments(
            widget.video.videoId,
            platform: widget.video.platform,
            cursor: _cursor,
            pageSize: 20,
          );
      if (!mounted) return;
      setState(() {
        _items.addAll(page.items);
        // Only the first page carries the grand total; later pages report 0.
        if (page.total > 0) _total = page.total;
        _disabled = page.disabled;
        _cursor = page.nextCursor;
        _exhausted = page.nextCursor == null;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _exhausted = true;
        _error = e.statusCode == 409 ? _kNotConnected : e.detail;
      });
    }
  }

  Future<void> _post() async {
    final text = _composer.text.trim();
    if (text.isEmpty || _posting) return;
    setState(() => _posting = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final posted = await ref.read(apiProvider).commentVideo(
            widget.video.videoId,
            text,
            platform: widget.video.platform,
          );
      if (!mounted) return;
      _composer.clear();
      setState(() {
        // Relevance ordering would bury a brand-new comment: show it on top.
        _items.insert(0, posted);
        if (_total > 0) _total += 1;
        _posting = false;
      });
      messenger.showSnackBar(const SnackBar(content: Text('Commentaire publié')));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _posting = false);
      messenger.showSnackBar(SnackBar(
        content: Text(e.statusCode == 409 ? _kNotConnected : e.toString()),
      ));
    }
  }

  /// Expands (or collapses) one comment's replies; [openComposer] forces the
  /// thread open and reveals its reply field.
  void _toggleReplies(Comment comment, {bool openComposer = false}) {
    final thread = _threads.putIfAbsent(comment.commentId, _ReplyThread.new);
    setState(() {
      thread.expanded = openComposer ? true : !thread.expanded;
      if (openComposer) thread.composerOpen = true;
    });
    if (thread.expanded &&
        thread.items.isEmpty &&
        !thread.exhausted &&
        !thread.loading &&
        comment.replies > 0) {
      _loadReplies(comment);
    }
  }

  Future<void> _loadReplies(Comment comment) async {
    final thread = _threads.putIfAbsent(comment.commentId, _ReplyThread.new);
    if (thread.loading || thread.exhausted) return;
    setState(() {
      thread.loading = true;
      thread.error = null;
    });
    try {
      final page = await ref.read(apiProvider).commentReplies(
            widget.video.videoId,
            comment.commentId,
            platform: widget.video.platform,
            cursor: thread.cursor,
            pageSize: 20,
          );
      if (!mounted) return;
      setState(() {
        thread.items.addAll(page.items);
        thread.cursor = page.nextCursor;
        thread.exhausted = page.nextCursor == null;
        thread.loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        thread.loading = false;
        thread.exhausted = true;
        thread.error = e.statusCode == 409 ? _kNotConnected : e.detail;
      });
    }
  }

  Future<void> _postReply(Comment comment) async {
    final thread = _threads.putIfAbsent(comment.commentId, _ReplyThread.new);
    final text = thread.composer.text.trim();
    if (text.isEmpty || thread.posting) return;
    setState(() => thread.posting = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final posted = await ref.read(apiProvider).replyComment(
            widget.video.videoId,
            comment.commentId,
            text,
            platform: widget.video.platform,
          );
      if (!mounted) return;
      thread.composer.clear();
      setState(() {
        // Replies read oldest first, so a fresh one belongs at the end.
        thread.items.add(posted);
        thread.posting = false;
      });
      messenger.showSnackBar(const SnackBar(content: Text('Réponse publiée')));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => thread.posting = false);
      messenger.showSnackBar(SnackBar(
        content: Text(e.statusCode == 409 ? _kNotConnected : e.toString()),
      ));
    }
  }

  /// The expanded thread of [comment]: replies, loader, error, pager and — on
  /// YouTube — its own composer, all indented under the parent card.
  List<Widget> _replyWidgets(Comment comment) {
    final thread = _threads[comment.commentId];
    if (thread == null) return const [];
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return [
      for (final reply in thread.items)
        Padding(
          padding: const EdgeInsets.only(left: 28),
          child: CommentCard(comment: reply),
        ),
      if (thread.loading)
        const Padding(
          padding: EdgeInsets.only(left: 28, top: 8, bottom: 8),
          child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
        ),
      if (thread.error != null)
        Padding(
          padding: const EdgeInsets.only(left: 28, bottom: 8),
          child: Text(
            thread.error!,
            style: theme.textTheme.bodySmall?.copyWith(color: colors.error),
          ),
        ),
      if (comment.replies > 0 && !thread.exhausted && !thread.loading)
        Padding(
          padding: const EdgeInsets.only(left: 28),
          child: Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: () => _loadReplies(comment),
              child: const Text('Charger plus de réponses'),
            ),
          ),
        ),
      if (_canPost && thread.composerOpen)
        Padding(
          padding: const EdgeInsets.only(left: 28, top: 4, bottom: 8),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: thread.composer,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _postReply(comment),
                  decoration: const InputDecoration(
                    isDense: true,
                    border: OutlineInputBorder(),
                    hintText: 'Répondre…',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                icon: thread.posting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send_rounded),
                tooltip: 'Publier',
                onPressed: thread.posting ? null : () => _postReply(comment),
              ),
            ],
          ),
        ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final title = _disabled
        ? 'Commentaires désactivés'
        : _total > 0
            ? 'Commentaires ($_total)'
            : 'Commentaires';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(kGutter, 12, kGutter, 6),
          child: Text(
            title,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: colors.primary,
            ),
          ),
        ),
        Expanded(
          child: NotificationListener<ScrollUpdateNotification>(
            // Reaching the end pulls the next page in, so the footer button is
            // only a fallback.
            onNotification: (notification) {
              final m = notification.metrics;
              if (!_loading &&
                  !_exhausted &&
                  m.axis == Axis.vertical &&
                  m.pixels > m.maxScrollExtent - 400) {
                _loadMore();
              }
              return false;
            },
            child: ListView(
              padding: const EdgeInsets.fromLTRB(kGutter, 0, kGutter, 12),
              children: [
                if (_error != null)
                  Text(
                    _error!,
                    style: theme.textTheme.bodySmall?.copyWith(color: colors.error),
                  ),
                for (final comment in _items) ...[
                  CommentCard(
                    comment: comment,
                    repliesExpanded: _threads[comment.commentId]?.expanded ?? false,
                    onToggleReplies:
                        comment.replies > 0 ? () => _toggleReplies(comment) : null,
                  ),
                  if (_canPost)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        icon: const Icon(Icons.reply, size: 16),
                        label: const Text('Répondre'),
                        onPressed: () => _toggleReplies(comment, openComposer: true),
                      ),
                    ),
                  if (_threads[comment.commentId]?.expanded ?? false)
                    ..._replyWidgets(comment),
                ],
                if (_loading)
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
                  )
                else if (_items.isEmpty && _error == null)
                  Text(
                    _disabled
                        ? 'L\'auteur a désactivé les commentaires.'
                        : 'Aucun commentaire.',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: colors.onSurfaceVariant,
                    ),
                  )
                else if (!_exhausted)
                  TextButton(
                    onPressed: _loadMore,
                    child: const Text('Charger plus de commentaires'),
                  ),
              ],
            ),
          ),
        ),
        if (_canPost)
          Padding(
            padding: const EdgeInsets.fromLTRB(kGutter, 0, kGutter, 8),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _composer,
                    textInputAction: TextInputAction.send,
                    onSubmitted: (_) => _post(),
                    decoration: const InputDecoration(
                      isDense: true,
                      border: OutlineInputBorder(),
                      hintText: 'Ajouter un commentaire…',
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  icon: _posting
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.send_rounded),
                  tooltip: 'Publier',
                  onPressed: _posting ? null : _post,
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// Reply thread of one comment, owned by [_CommentsSectionState].
class _ReplyThread {
  final List<Comment> items = [];
  final TextEditingController composer = TextEditingController();
  String? cursor;
  bool expanded = false;
  bool loading = false;
  bool exhausted = false;
  bool posting = false;
  bool composerOpen = false;
  String? error;
}
