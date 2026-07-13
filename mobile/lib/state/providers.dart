/// Riverpod providers wiring the API client to the screens.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../api/models.dart';
import 'settings.dart';

final apiProvider = Provider<YtuiApi>((ref) {
  final settings = ref.watch(settingsProvider);
  return YtuiApi(baseUrl: settings.url, token: settings.token);
});

final feedProvider =
    AsyncNotifierProvider<FeedNotifier, FeedResult>(FeedNotifier.new);

class FeedNotifier extends AsyncNotifier<FeedResult> {
  @override
  Future<FeedResult> build() => ref.watch(apiProvider).feed();

  Future<void> refresh() async {
    state = await AsyncValue.guard(() => ref.read(apiProvider).feed(refresh: true));
  }
}

final searchProvider = FutureProvider.autoDispose
    .family<List<Video>, (String, String)>((ref, arg) =>
        ref.watch(apiProvider).search(arg.$1, source: arg.$2));

final channelVideosProvider = FutureProvider.autoDispose
    .family<(List<Video>, String), (String, String)>((ref, arg) =>
        ref.watch(apiProvider).channelVideos(arg.$1, platform: arg.$2));

final ytPlaylistProvider = FutureProvider.autoDispose
    .family<(List<Video>, String), (String, String)>((ref, arg) =>
        ref.watch(apiProvider).playlistVideos(arg.$1, platform: arg.$2));

final videoDetailsProvider = FutureProvider.autoDispose
    .family<VideoDetails, (String, String)>((ref, arg) =>
        ref.watch(apiProvider).videoDetails(arg.$1, platform: arg.$2));

final relatedProvider = FutureProvider.autoDispose
    .family<List<Video>, (String, String)>((ref, arg) =>
        ref.watch(apiProvider).related(arg.$1, platform: arg.$2));

final commentsProvider = FutureProvider.autoDispose
    .family<CommentsPage, (String, String)>((ref, arg) =>
        ref.watch(apiProvider).videoComments(arg.$1, platform: arg.$2));

final historyProvider =
    FutureProvider.autoDispose((ref) => ref.watch(apiProvider).history());

final playlistsProvider =
    FutureProvider.autoDispose((ref) => ref.watch(apiProvider).playlists());

final playlistItemsProvider = FutureProvider.autoDispose
    .family<List<PlaylistItem>, int>((ref, id) => ref.watch(apiProvider).playlistItems(id));

final channelsProvider =
    FutureProvider.autoDispose((ref) => ref.watch(apiProvider).channels());

final livesProvider = FutureProvider.autoDispose<List<LiveItem>>((ref) async {
  try {
    return await ref.watch(apiProvider).lives();
  } on ApiException {
    return [];
  }
});

/// Watched ids for the ✓ markers, updated optimistically on play.
final watchedIdsProvider =
    AsyncNotifierProvider<WatchedIdsNotifier, Set<String>>(WatchedIdsNotifier.new);

class WatchedIdsNotifier extends AsyncNotifier<Set<String>> {
  @override
  Future<Set<String>> build() async {
    try {
      return await ref.watch(apiProvider).watchedIds();
    } on ApiException {
      return {};
    }
  }

  void markWatched(String videoId) {
    final current = state.valueOrNull ?? {};
    state = AsyncValue.data({...current, videoId});
  }
}
