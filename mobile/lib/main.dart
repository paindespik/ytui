import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:media_kit/media_kit.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'screens/channel.dart';
import 'screens/detail.dart';
import 'screens/history.dart';
import 'screens/home_feed.dart';
import 'screens/local_playlists.dart';
import 'screens/player.dart';
import 'screens/playlist_yt.dart';
import 'screens/search.dart';
import 'screens/settings.dart';
import 'services/background.dart';
import 'services/background_playback.dart';
import 'services/live_poll.dart';
import 'services/notifications.dart';
import 'state/settings.dart';
import 'theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  MediaKit.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  await initNotifications();
  await requestNotificationsPermission();
  await initForegroundTask();
  await registerBackgroundLiveCheck();
  final isTv = await _detectTv();
  if (isTv) {
    // Android defaults to the touch highlight mode, which hides every focus
    // ring: on a remote-only device the selection must always be visible.
    FocusManager.instance.highlightStrategy =
        FocusHighlightStrategy.alwaysTraditional;
  }

  runApp(
    ProviderScope(
      overrides: [
        sharedPreferencesProvider.overrideWithValue(prefs),
        isTvProvider.overrideWithValue(isTv),
      ],
      child: const YtuiApp(),
    ),
  );
}

/// Android TV / projector, reported by [MainActivity]: playback is then driven
/// by the remote instead of the (absent) touchscreen.
Future<bool> _detectTv() async {
  const channel = MethodChannel('dev.ytui.app/device');
  try {
    return await channel.invokeMethod<bool>('isTv') ?? false;
  } catch (_) {
    return false;
  }
}

final _router = GoRouter(
  routes: [
    GoRoute(path: '/', builder: (_, __) => const HomeFeedScreen()),
    GoRoute(path: '/search', builder: (_, __) => const SearchScreen()),
    GoRoute(path: '/player', builder: (_, __) => const PlayerScreen()),
    GoRoute(path: '/history', builder: (_, __) => const HistoryScreen()),
    GoRoute(path: '/settings', builder: (_, __) => const SettingsScreen()),
    GoRoute(
      path: '/playlists',
      builder: (_, __) => const LocalPlaylistsScreen(),
    ),
    GoRoute(
      path: '/playlists/:id',
      builder: (_, state) => LocalPlaylistScreen(
        playlistId: int.parse(state.pathParameters['id']!),
        name: state.uri.queryParameters['name'] ?? '',
      ),
    ),
    GoRoute(
      path: '/channel/:id',
      builder: (_, state) => ChannelScreen(
        channelId: state.pathParameters['id']!,
        platform: state.uri.queryParameters['platform'] ?? 'youtube',
        title: state.uri.queryParameters['title'] ?? '',
      ),
    ),
    GoRoute(
      path: '/ytplaylist/:id',
      builder: (_, state) => YtPlaylistScreen(
        playlistId: state.pathParameters['id']!,
        platform: state.uri.queryParameters['platform'] ?? 'youtube',
      ),
    ),
    GoRoute(
      path: '/detail/:id',
      builder: (_, state) => DetailScreen(
        videoId: state.pathParameters['id']!,
        platform: state.uri.queryParameters['platform'] ?? 'youtube',
      ),
    ),
  ],
);

class YtuiApp extends ConsumerWidget {
  const YtuiApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsProvider);

    if (settings.isConfigured) {
      ref.read(livePollerProvider).start();
    }

    final theme = buildAppTheme();

    if (!settings.isConfigured) {
      // First launch: force the settings screen until the server is set up.
      return MaterialApp(
        title: 'ytui',
        theme: theme,
        home: const SettingsScreen(firstLaunch: true),
      );
    }

    return MaterialApp.router(
      title: 'ytui',
      theme: theme,
      routerConfig: _router,
    );
  }
}
