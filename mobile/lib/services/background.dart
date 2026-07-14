/// Background live checks with workmanager (min interval 15 min on Android).
library;

import 'package:shared_preferences/shared_preferences.dart';
import 'package:flutter/foundation.dart';
import 'package:workmanager/workmanager.dart';

import 'notifications.dart';

const _liveCheckTask = 'ytui.liveCheck';

@pragma('vm:entry-point')
void callbackDispatcher() {
  Workmanager().executeTask((task, inputData) async {
    if (task == _liveCheckTask) {
      try {
        await initNotifications();
        final prefs = await SharedPreferences.getInstance();
        await checkLivesAndNotify(prefs);
        await checkNewVideosAndNotify(prefs);
      } catch (e) {
        debugPrint('ytui live check failed: $e');
      }
    }
    return true;
  });
}

Future<void> registerBackgroundLiveCheck() async {
  await Workmanager().initialize(callbackDispatcher);
  await Workmanager().registerPeriodicTask(
    _liveCheckTask,
    _liveCheckTask,
    frequency: const Duration(minutes: 15),
    existingWorkPolicy: ExistingPeriodicWorkPolicy.keep,
    constraints: Constraints(networkType: NetworkType.connected),
  );
}
