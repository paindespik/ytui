/// Responsive layout helpers so wide screens (tablets, TVs, projectors)
/// don't stretch single-column content edge-to-edge.
library;

import 'package:flutter/material.dart';

/// Width past which content is centered inside a bounded column instead of
/// filling the whole viewport.
const double kWideBreakpoint = 720;

/// Comfortable maximum content width for single-column lists/detail views.
/// Kept well below typical wide-screen widths (e.g. a 960dp projector) so the
/// centered column shows clear, balanced margins instead of near-full-bleed.
const double kMaxContentWidth = 640;

/// Centers [child] within a max-width column on wide screens.
///
/// Below [kWideBreakpoint] this is a no-op (child fills the width). Above it,
/// the child is constrained to [maxWidth] and horizontally centered, killing
/// the "60% empty space" look on projectors/TVs.
///
/// Wrap the *scrollable* (ListView/SingleChildScrollView) so the scrollbar and
/// content share the same bounded column.
class ResponsiveCenter extends StatelessWidget {
  final Widget child;
  final double maxWidth;

  const ResponsiveCenter({
    super.key,
    required this.child,
    this.maxWidth = kMaxContentWidth,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: child,
      ),
    );
  }
}

/// True when the current screen is wide enough to warrant bounded content.
bool isWideScreen(BuildContext context) =>
    MediaQuery.sizeOf(context).width >= kWideBreakpoint;
