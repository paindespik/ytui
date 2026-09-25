/// Remote-control (D-pad) playback UI for Android TV and projectors.
///
/// media_kit's touch overlay is unreachable without a touchscreen, so on TV the
/// player wraps its video surface in [RemotePlayerSurface] (arrow keys seek, the
/// centre button toggles playback, any key reveals the controls) and draws
/// [RemotePlayerControls]: a focusable progress bar plus an action row that can
/// itself be walked with the D-pad.
library;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../format.dart';
import '../theme.dart';

/// Seeks by [offset], relative to the position currently displayed.
class RemoteSeekIntent extends Intent {
  const RemoteSeekIntent(this.offset);

  final Duration offset;

  static const forward = RemoteSeekIntent(Duration(seconds: 10));
  static const backward = RemoteSeekIntent(Duration(seconds: -10));
}

/// Reveals the controls without touching playback.
class RemoteRevealIntent extends Intent {
  const RemoteRevealIntent();
}

/// Jumps to the next/previous queue entry.
class RemoteSkipIntent extends Intent {
  const RemoteSkipIntent({required this.forward});

  final bool forward;
}

/// Keys that act on playback wherever the focus sits (video surface or the
/// progress bar). Up/down are deliberately absent so directional traversal can
/// still move the focus between the bar and the action row.
const Map<ShortcutActivator, Intent> kRemotePlaybackShortcuts =
    <ShortcutActivator, Intent>{
  SingleActivator(LogicalKeyboardKey.arrowLeft): RemoteSeekIntent.backward,
  SingleActivator(LogicalKeyboardKey.arrowRight): RemoteSeekIntent.forward,
  SingleActivator(LogicalKeyboardKey.mediaRewind): RemoteSeekIntent.backward,
  SingleActivator(LogicalKeyboardKey.mediaFastForward): RemoteSeekIntent.forward,
  SingleActivator(LogicalKeyboardKey.mediaPlayPause): ActivateIntent(),
  SingleActivator(LogicalKeyboardKey.mediaPlay): ActivateIntent(),
  SingleActivator(LogicalKeyboardKey.mediaPause): ActivateIntent(),
  SingleActivator(LogicalKeyboardKey.mediaTrackNext):
      RemoteSkipIntent(forward: true),
  SingleActivator(LogicalKeyboardKey.mediaTrackPrevious):
      RemoteSkipIntent(forward: false),
};

/// [kRemotePlaybackShortcuts] plus up/down, which reveal the controls when the
/// bare video surface holds the focus.
const Map<ShortcutActivator, Intent> _kSurfaceShortcuts =
    <ShortcutActivator, Intent>{
  ...kRemotePlaybackShortcuts,
  SingleActivator(LogicalKeyboardKey.arrowUp): RemoteRevealIntent(),
  SingleActivator(LogicalKeyboardKey.arrowDown): RemoteRevealIntent(),
};

Map<Type, Action<Intent>> _playbackActions({
  required ValueChanged<Duration> onSeek,
  required VoidCallback onTogglePlay,
  required ValueChanged<bool> onSkip,
  VoidCallback? onReveal,
}) {
  return <Type, Action<Intent>>{
    RemoteSeekIntent: CallbackAction<RemoteSeekIntent>(onInvoke: (intent) {
      onReveal?.call();
      onSeek(intent.offset);
      return null;
    }),
    RemoteSkipIntent: CallbackAction<RemoteSkipIntent>(onInvoke: (intent) {
      onReveal?.call();
      onSkip(intent.forward);
      return null;
    }),
    RemoteRevealIntent: CallbackAction<RemoteRevealIntent>(onInvoke: (_) {
      onReveal?.call();
      return null;
    }),
    ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
      onReveal?.call();
      onTogglePlay();
      return null;
    }),
  };
}

/// The video surface, focused whenever the controls are hidden: it turns remote
/// keys into seeks / play-pause and reveals the controls.
class RemotePlayerSurface extends StatelessWidget {
  const RemotePlayerSurface({
    super.key,
    required this.focusNode,
    required this.onSeek,
    required this.onTogglePlay,
    required this.onSkip,
    required this.onReveal,
    required this.child,
  });

  /// The surface is never a traversal target — it is focused explicitly, by the
  /// player, whenever the controls are hidden — so the widget marks the node
  /// `skipTraversal`: arrow keys inside the overlay stay on the overlay.
  final FocusNode focusNode;
  final ValueChanged<Duration> onSeek;
  final VoidCallback onTogglePlay;

  /// `true` moves to the next queue entry, `false` to the previous one.
  final ValueChanged<bool> onSkip;
  final VoidCallback onReveal;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    focusNode.skipTraversal = true;
    return FocusableActionDetector(
      focusNode: focusNode,
      autofocus: true,
      shortcuts: _kSurfaceShortcuts,
      actions: _playbackActions(
        onSeek: onSeek,
        onTogglePlay: onTogglePlay,
        onSkip: onSkip,
        onReveal: onReveal,
      ),
      child: child,
    );
  }
}

/// Bottom overlay: title, progress bar, action row and — when supplied — the
/// "À suivre" strip, all D-pad navigable (down walks bar → row → strip).
class RemotePlayerControls extends StatefulWidget {
  const RemotePlayerControls({
    super.key,
    required this.title,
    required this.position,
    required this.duration,
    required this.playing,
    required this.live,
    required this.rateLabel,
    required this.audioDelayLabel,
    required this.qualityLabel,
    this.qualityTooltip = 'Qualité',
    required this.hasSubtitles,
    required this.hasNext,
    required this.hasPrevious,
    required this.onSeek,
    required this.onTogglePlay,
    required this.onSkip,
    required this.onRate,
    required this.onSubtitles,
    required this.onAudioDelay,
    required this.onQuality,
    required this.onInteract,
    required this.entryFocus,
    this.autoplay = true,
    this.onAutoplay,
    this.loop = false,
    this.onLoop,
    this.sleepLabel,
    this.onSleep,
    this.liked = false,
    this.onLike,
    this.upNext,
    this.hint,
  });

  final String title;
  final Duration position;
  final Duration duration;
  final bool playing;

  /// Live streams have no meaningful timeline: the bar is replaced by a badge
  /// and seeking is disabled (an FLV live stalls on seek).
  final bool live;
  final String rateLabel;
  final String audioDelayLabel;
  final String qualityLabel;

  /// Tooltip of the quality chip: usually the height cap, since the label
  /// shows the served height.
  final String qualityTooltip;
  final bool hasSubtitles;
  final bool hasNext;
  final bool hasPrevious;
  final ValueChanged<Duration> onSeek;
  final VoidCallback onTogglePlay;
  final ValueChanged<bool> onSkip;
  final VoidCallback onRate;
  final VoidCallback onSubtitles;
  final VoidCallback onAudioDelay;
  final VoidCallback onQuality;

  /// Called for keys the controls do not consume themselves — mainly focus
  /// traversal between the bar and the action row — so the caller can postpone
  /// the auto-hide. Consumed keys already report through their own callback.
  final VoidCallback onInteract;

  /// Focus node of the element the remote lands on when the overlay opens (the
  /// progress bar, or play/pause on a live). The caller owns it and focuses it
  /// explicitly: `autofocus` is ignored here because the video surface already
  /// holds the focus of this scope when the overlay appears.
  final FocusNode entryFocus;

  /// "Lecture automatique" toggle; hidden when [onAutoplay] is null.
  final bool autoplay;
  final VoidCallback? onAutoplay;

  /// "Lecture en boucle" toggle; hidden when [onLoop] is null (lives).
  final bool loop;
  final VoidCallback? onLoop;

  /// Sleep timer: [sleepLabel] is the remaining time while one runs (the
  /// button is then highlighted); hidden when [onSleep] is null.
  final String? sleepLabel;
  final VoidCallback? onSleep;

  /// Like toggle (YouTube account); hidden when [onLike] is null.
  final bool liked;
  final VoidCallback? onLike;

  /// Row of upcoming videos under the actions (see [RemoteCardStrip]). Pass
  /// the same instance across position ticks so it is not rebuilt with them.
  final Widget? upNext;

  /// Description of the focused element, shown on the hint line. Supplied by
  /// the caller when [upNext]'s cards publish to it too; owned here otherwise.
  final ValueNotifier<String?>? hint;

  @override
  State<RemotePlayerControls> createState() => _RemotePlayerControlsState();
}

class _RemotePlayerControlsState extends State<RemotePlayerControls> {
  /// Description of the focused button. A remote has no hover, so tooltips
  /// never show: the hint line under the row says what the selection does
  /// (and the state of a toggle) instead.
  final ValueNotifier<String?> _ownHint = ValueNotifier<String?>(null);

  ValueNotifier<String?> get _hint => widget.hint ?? _ownHint;

  @override
  void dispose() {
    _ownHint.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final w = widget;
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    // ← → rather than ◀ ▶: the projector's font fallback drew those as orange
    // emoji buttons (and ignored the U+FE0E text-presentation selector).
    final below = w.upNext == null ? '▼ options' : '▼ options et à suivre';
    final defaultHint = w.live
        ? 'OK lecture/pause · ← → options'
            '${w.upNext == null ? '' : ' · ▼ à suivre'}'
        : '← → reculer/avancer de 10 s · OK lecture/pause · $below';
    return Focus(
      canRequestFocus: false,
      onKeyEvent: (_, event) {
        if (event is KeyDownEvent || event is KeyRepeatEvent) w.onInteract();
        return KeyEventResult.ignored;
      },
      child: Align(
        alignment: Alignment.bottomCenter,
        child: Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Colors.transparent, Color(0xE6000000)],
            ),
          ),
          padding: EdgeInsets.fromLTRB(
              kGutter * 2, 48, kGutter * 2, w.upNext == null ? 24 : 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                w.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.titleLarge?.copyWith(
                  color: Colors.white,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 16),
              if (w.live)
                _LiveBadge(colors: colors)
              else
                _SeekBar(
                  focusNode: w.entryFocus,
                  position: w.position,
                  duration: w.duration,
                  onSeek: w.onSeek,
                  onTogglePlay: w.onTogglePlay,
                  onSkip: w.onSkip,
                ),
              const SizedBox(height: 16),
              _CenteredScrollRow(
                children: [
                  RemoteButton(
                    icon: Icons.skip_previous_rounded,
                    tooltip: 'Vidéo précédente',
                    hint: _hint,
                    onPressed: w.hasPrevious ? () => w.onSkip(false) : null,
                  ),
                  if (!w.live)
                    RemoteButton(
                      icon: Icons.replay_10_rounded,
                      tooltip: 'Reculer de 10 s',
                      hint: _hint,
                      onPressed: () =>
                          w.onSeek(RemoteSeekIntent.backward.offset),
                    ),
                  RemoteButton(
                    icon: w.playing
                        ? Icons.pause_rounded
                        : Icons.play_arrow_rounded,
                    tooltip: w.playing ? 'Pause' : 'Lecture',
                    hint: _hint,
                    focusNode: w.live ? w.entryFocus : null,
                    onPressed: w.onTogglePlay,
                  ),
                  if (!w.live)
                    RemoteButton(
                      icon: Icons.forward_10_rounded,
                      tooltip: 'Avancer de 10 s',
                      hint: _hint,
                      onPressed: () =>
                          w.onSeek(RemoteSeekIntent.forward.offset),
                    ),
                  RemoteButton(
                    icon: Icons.skip_next_rounded,
                    tooltip: 'Vidéo suivante',
                    hint: _hint,
                    onPressed: w.hasNext ? () => w.onSkip(true) : null,
                  ),
                  const _RowDivider(),
                  if (w.onAutoplay != null)
                    RemoteButton(
                      icon: Icons.queue_play_next_rounded,
                      tooltip: w.autoplay
                          ? 'Lecture automatique de la suivante : activée'
                          : 'Lecture automatique de la suivante : désactivée',
                      hint: _hint,
                      selected: w.autoplay,
                      onPressed: w.onAutoplay,
                    ),
                  if (w.onLoop != null)
                    RemoteButton(
                      icon: w.loop
                          ? Icons.repeat_one_on_rounded
                          : Icons.repeat_one_rounded,
                      tooltip: w.loop
                          ? 'Lecture en boucle : activée'
                          : 'Lecture en boucle : désactivée',
                      hint: _hint,
                      selected: w.loop,
                      onPressed: w.onLoop,
                    ),
                  if (w.onSleep != null)
                    RemoteButton(
                      icon: Icons.bedtime_outlined,
                      label: w.sleepLabel,
                      tooltip: w.sleepLabel == null
                          ? 'Minuterie de mise en veille'
                          : 'Minuterie : pause dans ${w.sleepLabel}',
                      hint: _hint,
                      selected: w.sleepLabel != null,
                      onPressed: w.onSleep,
                    ),
                  if (w.onLike != null)
                    RemoteButton(
                      icon: w.liked
                          ? Icons.thumb_up_rounded
                          : Icons.thumb_up_outlined,
                      tooltip: w.liked ? 'Retirer le j\'aime' : 'J\'aime',
                      hint: _hint,
                      selected: w.liked,
                      onPressed: w.onLike,
                    ),
                  RemoteButton(
                    label: w.rateLabel,
                    tooltip: 'Vitesse de lecture',
                    hint: _hint,
                    onPressed: w.onRate,
                  ),
                  RemoteButton(
                    icon: Icons.subtitles_rounded,
                    tooltip: w.hasSubtitles
                        ? 'Sous-titres'
                        : 'Pas de sous-titres pour cette vidéo',
                    hint: _hint,
                    onPressed: w.hasSubtitles ? w.onSubtitles : null,
                  ),
                  RemoteButton(
                    icon: Icons.av_timer_rounded,
                    label: w.audioDelayLabel,
                    tooltip: 'Décalage audio',
                    hint: _hint,
                    onPressed: w.onAudioDelay,
                  ),
                  RemoteButton(
                    label: w.qualityLabel,
                    tooltip: w.qualityTooltip,
                    hint: _hint,
                    onPressed: w.onQuality,
                  ),
                ],
              ),
              if (w.upNext != null) ...[
                const SizedBox(height: 12),
                w.upNext!,
              ],
              const SizedBox(height: 10),
              ValueListenableBuilder<String?>(
                valueListenable: _hint,
                builder: (context, hint, _) => Text(
                  hint ?? defaultHint,
                  textAlign: TextAlign.center,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: hint == null ? Colors.white70 : Colors.white,
                    fontWeight: hint == null ? null : FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Horizontal row that stays centred while it fits and scrolls sideways once
/// it does not: the projector's ~900 dp cannot always hold every action. A
/// focus move off-screen scrolls it in (focus traversal ensures visibility).
class _CenteredScrollRow extends StatelessWidget {
  const _CenteredScrollRow({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: ConstrainedBox(
          constraints: BoxConstraints(minWidth: constraints.maxWidth),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: children,
          ),
        ),
      ),
    );
  }
}

/// Separates the transport buttons from the options.
class _RowDivider extends StatelessWidget {
  const _RowDivider();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 1,
      height: 32,
      margin: const EdgeInsets.symmetric(horizontal: 8),
      color: Colors.white24,
    );
  }
}

class _LiveBadge extends StatelessWidget {
  const _LiveBadge({required this.colors});

  final ColorScheme colors;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: colors.error,
            borderRadius: BorderRadius.circular(kRadiusSm),
          ),
          child: const Text(
            'EN DIRECT',
            style: TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.w700,
              letterSpacing: 1,
            ),
          ),
        ),
      ],
    );
  }
}

/// Focusable progress bar: left/right seek while it holds the focus, up/down
/// fall through to focus traversal so the action row stays reachable.
class _SeekBar extends StatefulWidget {
  const _SeekBar({
    required this.focusNode,
    required this.position,
    required this.duration,
    required this.onSeek,
    required this.onTogglePlay,
    required this.onSkip,
  });

  final FocusNode focusNode;
  final Duration position;
  final Duration duration;
  final ValueChanged<Duration> onSeek;
  final VoidCallback onTogglePlay;
  final ValueChanged<bool> onSkip;

  @override
  State<_SeekBar> createState() => _SeekBarState();
}

class _SeekBarState extends State<_SeekBar> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final total = widget.duration.inMilliseconds;
    final progress = total <= 0
        ? 0.0
        : (widget.position.inMilliseconds / total).clamp(0.0, 1.0);
    final labelStyle = theme.textTheme.titleMedium?.copyWith(
      color: Colors.white,
      fontFeatures: const [FontFeature.tabularFigures()],
    );
    return FocusableActionDetector(
      focusNode: widget.focusNode,
      shortcuts: kRemotePlaybackShortcuts,
      actions: _playbackActions(
        onSeek: widget.onSeek,
        onTogglePlay: widget.onTogglePlay,
        onSkip: widget.onSkip,
      ),
      // Focus, not the highlight mode: a remote is the only pointer here, and
      // Android's touch highlight mode would hide the selection entirely.
      onFocusChange: (value) {
        if (value != _focused) setState(() => _focused = value);
      },
      child: Row(
        children: [
          Text(formatClock(widget.position), style: labelStyle),
          const SizedBox(width: 16),
          Expanded(
            child: Container(
              height: _focused ? 10 : 6,
              decoration: BoxDecoration(
                color: Colors.white24,
                borderRadius: BorderRadius.circular(6),
                border: _focused
                    ? Border.all(color: Colors.white, width: 1)
                    : null,
              ),
              child: FractionallySizedBox(
                alignment: Alignment.centerLeft,
                widthFactor: progress,
                child: Container(
                  decoration: BoxDecoration(
                    color: colors.primary,
                    borderRadius: BorderRadius.circular(6),
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: 16),
          Text(formatClock(widget.duration), style: labelStyle),
        ],
      ),
    );
  }
}

/// An action-row button with a focus ring strong enough to read from a couch.
class RemoteButton extends StatefulWidget {
  const RemoteButton({
    super.key,
    this.icon,
    this.label,
    required this.tooltip,
    required this.onPressed,
    this.focusNode,
    this.selected = false,
    this.hint,
  });

  final IconData? icon;
  final String? label;

  /// What the button does. Also published to [hint] while focused: tooltips
  /// never show on a remote (no hover, no long-press).
  final String tooltip;

  /// `null` disables the button: it is skipped by focus traversal.
  final VoidCallback? onPressed;

  /// Supplied only for the element the overlay focuses on reveal.
  final FocusNode? focusNode;

  /// A toggle that is on (loop, autoplay, a running timer...): tinted even
  /// without the focus.
  final bool selected;

  /// Receives [tooltip] while the button holds the focus.
  final ValueNotifier<String?>? hint;

  @override
  State<RemoteButton> createState() => _RemoteButtonState();
}

class _RemoteButtonState extends State<RemoteButton> {
  bool _focused = false;

  void _publishHint() {
    final hint = widget.hint;
    if (hint == null) return;
    if (_focused) {
      hint.value = widget.tooltip;
    } else if (hint.value == widget.tooltip) {
      hint.value = null;
    }
  }

  @override
  void didUpdateWidget(RemoteButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A toggle pressed while focused changes its own description: the hint
    // line must follow ("activée" → "désactivée"). Deferred: the notifier's
    // listeners may be mid-build.
    if (_focused && oldWidget.tooltip != widget.tooltip) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _focused) _publishHint();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final enabled = widget.onPressed != null;
    final foreground = enabled
        ? (_focused ? colors.onPrimary : Colors.white)
        : Colors.white38;
    final background = _focused
        ? colors.primary
        : widget.selected && enabled
            ? colors.primary.withValues(alpha: 0.35)
            : Colors.white12;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: FocusableActionDetector(
        enabled: enabled,
        focusNode: widget.focusNode,
        actions: <Type, Action<Intent>>{
          ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
            widget.onPressed?.call();
            return null;
          }),
        },
        onFocusChange: (value) {
          if (value != _focused) setState(() => _focused = value);
          _publishHint();
        },
        child: GestureDetector(
          onTap: widget.onPressed,
          child: Tooltip(
            message: widget.tooltip,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: background,
                borderRadius: BorderRadius.circular(kRadiusSm),
              ),
              child: Row(
                children: [
                  if (widget.icon != null)
                    Icon(widget.icon, size: 28, color: foreground),
                  if (widget.icon != null && widget.label != null)
                    const SizedBox(width: 6),
                  if (widget.label != null)
                    Text(
                      widget.label!,
                      style: theme.textTheme.titleSmall?.copyWith(
                        color: foreground,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Titled horizontal strip of [RemoteVideoCard]s under the action row (the
/// queue and the suggestions): what a TV viewer would otherwise never see,
/// since the player never leaves fullscreen there.
class RemoteCardStrip extends StatelessWidget {
  const RemoteCardStrip({super.key, required this.title, required this.cards});

  final String title;
  final List<Widget> cards;

  /// Card height: 16:9 thumbnail of [RemoteVideoCard.width] + two title lines.
  static const double height = 142;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          title,
          style: theme.textTheme.titleSmall?.copyWith(
            color: Colors.white70,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 6),
        SizedBox(
          height: height,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            // Room for the focus ring of the first/last card.
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 3),
            itemCount: cards.length,
            separatorBuilder: (_, __) => const SizedBox(width: 12),
            itemBuilder: (_, i) => cards[i],
          ),
        ),
      ],
    );
  }
}

/// One video of a [RemoteCardStrip]: thumbnail, title, a focus ring, and OK
/// to play it.
class RemoteVideoCard extends StatefulWidget {
  const RemoteVideoCard({
    super.key,
    required this.title,
    required this.thumbnailUrl,
    required this.onPressed,
    this.badge,
    this.durationLabel = '',
    this.hint,
  });

  final String title;
  final String thumbnailUrl;
  final VoidCallback onPressed;

  /// Small label over the thumbnail ("File d'attente", "À suivre"...).
  final String? badge;
  final String durationLabel;

  /// Receives "OK : lire …" while the card holds the focus.
  final ValueNotifier<String?>? hint;

  static const double width = 176;

  @override
  State<RemoteVideoCard> createState() => _RemoteVideoCardState();
}

class _RemoteVideoCardState extends State<RemoteVideoCard> {
  bool _focused = false;

  String get _hintText => 'OK : lire « ${widget.title} »';

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return FocusableActionDetector(
      actions: <Type, Action<Intent>>{
        ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
          widget.onPressed();
          return null;
        }),
      },
      onFocusChange: (value) {
        if (value != _focused) setState(() => _focused = value);
        final hint = widget.hint;
        if (hint == null) return;
        if (value) {
          hint.value = _hintText;
        } else if (hint.value == _hintText) {
          hint.value = null;
        }
      },
      child: GestureDetector(
        onTap: widget.onPressed,
        child: SizedBox(
          width: RemoteVideoCard.width,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AspectRatio(
                aspectRatio: 16 / 9,
                child: DecoratedBox(
                  position: DecorationPosition.foreground,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(kRadiusSm),
                    border: Border.all(
                      color: _focused ? colors.primary : Colors.transparent,
                      width: 3,
                    ),
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(kRadiusSm),
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        if (widget.thumbnailUrl.isNotEmpty)
                          CachedNetworkImage(
                            imageUrl: widget.thumbnailUrl,
                            fit: BoxFit.cover,
                            errorWidget: (_, __, ___) =>
                                const ColoredBox(color: Colors.white12),
                          )
                        else
                          const ColoredBox(
                            color: Colors.white12,
                            child: Icon(Icons.play_circle_outline,
                                color: Colors.white54),
                          ),
                        if (widget.badge != null)
                          Positioned(
                            top: 4,
                            left: 4,
                            child: _Pill(
                              text: widget.badge!,
                              color: colors.primary,
                              textColor: colors.onPrimary,
                            ),
                          ),
                        if (widget.durationLabel.isNotEmpty)
                          Positioned(
                            bottom: 4,
                            right: 4,
                            child: _Pill(
                              text: widget.durationLabel,
                              color: const Color(0xBF000000),
                              textColor: Colors.white,
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 4),
              Text(
                widget.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: _focused ? Colors.white : Colors.white70,
                  fontWeight: _focused ? FontWeight.w600 : null,
                  height: 1.2,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  const _Pill({required this.text, required this.color, required this.textColor});

  final String text;
  final Color color;
  final Color textColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        text,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
              color: textColor,
              fontWeight: FontWeight.w700,
              height: 1,
            ),
      ),
    );
  }
}
