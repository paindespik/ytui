/// Foreground-service wrapper keeping audio/video playback alive when the
/// screen is off (thin layer over `flutter_foreground_task`).
library;

import 'package:flutter_foreground_task/flutter_foreground_task.dart';

const _channelId = 'ytui_playback';

/// Initializes the foreground task plugin. Call once, early in `main()`.
Future<void> initForegroundTask() async {
  FlutterForegroundTask.initCommunicationPort();
  FlutterForegroundTask.init(
    androidNotificationOptions: AndroidNotificationOptions(
      channelId: _channelId,
      channelName: 'Lecture',
      channelDescription: 'Lecture en cours en arrière-plan.',
      channelImportance: NotificationChannelImportance.LOW,
      priority: NotificationPriority.LOW,
      onlyAlertOnce: true,
    ),
    iosNotificationOptions: const IOSNotificationOptions(),
    foregroundTaskOptions: ForegroundTaskOptions(
      eventAction: ForegroundTaskEventAction.nothing(),
      allowWakeLock: true,
    ),
  );
}

/// Starts (or updates, if already running) the playback foreground service.
Future<void> startPlaybackService({required String title, required String text}) async {
  if (!await FlutterForegroundTask.isRunningService) {
    await FlutterForegroundTask.startService(notificationTitle: title, notificationText: text);
  } else {
    await FlutterForegroundTask.updateService(notificationTitle: title, notificationText: text);
  }
}

/// Updates the ongoing notification's title/text (e.g. on track change).
Future<void> updatePlaybackNotification({required String title, required String text}) async {
  if (await FlutterForegroundTask.isRunningService) {
    await FlutterForegroundTask.updateService(notificationTitle: title, notificationText: text);
  }
}

/// Stops the playback foreground service, if running.
Future<void> stopPlaybackService() async {
  if (await FlutterForegroundTask.isRunningService) {
    await FlutterForegroundTask.stopService();
  }
}
