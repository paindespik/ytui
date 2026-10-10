/// Riverpod providers wiring the API client to the screens.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../api/models.dart';
import 'settings.dart';

final apiProvider = Provider<YtuiApi>((ref) {
  final settings = ref.watch(settingsProvider);
  final api = YtuiApi(baseUrl: settings.url, token: settings.token);
  // New server settings rebuild the client: release the old one's pooled
  // connections (requests still in flight are allowed to finish).
  ref.onDispose(api.dio.close);
  return api;
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

final musicProvider =
    AsyncNotifierProvider<MusicNotifier, MusicHome>(MusicNotifier.new);

class MusicNotifier extends AsyncNotifier<MusicHome> {
  @override
  Future<MusicHome> build() => ref.watch(apiProvider).music();

  Future<void> refresh() async {
    state =
        await AsyncValue.guard(() => ref.read(apiProvider).music(refresh: true));
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

/// Platforms whose channels list their playlists (Playlists tab).
const kChannelPlaylistPlatforms = {'youtube', 'bitchute'};

/// Platforms whose channel videos can be listed most viewed first.
const kChannelSortPlatforms = {'youtube', 'bitchute', 'crowdbunker', 'odysee'};

/// A paginated channel listing (videos or playlists): page 1 on build, the
/// next ones through [loadMore]. Subclasses only say how a page is fetched.
abstract class ChannelListingNotifier<A>
    extends AutoDisposeFamilyAsyncNotifier<ChannelVideos, A> {
  Future<(List<Video>, String, bool)> fetchPage(YtuiApi api, int offset);

  @override
  Future<ChannelVideos> build(A arg) async {
    final (videos, title, hasMore) = await fetchPage(ref.watch(apiProvider), 0);
    return ChannelVideos(videos: videos, title: title, hasMore: hasMore);
  }

  /// Appends the next page, keeping the loaded items on failure.
  /// Returns the error when the page could not be fetched, else null.
  Future<Object?> loadMore() async {
    final current = state.valueOrNull;
    if (current == null || !current.hasMore || current.loadingMore) return null;
    state = AsyncData(current.copyWith(loadingMore: true));
    try {
      final (videos, _, hasMore) =
          await fetchPage(ref.read(apiProvider), current.videos.length);
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

/// Family key: (channelId, platform, query, sort). An empty query lists the
/// whole channel, ordered by sort ('date': newest first, 'views': most
/// viewed first); a non-empty one searches within it, in the search's own
/// order (callers pass 'date' then).
final channelVideosProvider = AsyncNotifierProvider.autoDispose.family<
    ChannelVideosNotifier,
    ChannelVideos,
    (String, String, String, String)>(ChannelVideosNotifier.new);

class ChannelVideosNotifier
    extends ChannelListingNotifier<(String, String, String, String)> {
  @override
  Future<(List<Video>, String, bool)> fetchPage(YtuiApi api, int offset) =>
      api.channelVideos(
        arg.$1,
        platform: arg.$2,
        limit: kChannelPageSize,
        offset: offset,
        q: arg.$3.isEmpty ? null : arg.$3,
        sort: arg.$3.isEmpty ? arg.$4 : 'date',
      );
}

/// Family key: (channelId, platform). Only for [kChannelPlaylistPlatforms].
final channelPlaylistsProvider = AsyncNotifierProvider.autoDispose
    .family<ChannelPlaylistsNotifier, ChannelVideos, (String, String)>(
        ChannelPlaylistsNotifier.new);

class ChannelPlaylistsNotifier extends ChannelListingNotifier<(String, String)> {
  @override
  Future<(List<Video>, String, bool)> fetchPage(YtuiApi api, int offset) =>
      api.channelPlaylists(
        arg.$1,
        platform: arg.$2,
        limit: kChannelPageSize,
        offset: offset,
      );
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

/// Channel id resolved on demand for videos that do not carry one (flat
/// playlist extraction never fills it). Fails soft: a null result degrades
/// the actions sheet to the no-channel variant instead of erroring.
/// Plain (non-autoDispose) on purpose: it is a session-long in-memory cache
/// — one short string per opened actions sheet.
/// Family key: (platform, videoId).
final resolvedChannelProvider = FutureProvider
    .family<String?, (String, String)>((ref, arg) async {
  try {
    final details =
        await ref.read(apiProvider).videoDetails(arg.$2, platform: arg.$1);
    return details.channelId.isEmpty ? null : details.channelId;
  } catch (_) {
    return null;
  }
});

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
/// Ids are platform-qualified: '{platform}:{videoId}'.
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

  void markWatched(String qualifiedId) {
    final current = state.valueOrNull ?? {};
    state = AsyncValue.data({...current, qualifiedId});
  }
}
