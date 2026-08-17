import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/format.dart';

void main() {
  group('compactCount', () {
    test('leaves values under 1000 untouched', () {
      expect(compactCount(0), '0');
      expect(compactCount(1), '1');
      expect(compactCount(999), '999');
    });

    test('formats thousands with one decimal, dropping .0', () {
      expect(compactCount(1000), '1K');
      expect(compactCount(1200), '1.2K');
      expect(compactCount(60106), '60.1K');
    });

    test('formats millions and billions', () {
      expect(compactCount(5348012), '5.3M');
      expect(compactCount(2000000000), '2B');
    });

    test('drops the decimal at or above 100 of a unit', () {
      expect(compactCount(150000), '150K');
    });

    test('preserves the sign of negatives', () {
      expect(compactCount(-1200), '-1.2K');
    });
  });

  group('pluralize', () {
    test('uses the singular for exactly one', () {
      expect(pluralize(1, 'item'), '1 item');
    });

    test('appends s for zero and many', () {
      expect(pluralize(0, 'item'), '0 items');
      expect(pluralize(3, 'item'), '3 items');
    });

    test('honors an irregular plural', () {
      expect(pluralize(2, 'entry', plural: 'entries'), '2 entries');
    });
  });

  group('formatClock', () {
    test('pads seconds and drops the hour under an hour', () {
      expect(formatClock(Duration.zero), '0:00');
      expect(formatClock(const Duration(seconds: 7)), '0:07');
      expect(formatClock(const Duration(minutes: 12, seconds: 34)), '12:34');
      expect(formatClock(const Duration(minutes: 59, seconds: 59)), '59:59');
    });

    test('shows hours with zero-padded minutes past an hour', () {
      expect(formatClock(const Duration(hours: 1)), '1:00:00');
      expect(
        formatClock(const Duration(hours: 2, minutes: 5, seconds: 9)),
        '2:05:09',
      );
    });

    test('clamps a negative seek preview to zero', () {
      expect(formatClock(const Duration(seconds: -10)), '0:00');
    });
  });

  group('formatDateFr', () {
    final now = DateTime(2026, 8, 17, 12);

    String isoOf(DateTime d) =>
        DateTime(d.year, d.month, d.day).toUtc().toIso8601String();

    test('relative days: today, yesterday, il y a N j', () {
      expect(formatDateFr(isoOf(now), now), 'aujourd’hui');
      expect(formatDateFr(isoOf(DateTime(2026, 8, 16)), now), 'hier');
      expect(formatDateFr(isoOf(DateTime(2026, 8, 10)), now), 'il y a 7 j');
      expect(formatDateFr(isoOf(DateTime(2026, 7, 18)), now), 'il y a 30 j');
    });

    test('full French date after a month', () {
      expect(formatDateFr(isoOf(DateTime(2026, 7, 17)), now), '17 juillet 2026');
      expect(formatDateFr(isoOf(DateTime(2025, 1, 3)), now), '3 janvier 2025');
      expect(formatDateFr(isoOf(DateTime(2024, 8, 14)), now), '14 août 2024');
    });

    test('unparseable input is returned unchanged', () {
      expect(formatDateFr('pas une date', now), 'pas une date');
    });
  });
}
