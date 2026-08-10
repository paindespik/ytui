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

final suggestionsProvider = AsyncNotifierProvider<SuggestionsNotifier, FeedResult>(
    SuggestionsNotifier.new);

class SuggestionsNotifier extends AsyncNotifier<FeedResult> {
  @override
  Future<FeedResult> build() => ref.watch(apiProvider).suggestions();

  Future<void> refresh() async {
    state = await AsyncValue.guard(
        () => ref.read(apiProvider).suggestions(refresh: true));
  }
}

final searchProvider = FutureProvider.autoDispose
    .family<List<Video>, (String, String)>((ref, arg) =>
        ref.watch(apiProvider).search(arg.$1, source: arg.$2));

/// Paginated channel listing: page 1 on build, older pages via [loadMore].
class ChannelVideos {
  final List<Video> videos;
  final String title;
  final bool hasMore;
  final bool loadingMore;

  const ChannelVideos({
    required this.videos,
    required this.title,
    required this.hasMore,
    this.loadingMore = false,
  });

  ChannelVideos copyWith({
    List<Video>? videos,
    String? title,
    bool? hasMore,
    bool? loadingMore,
  }) =>
      ChannelVideos(
        videos: videos ?? this.videos,
        title: title ?? this.title,
        hasMore: hasMore ?? this.hasMore,
        loadingMore: loadingMore ?? this.loadingMore,
      );
}

const kChannelPageSize = 50;

/// Family key: (channelId, platform, query). An empty query lists the whole
/// channel; a non-empty one searches within it.
final channelVideosProvider = AsyncNotifierProvider.autoDispose
    .family<ChannelVideosNotifier, ChannelVideos, (String, String, String)>(
        ChannelVideosNotifier.new);

class ChannelVideosNotifier extends AutoDisposeFamilyAsyncNotifier<ChannelVideos,
    (String, String, String)> {
  String? get _query => arg.$3.isEmpty ? null : arg.$3;

  @override
  Future<ChannelVideos> build((String, String, String) arg) async {
    final (videos, title, hasMore) = await ref.watch(apiProvider).channelVideos(
        arg.$1,
        platform: arg.$2,
        limit: kChannelPageSize,
        q: _query);
    return ChannelVideos(videos: videos, title: title, hasMore: hasMore);
  }

  /// Appends the next page, keeping the loaded videos on failure.
  /// Returns the error when the page could not be fetched, else null.
  Future<Object?> loadMore() async {
    final current = state.valueOrNull;
    if (current == null || !current.hasMore || current.loadingMore) return null;
    state = AsyncData(current.copyWith(loadingMore: true));
    try {
      final (videos, _, hasMore) = await ref.read(apiProvider).channelVideos(
            arg.$1,
            platform: arg.$2,
            limit: kChannelPageSize,
            offset: current.videos.length,
            q: _query,
          );
      state = AsyncData(current.copyWith(
        videos: [...current.videos, ...videos],
        hasMore: hasMore,
        loadingMore: false,
      ));
      return null;
    } catch (e) {
      state = AsyncData(current.copyWith(loadingMore: false));
      return e;
    }
  }
}

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
