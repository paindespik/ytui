/// Local notifications for lives and new videos, shared by the foreground
/// poll (main isolate) and the workmanager background task (its own isolate).
///
/// Both isolates keep their own [SharedPreferences] cache, so every check
/// reloads it from disk first: with a stale cache the foreground poll never
/// saw what the background task had just notified and notified it again.
library;

import 'dart:collection';
import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../state/settings.dart';

const _liveChannelId = 'ytui_lives';
const _newVideoChannelId = 'ytui_new_videos';

/// Bundles a batch of new-video notifications under one summary.
const _newVideoGroup = 'dev.ytui.app.NEW_VIDEOS';

/// Fixed id of the batch summary (per-video ids are string hashes).
const _newVideoSummaryId = 0x7ffffff0;

/// Monochrome status-bar icon (res/drawable/ic_stat_ytui.xml, kept from
/// resource shrinking by res/raw/keep.xml).
const _smallIcon = 'ic_stat_ytui';

/// A live stays "seen" this long after it was last listed. The backend's live
/// list blinks when an upstream check fails, and forgetting a live as soon as
/// one poll missed it notified it again when it came back. A new broadcast has
/// a new id anyway, so the grace never hides a real one.
const kLiveSeenGrace = Duration(minutes: 45);

/// Videos published longer ago than this are recorded as seen without a
/// notification: following a channel brings its whole back catalogue into the
/// feed at once, and a phone out of Doze after days has nothing "new" to say
/// about last week.
const kNotifyMaxAge = Duration(days: 3);

/// At most this many new-video notifications per check; the group summary
/// counts the rest.
const kMaxVideoNotifications = 8;

/// Seen ids that left the feed (a channel failing for a while, RSS rolling
/// over) are remembered up to this many, newest last.
const kStaleSeenCap = 500;

int _notifId(String s) {
  var h = 0;
  for (final c in s.codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  // Never collide with the batch summary.
  return h == _newVideoSummaryId ? h - 1 : h;
}

final FlutterLocalNotificationsPlugin notificationsPlugin =
    FlutterLocalNotificationsPlugin();

/// Plugin init — safe in the background isolate (no Activity needed). [onTap]
/// receives the payload of a notification tapped while the app runs; a tap
/// that launched the app is read with [notificationLaunchPayload].
Future<void> initNotifications({void Function(String? payload)? onTap}) async {
  Future<void> init(String icon) => notificationsPlugin.initialize(
        InitializationSettings(android: AndroidInitializationSettings(icon)),
        onDidReceiveNotificationResponse:
            onTap == null ? null : (response) => onTap(response.payload),
      );
  try {
    await init(_smallIcon);
  } catch (_) {
    // The plugin rejects an unknown icon at init: never let a missing
    // drawable take notifications (or the app start) down with it.
    await init('@mipmap/ic_launcher');
  }
}

/// Payload of the notification whose tap started the app, if any.
Future<String?> notificationLaunchPayload() async {
  final details = await notificationsPlugin.getNotificationAppLaunchDetails();
  if (details == null || !details.didNotificationLaunchApp) return null;
  return details.notificationResponse?.payload;
}

/// Runtime permission prompt — requires a foreground Activity.
/// NEVER call from the workmanager background isolate (NPE on Android).
Future<void> requestNotificationsPermission() async {
  await notificationsPlugin
      .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>()
      ?.requestNotificationsPermission();
}

/// Notification payload: the video to play when the notification is tapped.
String videoPayload(Video video) => jsonEncode(video.toJson());

/// Inverse of [videoPayload]; null for anything else (summary, old payloads).
Video? videoFromPayload(String? payload) {
  if (payload == null || payload.isEmpty) return null;
  try {
    final json = jsonDecode(payload);
    if (json is! Map<String, dynamic>) return null;
    final video = Video.fromJson(json);
    return video.videoId.isEmpty ? null : video;
  } catch (_) {
    return null;
  }
}

String _qualified(Video v) => '${v.platform}:${v.videoId}';

Future<void> showLiveNotification(Video video) {
  final (icon, where) = switch (video.platform) {
    'twitch' => ('\u{1F7E3}', ' sur Twitch'),
    'tiktok' => ('\u{1F3B5}', ' sur TikTok'),
    _ => ('\u{1F534}', ''),
  };
  final channel = video.channelTitle.isEmpty ? 'Une chaîne' : video.channelTitle;
  return notificationsPlugin.show(
    _notifId(video.videoId),
    '$icon $channel est en direct$where',
    video.title,
    const NotificationDetails(
      android: AndroidNotificationDetails(
        _liveChannelId,
        'Directs',
        channelDescription: 'Une chaîne suivie passe en direct',
        importance: Importance.high,
        priority: Priority.high,
      ),
    ),
    payload: videoPayload(video),
  );
}

/// Outcome of [reconcileLives]: the live keys to notify and the entries to
/// persist ("key\tlastSeenMs").
typedef LiveReconciliation = ({List<String> notify, List<String> seen});

/// Pure dedup step of [checkLivesAndNotify]. [stored] holds "key\tlastSeenMs"
/// entries (bare keys from older versions count as seen [now]); [current] the
/// platform-qualified keys listed live right now.
LiveReconciliation reconcileLives(
  List<String> stored,
  Iterable<String> current,
  DateTime now, {
  Duration grace = kLiveSeenGrace,
}) {
  final nowMs = now.millisecondsSinceEpoch;
  final lastSeen = <String, int>{};
  for (final entry in stored) {
    final tab = entry.lastIndexOf('\t');
    if (tab < 0) {
      lastSeen[entry] = nowMs;
    } else {
      lastSeen[entry.substring(0, tab)] =
          int.tryParse(entry.substring(tab + 1)) ?? nowMs;
    }
  }
  final notify = <String>[
    for (final key in LinkedHashSet<String>.of(current))
      if (!lastSeen.containsKey(key)) key,
  ];
  for (final key in current) {
    lastSeen[key] = nowMs;
  }
  // Pruned only now: a live still listed after a long Doze gap is refreshed
  // above before its old timestamp could expire it.
  lastSeen.removeWhere((_, ms) => nowMs - ms > grace.inMilliseconds);
  return (
    notify: notify,
    seen: [for (final e in lastSeen.entries) '${e.key}\t${e.value}'],
  );
}

/// Poll /api/lives and notify about lives not seen before.
Future<void> checkLivesAndNotify(SharedPreferences prefs, {DateTime? now}) async {
  await prefs.reload();
  final url = prefs.getString(kServerUrlKey) ?? '';
  final token = prefs.getString(kServerTokenKey) ?? '';
  if (url.isEmpty || token.isEmpty) return;

  final lives = await YtuiApi(baseUrl: url, token: token).lives();
  final byKey = {for (final live in lives) _qualified(live.video): live.video};
  final result = reconcileLives(
    prefs.getStringList(kSeenLiveIdsKey) ?? const [],
    byKey.keys,
    now ?? DateTime.now(),
  );
  // Persisted first: a notification failure must not re-notify next time.
  await prefs.setStringList(kSeenLiveIdsKey, result.seen);
  for (final key in result.notify) {
    await showLiveNotification(byKey[key]!);
  }
}

/// Pure: the seen-id list to persist after a feed poll. Every id of the
/// current feed is kept — dropping one re-notified it on the next poll, which
/// the old "keep the last 500 of seen ∪ feed" did as soon as the list filled
/// up, since the oldest entries were the feed's own. Ids that left the feed
/// follow, newest last, capped at [staleCap].
List<String> mergeSeenVideoIds(
  List<String> stored,
  List<String> current, {
  int staleCap = kStaleSeenCap,
}) {
  final currentSet = LinkedHashSet<String>.of(current);
  final stale = [
    for (final id in LinkedHashSet<String>.of(stored))
      if (!currentSet.contains(id)) id,
  ];
  final kept =
      stale.length > staleCap ? stale.sublist(stale.length - staleCap) : stale;
  return [...kept, ...currentSet];
}

/// Pure: the feed videos worth a notification — unseen, and recent.
List<Video> videosToNotify(
  List<Video> feed,
  Set<String> seen,
  DateTime now, {
  Duration maxAge = kNotifyMaxAge,
}) {
  return [
    for (final v in feed)
      if (v.kind == 'video' &&
          !seen.contains(_qualified(v)) &&
          (v.published == null || now.difference(v.published!) <= maxAge))
        v,
  ];
}

AndroidNotificationDetails _newVideoDetails({
  bool grouped = false,
  bool summary = false,
  DateTime? when,
  StyleInformation? style,
}) {
  return AndroidNotificationDetails(
    _newVideoChannelId,
    'Nouvelles vidéos',
    channelDescription: 'Nouvelles vidéos des chaînes suivies',
    importance: Importance.high,
    priority: Priority.high,
    groupKey: grouped ? _newVideoGroup : null,
    setAsGroupSummary: summary,
    // A batch rings once (the summary), not once per video.
    groupAlertBehavior:
        grouped ? GroupAlertBehavior.summary : GroupAlertBehavior.all,
    when: when?.millisecondsSinceEpoch,
    showWhen: when != null || summary,
    styleInformation: style,
  );
}

Future<void> showNewVideoNotification(Video video, {bool grouped = false}) {
  return notificationsPlugin.show(
    _notifId(video.videoId),
    video.channelTitle.isEmpty ? 'Nouvelle vidéo' : video.channelTitle,
    video.title,
    NotificationDetails(
      android: _newVideoDetails(grouped: grouped, when: video.published),
    ),
    payload: videoPayload(video),
  );
}

Future<void> _showNewVideoSummary(List<Video> videos) {
  final title = '${videos.length} nouvelles vidéos';
  return notificationsPlugin.show(
    _newVideoSummaryId,
    title,
    videos.map((v) => v.channelTitle).toSet().join(', '),
    NotificationDetails(
      android: _newVideoDetails(
        grouped: true,
        summary: true,
        style: InboxStyleInformation(
          [for (final v in videos.take(6)) '${v.channelTitle} · ${v.title}'],
          contentTitle: title,
          summaryText: videos.length > 6 ? '+ ${videos.length - 6} autres' : null,
        ),
      ),
    ),
  );
}

/// Poll /api/feed and notify about new videos not seen before.
/// On first run (no seed), silently record all current video ids without
/// notifying — avoids a notification storm on fresh install.
Future<void> checkNewVideosAndNotify(SharedPreferences prefs,
    {DateTime? now}) async {
  await prefs.reload();
  final url = prefs.getString(kServerUrlKey) ?? '';
  final token = prefs.getString(kServerTokenKey) ?? '';
  if (url.isEmpty || token.isEmpty) return;

  // No forced refresh: the backend re-fetches any channel older than its TTL
  // (15 min) on its own. Forcing it from every phone every 15 min re-ran the
  // RSS fetch AND a yt-dlp view-count scrape for every followed channel.
  final feed = await YtuiApi(baseUrl: url, token: token).feed();
  final allIds = feed.videos.map(_qualified).toList();
  final stored = prefs.getStringList(kSeenVideoIdsKey) ?? const <String>[];

  if (!(prefs.getBool(kFirstFeedSeedKey) ?? false)) {
    // First run: seed silently, no notifications.
    await prefs.setStringList(kSeenVideoIdsKey, mergeSeenVideoIds(const [], allIds));
    await prefs.setBool(kFirstFeedSeedKey, true);
    return;
  }

  final fresh = videosToNotify(feed.videos, stored.toSet(), now ?? DateTime.now());
  // Persisted before notifying: a failed post must not re-notify next time.
  await prefs.setStringList(kSeenVideoIdsKey, mergeSeenVideoIds(stored, allIds));
  if (fresh.isEmpty) return;
  final grouped = fresh.length > 1;
  // The feed is newest first: post the oldest first so the newest ends on top.
  for (final v in fresh.take(kMaxVideoNotifications).toList().reversed) {
    await showNewVideoNotification(v, grouped: grouped);
  }
  if (grouped) await _showNewVideoSummary(fresh);
}
