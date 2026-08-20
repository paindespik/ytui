/// Server settings (URL + token) persisted in SharedPreferences.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

const kServerUrlKey = 'server_url';
const kServerTokenKey = 'server_token';
const kSeenLiveIdsKey = 'seen_live_ids';
const kSeenVideoIdsKey = 'seen_video_ids';
const kFirstFeedSeedKey = 'first_feed_seed';
const kSponsorblockKey = 'sponsorblock_enabled';
const kAudioDelayKey = 'audio_delay_ms';
const kMaxHeightKey = 'max_height';
const kCellularMaxHeightKey = 'max_height_cellular';

class ServerSettings {
  final String url;
  final String token;

  const ServerSettings({this.url = '', this.token = ''});

  bool get isConfigured => url.isNotEmpty && token.isNotEmpty;
}

class SettingsNotifier extends Notifier<ServerSettings> {
  @override
  ServerSettings build() {
    final prefs = ref.watch(sharedPreferencesProvider);
    return ServerSettings(
      url: prefs.getString(kServerUrlKey) ?? '',
      token: prefs.getString(kServerTokenKey) ?? '',
    );
  }

  Future<void> save(String url, String token) async {
    final normalized = url.trim().replaceAll(RegExp(r'/+$'), '');
    final prefs = ref.read(sharedPreferencesProvider);
    await prefs.setString(kServerUrlKey, normalized);
    await prefs.setString(kServerTokenKey, token.trim());
    state = ServerSettings(url: normalized, token: token.trim());
  }
}

/// Overridden in main() with the real instance.
final sharedPreferencesProvider =
    Provider<SharedPreferences>((ref) => throw UnimplementedError());

final settingsProvider =
    NotifierProvider<SettingsNotifier, ServerSettings>(SettingsNotifier.new);


class SponsorblockNotifier extends Notifier<bool> {
  @override
  bool build() =>
      ref.watch(sharedPreferencesProvider).getBool(kSponsorblockKey) ?? true;

  Future<void> setEnabled(bool value) async {
    await ref.read(sharedPreferencesProvider).setBool(kSponsorblockKey, value);
    state = value;
  }
}

final sponsorblockProvider =
    NotifierProvider<SponsorblockNotifier, bool>(SponsorblockNotifier.new);

/// Audio/video sync offset applied to libmpv (`--audio-delay`), in
/// milliseconds: positive delays the sound, negative delays the picture.
/// Per-device on purpose — a projector's speakers/HDMI/Bluetooth path adds a
/// latency no player can measure, so it has to be dialled in by ear.
class AudioDelayNotifier extends Notifier<int> {
  @override
  int build() =>
      ref.watch(sharedPreferencesProvider).getInt(kAudioDelayKey) ?? 0;

  Future<void> setDelay(int milliseconds) async {
    await ref.read(sharedPreferencesProvider).setInt(kAudioDelayKey, milliseconds);
    state = milliseconds;
  }
}

final audioDelayProvider =
    NotifierProvider<AudioDelayNotifier, int>(AudioDelayNotifier.new);

/// Max video height requested from the backend (360…2160). Per-device: a phone
/// on mobile data and a TV on Ethernet do not want the same ceiling.
class MaxHeightNotifier extends Notifier<int> {
  @override
  int build() =>
      ref.watch(sharedPreferencesProvider).getInt(kMaxHeightKey) ?? 1440;

  Future<void> setHeight(int height) async {
    await ref.read(sharedPreferencesProvider).setInt(kMaxHeightKey, height);
    state = height;
  }
}

final maxHeightProvider =
    NotifierProvider<MaxHeightNotifier, int>(MaxHeightNotifier.new);

/// Ceiling used instead of [maxHeightProvider] when the phone is on mobile
/// data. 480p (~40 KB/s with audio) is what a weak cellular link sustains —
/// the Wi-Fi ceiling routinely picks tracks (60-160 KB/s) such a link cannot
/// carry, which showed as constant stutter.
class CellularMaxHeightNotifier extends Notifier<int> {
  @override
  int build() =>
      ref.watch(sharedPreferencesProvider).getInt(kCellularMaxHeightKey) ?? 480;

  Future<void> setHeight(int height) async {
    await ref
        .read(sharedPreferencesProvider)
        .setInt(kCellularMaxHeightKey, height);
    state = height;
  }
}

final cellularMaxHeightProvider =
    NotifierProvider<CellularMaxHeightNotifier, int>(
        CellularMaxHeightNotifier.new);

const kQualityLadder = [360, 480, 720, 1080, 1440, 2160];

/// True on Android TV / projectors (leanback, no touchscreen): playback is
/// driven by the remote instead of the touch overlay. Overridden in main()
/// with the value reported by the platform channel.
final isTvProvider = Provider<bool>((ref) => false);
