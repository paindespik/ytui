/// Local notifications for live streams (shared by foreground poll and
/// the workmanager background task).
library;

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';
import '../state/settings.dart';

const _channelId = 'ytui_lives';
const _newVideoChannelId = 'ytui_new_videos';

int _notifId(String s) {
  var h = 0;
  for (final c in s.codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  return h;
}

final FlutterLocalNotificationsPlugin notificationsPlugin =
    FlutterLocalNotificationsPlugin();

/// Plugin init only — safe in the background isolate (no Activity needed).
Future<void> initNotifications() async {
  const settings = InitializationSettings(
    android: AndroidInitializationSettings('@mipmap/ic_launcher'),
  );
  await notificationsPlugin.initialize(settings);
}

/// Runtime permission prompt — requires a foreground Activity.
/// NEVER call from the workmanager background isolate (NPE on Android).
Future<void> requestNotificationsPermission() async {
  await notificationsPlugin
      .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>()
      ?.requestNotificationsPermission();
}

Future<void> showLiveNotification(String videoId, String title, String channel,
    {String platform = 'youtube'}) {
  final icon = platform == 'twitch' ? '\u{1F7E3}' : '\u{1F534}';
  final summary = platform == 'twitch'
      ? '$icon $channel is live on Twitch'
      : '$icon $channel is live';
  return notificationsPlugin.show(
    _notifId(videoId),
    summary,
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
      await showLiveNotification(live.video.videoId, live.video.title,
          live.video.channelTitle,
          platform: live.video.platform);
    }
  }
  // Keep only ids still live so a channel going live again re-notifies.
  await prefs.setStringList(kSeenLiveIdsKey, currentIds.toList());
}

/// Separate Android notification channel for new videos.
Future<void> _ensureNewVideoChannel() async {
  await notificationsPlugin
      .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>()
      ?.createNotificationChannel(
    AndroidNotificationChannel(
      _newVideoChannelId,
      'New videos',
      description: 'New videos from followed channels',
      importance: Importance.high,
    ),
  );
}

Future<void> showNewVideoNotification(
    String videoId, String title, String channel) async {
  await _ensureNewVideoChannel();
  return notificationsPlugin.show(
    _notifId(videoId),
    channel,
    title,
    const NotificationDetails(
      android: AndroidNotificationDetails(
        _newVideoChannelId,
        'New videos',
        channelDescription: 'New videos from followed channels',
        importance: Importance.high,
        priority: Priority.high,
      ),
    ),
  );
}

/// Poll /api/feed and notify about new videos not seen before.
/// On first run (no seed), silently record all current video ids without
/// notifying — avoids a notification storm on fresh install.
/// Seen ids are pruned to current feed on each run (bounded storage).
Future<void> checkNewVideosAndNotify(SharedPreferences prefs) async {
  final url = prefs.getString(kServerUrlKey) ?? '';
  final token = prefs.getString(kServerTokenKey) ?? '';
  if (url.isEmpty || token.isEmpty) return;

  final api = YtuiApi(baseUrl: url, token: token);
  final feed = await api.feed(refresh: true);
  final allIds = feed.videos.map((v) => v.videoId).toList();

  final seen = (prefs.getStringList(kSeenVideoIdsKey) ?? []).toSet();
  final seeded = prefs.getBool(kFirstFeedSeedKey) ?? false;

  if (!seeded) {
    // First run: seed silently, no notifications.
    await prefs.setStringList(kSeenVideoIdsKey, allIds);
    await prefs.setBool(kFirstFeedSeedKey, true);
    return;
  }

  // Notify for videos not in seen set.
  for (final v in feed.videos) {
    if (!seen.contains(v.videoId)) {
      await showNewVideoNotification(
          v.videoId, v.title, v.channelTitle);
    }
  }

  // Union with seen set (don't drop ids from failed channels) and cap at 500.
  final merged = {...seen, ...allIds}.toList();
  final capped = merged.length > 500 ? merged.sublist(merged.length - 500) : merged;
  await prefs.setStringList(kSeenVideoIdsKey, capped);
}
