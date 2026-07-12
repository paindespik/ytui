/// Local notifications for live streams (shared by foreground poll and
/// the workmanager background task).
library;

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';
import '../state/settings.dart';

const _channelId = 'ytui_lives';

int _notifId(String s) {
  var h = 0;
  for (final c in s.codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  return h;
}

final FlutterLocalNotificationsPlugin notificationsPlugin =
    FlutterLocalNotificationsPlugin();

Future<void> initNotifications() async {
  const settings = InitializationSettings(
    android: AndroidInitializationSettings('@mipmap/ic_launcher'),
  );
  await notificationsPlugin.initialize(settings);
  await notificationsPlugin
      .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>()
      ?.requestNotificationsPermission();
}

Future<void> showLiveNotification(String videoId, String title, String channel) {
  return notificationsPlugin.show(
    _notifId(videoId),
    '🔴 $channel is live',
    title,
    const NotificationDetails(
      android: AndroidNotificationDetails(
        _channelId,
        'Live streams',
        channelDescription: 'A followed channel went live',
        importance: Importance.high,
        priority: Priority.high,
      ),
    ),
  );
}

/// Poll /api/lives and notify about lives not seen before.
/// Seen ids are persisted in SharedPreferences so the foreground poll and
/// the background task share the same dedup state.
Future<void> checkLivesAndNotify(SharedPreferences prefs) async {
  final url = prefs.getString(kServerUrlKey) ?? '';
  final token = prefs.getString(kServerTokenKey) ?? '';
  if (url.isEmpty || token.isEmpty) return;

  final api = YtuiApi(baseUrl: url, token: token);
  final lives = await api.lives();
  final seen = (prefs.getStringList(kSeenLiveIdsKey) ?? []).toSet();
  final currentIds = <String>{};
  for (final live in lives) {
    currentIds.add(live.video.videoId);
    if (!seen.contains(live.video.videoId)) {
      await showLiveNotification(
          live.video.videoId, live.video.title, live.video.channelTitle);
    }
  }
  // Keep only ids still live so a channel going live again re-notifies.
  await prefs.setStringList(kSeenLiveIdsKey, currentIds.toList());
}
