/// Small, dependency-free formatting helpers for display strings.
library;

/// Formats a count compactly: 1234 -> "1.2K", 5348012 -> "5.3M".
///
/// Keeps values under 1000 as-is. Uses one decimal for K/M/B, dropping a
/// trailing ".0". Negative values are passed through with their sign.
String compactCount(int n) {
  if (n < 0) return '-${compactCount(-n)}';
  if (n < 1000) return '$n';
  const units = [
    (1000000000, 'B'),
    (1000000, 'M'),
    (1000, 'K'),
  ];
  for (final (threshold, suffix) in units) {
    if (n >= threshold) {
      final value = n / threshold;
      final text = value >= 100
          ? value.round().toString()
          : value.toStringAsFixed(1);
      return '${text.endsWith('.0') ? text.substring(0, text.length - 2) : text}$suffix';
    }
  }
  return '$n';
}

/// Pluralizes [word] based on [count]: `plural(1, 'item') == '1 item'`,
/// `plural(3, 'item') == '3 items'`. Pass [plural] for irregular forms.
String pluralize(int count, String word, {String? plural}) {
  final label = count == 1 ? word : (plural ?? '${word}s');
  return '$count $label';
}

/// Formats a playback duration as a clock: `m:ss`, or `h:mm:ss` past an hour.
/// Negative values (a seek preview clamped below zero) render as `0:00`.
String formatClock(Duration value) {
  final total = value.inSeconds < 0 ? 0 : value.inSeconds;
  final seconds = (total % 60).toString().padLeft(2, '0');
  final minutes = (total ~/ 60) % 60;
  final hours = total ~/ 3600;
  if (hours > 0) {
    return '$hours:${minutes.toString().padLeft(2, '0')}:$seconds';
  }
  return '$minutes:$seconds';
}
