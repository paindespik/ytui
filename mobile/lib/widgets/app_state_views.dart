/// Shared loading / error / empty state widgets used across list screens.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';

/// Centered progress spinner for `AsyncValue.loading` branches.
class AppLoading extends StatelessWidget {
  const AppLoading({super.key});

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(24),
        child: CircularProgressIndicator(),
      ),
    );
  }
}

/// Error state with a friendly message and an optional retry button.
///
/// Pass a raw error to [error] to get a user-facing message
/// (unreachable server is called out explicitly); or pass [message] directly.
class AppError extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;

  const AppError({super.key, required this.message, this.onRetry});

  AppError.from(Object error, {super.key, this.onRetry})
      : message = messageFor(error);

  /// Human-readable message for a thrown error.
  static String messageFor(Object error) {
    if (error is ApiException) {
      return error.statusCode == 0
          ? 'Server unreachable — check settings'
          : 'API error ${error.statusCode}: ${error.detail}';
    }
    return '$error';
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.error_outline,
              size: 56,
              color: colors.error,
            ),
            const SizedBox(height: 16),
            Text(
              message,
              textAlign: TextAlign.center,
              style: textTheme.bodyMedium?.copyWith(
                color: colors.onSurfaceVariant,
              ),
            ),
            if (onRetry != null) ...[
              const SizedBox(height: 24),
              FilledButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Empty-state placeholder for successful-but-empty results.
class AppEmpty extends StatelessWidget {
  final String message;
  final IconData icon;

  const AppEmpty({super.key, required this.message, this.icon = Icons.inbox});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;

    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              icon,
              size: 48,
              color: colors.onSurfaceVariant,
            ),
            const SizedBox(height: 16),
            Text(
              message,
              textAlign: TextAlign.center,
              style: textTheme.bodyMedium?.copyWith(
                color: colors.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
