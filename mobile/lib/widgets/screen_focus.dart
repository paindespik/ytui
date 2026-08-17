/// Initial D-pad focus for screens.
///
/// A freshly opened screen has no focused widget, and the first D-pad press
/// then lands on the first focusable node in tree order — the AppBar's Back
/// button — so DOWN+OK exits the screen the user just opened. [ScreenFocus]
/// wraps the screen *body* (never the AppBar) and moves the focus to the
/// first focusable widget of the body as soon as one exists.
library;

import 'dart:async';

import 'package:flutter/material.dart';

class ScreenFocus extends StatefulWidget {
  /// The screen body (the feed list, the "Play all" button, ...). The AppBar
  /// must stay outside the widget: its Back button must remain reachable.
  final Widget child;

  const ScreenFocus({super.key, required this.child});

  @override
  State<ScreenFocus> createState() => _ScreenFocusState();
}

class _ScreenFocusState extends State<ScreenFocus> {
  final FocusNode _scope = FocusNode(debugLabel: 'screen focus');

  /// Bodies usually start as a spinner and the first focusable widget only
  /// appears when the data arrives, so the focus request is retried for a
  /// few seconds instead of firing once.
  static const _maxAttempts = 24; // 24 × 250 ms = 6 s
  static const _retryDelay = Duration(milliseconds: 250);

  Timer? _retry;
  int _attempts = 0;

  @override
  void initState() {
    super.initState();
    _scope.skipTraversal = true; // the wrapper never takes the highlight ring
    _schedule(Duration.zero);
  }

  @override
  void dispose() {
    _retry?.cancel();
    _scope.dispose();
    super.dispose();
  }

  bool get _bodyHasFocus =>
      _scope.hasFocus || _scope.descendants.any((n) => n.hasFocus);

  void _schedule(Duration delay) {
    _retry?.cancel();
    _retry = Timer(delay, _tryFocus);
  }

  void _retryLater() {
    if (_attempts < _maxAttempts) {
      _attempts += 1;
      _schedule(_retryDelay);
    }
  }

  void _tryFocus() {
    if (!mounted || _scope.context == null) return;
    if (_bodyHasFocus) return;

    // Never steal focus the user already moved: only take it when the
    // primary focus sits at/above the screen root (freshly pushed route, or
    // nothing focused at app launch).
    final primary = FocusManager.instance.primaryFocus;
    if (primary != null &&
        !identical(primary, _scope) &&
        !_scope.ancestors.any((a) => identical(a, primary))) {
      return;
    }

    FocusNode? target;
    for (final node in _scope.descendants) {
      if (node.canRequestFocus) {
        target = node;
        break;
      }
    }
    if (target == null) {
      _retryLater();
      return;
    }
    target.requestFocus();
    // requestFocus reports no result: verify on the next frame that the
    // focus actually moved, retry if it did not.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _bodyHasFocus) return;
      _retryLater();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Focus(focusNode: _scope, child: widget.child);
  }
}
