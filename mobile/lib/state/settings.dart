/// Server settings (URL + token) persisted in SharedPreferences.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

const kServerUrlKey = 'server_url';
const kServerTokenKey = 'server_token';
const kSeenLiveIdsKey = 'seen_live_ids';
const kSeenVideoIdsKey = 'seen_video_ids';
const kFirstFeedSeedKey = 'first_feed_seed';

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
