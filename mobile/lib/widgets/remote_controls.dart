/// Remote-control (D-pad) playback UI for Android TV and projectors.
///
/// media_kit's touch overlay is unreachable without a touchscreen, so on TV the
/// player wraps its video surface in [RemotePlayerSurface] (arrow keys seek, the
/// centre button toggles playback, any key reveals the controls) and draws
/// [RemotePlayerControls]: a focusable progress bar plus an action row that can
/// itself be walked with the D-pad.
library;

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

/// Bottom overlay: title, progress bar and action row, all D-pad navigable.
class RemotePlayerControls extends StatelessWidget {
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Focus(
      canRequestFocus: false,
      onKeyEvent: (_, event) {
        if (event is KeyDownEvent || event is KeyRepeatEvent) onInteract();
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
          padding: const EdgeInsets.fromLTRB(kGutter * 2, 48, kGutter * 2, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.titleLarge?.copyWith(
                  color: Colors.white,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 16),
              if (live)
                _LiveBadge(colors: colors)
              else
                _SeekBar(
                  focusNode: entryFocus,
                  position: position,
                  duration: duration,
                  onSeek: onSeek,
                  onTogglePlay: onTogglePlay,
                  onSkip: onSkip,
                ),
              const SizedBox(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  RemoteButton(
                    icon: Icons.skip_previous_rounded,
                    tooltip: 'Précédent',
                    onPressed: hasPrevious ? () => onSkip(false) : null,
                  ),
                  if (!live)
                    RemoteButton(
                      icon: Icons.replay_10_rounded,
                      tooltip: '-10 s',
                      onPressed: () => onSeek(RemoteSeekIntent.backward.offset),
                    ),
                  RemoteButton(
                    icon: playing
                        ? Icons.pause_rounded
                        : Icons.play_arrow_rounded,
                    tooltip: playing ? 'Pause' : 'Lecture',
                    focusNode: live ? entryFocus : null,
                    onPressed: onTogglePlay,
                  ),
                  if (!live)
                    RemoteButton(
                      icon: Icons.forward_10_rounded,
                      tooltip: '+10 s',
                      onPressed: () => onSeek(RemoteSeekIntent.forward.offset),
                    ),
                  RemoteButton(
                    icon: Icons.skip_next_rounded,
                    tooltip: 'Suivant',
                    onPressed: hasNext ? () => onSkip(true) : null,
                  ),
                  RemoteButton(
                    label: rateLabel,
                    tooltip: 'Vitesse',
                    onPressed: onRate,
                  ),
                  RemoteButton(
                    icon: Icons.subtitles_rounded,
                    tooltip: 'Sous-titres',
                    onPressed: hasSubtitles ? onSubtitles : null,
                  ),
                  RemoteButton(
                    icon: Icons.av_timer_rounded,
                    label: audioDelayLabel,
                    tooltip: 'Décalage audio',
                    onPressed: onAudioDelay,
                  ),
                  RemoteButton(
                    label: qualityLabel,
                    tooltip: qualityTooltip,
                    onPressed: onQuality,
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text(
                live
                    ? 'OK lecture/pause · ◀ ▶ options'
                    : '◀ ▶ reculer/avancer de 10 s · OK lecture/pause · ▼ options',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: Colors.white70,
                ),
              ),
            ],
          ),
        ),
      ),
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
  });

  final IconData? icon;
  final String? label;
  final String tooltip;

  /// `null` disables the button: it is skipped by focus traversal.
  final VoidCallback? onPressed;

  /// Supplied only for the element the overlay focuses on reveal.
  final FocusNode? focusNode;

  @override
  State<RemoteButton> createState() => _RemoteButtonState();
}

class _RemoteButtonState extends State<RemoteButton> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final enabled = widget.onPressed != null;
    final foreground = enabled
        ? (_focused ? colors.onPrimary : Colors.white)
        : Colors.white38;
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
        },
        child: GestureDetector(
          onTap: widget.onPressed,
          child: Tooltip(
            message: widget.tooltip,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: _focused ? colors.primary : Colors.white12,
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
