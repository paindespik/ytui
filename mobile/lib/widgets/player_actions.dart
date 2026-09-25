/// Touch action bar under the portrait player: one horizontally scrollable
/// row of pill buttons (toggles and option pickers).
///
/// A fixed row stopped fitting long ago — the Fairphone is 372 dp wide and
/// the old centred row needed ~390 dp, so its last button was clipped. The
/// bar scrolls sideways under the finger instead, and its edges fade out so
/// the cut-off pill reads as "there is more this way".
library;

import 'package:flutter/material.dart';

import '../theme.dart';

/// One pill of the [PlayerActionBar].
class PlayerAction {
  const PlayerAction({
    required this.icon,
    required this.label,
    required this.tooltip,
    required this.onPressed,
    this.selected = false,
    this.toggle = false,
  });

  final IconData icon;
  final String label;

  /// Long-press hint and accessibility label: says what the pill does, or —
  /// for a toggle — the state it is in.
  final String tooltip;

  /// `null` disables the pill (e.g. subtitles when the video has none).
  final VoidCallback? onPressed;

  /// Highlighted: a toggle that is on, or a picker away from its default
  /// (speed ≠ 1×, a sleep timer running...).
  final bool selected;

  /// On/off switch rather than a picker: exposed as such to accessibility.
  final bool toggle;
}

class PlayerActionBar extends StatelessWidget {
  const PlayerActionBar({super.key, required this.actions, this.controller});

  final List<PlayerAction> actions;
  final ScrollController? controller;

  /// Pill height; the bar adds nothing around it vertically.
  static const double height = 40;

  /// Width of the fade on each edge.
  static const double _fade = 16;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      child: ShaderMask(
        // dstIn keeps the pills where the mask is opaque: transparent ends
        // fade them into the background on both sides.
        blendMode: BlendMode.dstIn,
        shaderCallback: (bounds) {
          final stop = bounds.width <= 0 ? 0.0 : (_fade / bounds.width);
          return LinearGradient(
            colors: const [
              Color(0x00000000),
              Color(0xFF000000),
              Color(0xFF000000),
              Color(0x00000000),
            ],
            stops: [0, stop, 1 - stop, 1],
          ).createShader(bounds);
        },
        child: ListView.separated(
          controller: controller,
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: kGutter),
          itemCount: actions.length,
          separatorBuilder: (_, __) => const SizedBox(width: 8),
          itemBuilder: (context, i) => _ActionPill(action: actions[i]),
        ),
      ),
    );
  }
}

class _ActionPill extends StatelessWidget {
  const _ActionPill({required this.action});

  final PlayerAction action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final enabled = action.onPressed != null;
    final background = action.selected && enabled
        ? colors.primaryContainer
        : colors.surfaceContainerHighest;
    final foreground = !enabled
        ? colors.onSurface.withValues(alpha: 0.38)
        : action.selected
            ? colors.onPrimaryContainer
            : colors.onSurface;
    return Semantics(
      button: true,
      enabled: enabled,
      toggled: action.toggle ? action.selected : null,
      label: action.tooltip,
      // The pill's own node replaces the InkWell/Tooltip ones, so it must
      // carry the tap itself.
      onTap: action.onPressed,
      excludeSemantics: true,
      child: Tooltip(
        message: action.tooltip,
        child: Material(
          color: background,
          shape: const StadiumBorder(),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: action.onPressed,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(action.icon, size: 18, color: foreground),
                  const SizedBox(width: 6),
                  Text(
                    action.label,
                    style: theme.textTheme.labelLarge?.copyWith(
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
