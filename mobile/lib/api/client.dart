/// HTTP client for the ytui backend (one method per endpoint).
library;

import 'package:dio/dio.dart';

import 'models.dart';

class ApiException implements Exception {
  final int statusCode; // 0 = server unreachable
  final String detail;

  const ApiException(this.statusCode, this.detail);

  @override
  String toString() =>
      statusCode == 0 ? 'Server unreachable: $detail' : 'API error $statusCode: $detail';
}

/// Path-encode an id: Odysee ids contain ':' and '@' — encode everywhere for safety.
String _enc(String id) => Uri.encodeComponent(id);

/// Subtitle languages requested with every stream resolution (manual tracks
/// always included server-side; this only gates auto captions).
const _kSubLangs = 'fr,en';

class YtuiApi {
  final Dio dio;

  YtuiApi({required String baseUrl, required String token, Dio? dio})
      : dio = dio ??
            Dio(BaseOptions(
              baseUrl: baseUrl,
              headers: {'Authorization': 'Bearer $token'},
              connectTimeout: const Duration(seconds: 10),
              receiveTimeout: const Duration(seconds: 60),
            ));

  Future<Response<dynamic>> _request(
    String method,
    String path, {
    Map<String, dynamic>? query,
    Object? data,
  }) async {
    try {
      return await dio.request(
        path,
        queryParameters: query,
        data: data,
        options: Options(method: method),
      );
    } on DioException catch (e) {
      final response = e.response;
      if (response != null) {
        final body = response.data;
        final detail = body is Map<String, dynamic>
            ? (body['detail']?.toString() ?? response.statusMessage ?? '')
            : response.statusMessage ?? '';
        throw ApiException(response.statusCode ?? 0, detail);
      }
      throw ApiException(0, e.message ?? 'connection failed');
    }
  }

  Future<Map<String, dynamic>> health() async {
    final r = await _request('GET', '/health');
    return r.data as Map<String, dynamic>;
  }

  // ─── Feed & discovery ───

  Future<FeedResult> feed({bool refresh = false}) async {
    final r = await _request('GET', '/api/feed', query: {'refresh': refresh});
    return FeedResult.fromJson(r.data as Map<String, dynamic>);
  }

  Future<FeedResult> suggestions({bool refresh = false}) async {
    final r =
        await _request('GET', '/api/suggestions', query: {'refresh': refresh});
    return FeedResult.fromJson(r.data as Map<String, dynamic>);
  }

  Future<List<Video>> search(
    String query, {
    int limit = 20,
    String source = 'youtube',
  }) async {
    final r = await _request('GET', '/api/search',
        query: {'q': query, 'limit': limit, 'source': source});
    return ((r.data as Map<String, dynamic>)['items'] as List<dynamic>? ?? const [])
        .map((e) => Video.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<(List<Video>, String, bool)> channelVideos(
    String channelId, {
    String platform = 'youtube',
    int limit = 50,
    int offset = 0,
    String? q,
  }) async {
    final r = await _request('GET', '/api/channels/${_enc(channelId)}/videos',
        query: {
          'platform': platform,
          'limit': limit,
          'offset': offset,
          // The backend rejects an empty q (min_length=1): omit it entirely
          // when listing the whole channel.
          if (q != null && q.isNotEmpty) 'q': q,
        });
    final data = r.data as Map<String, dynamic>;
    final items = (data['items'] as List<dynamic>? ?? const [])
        .map((e) => Video.fromJson(e as Map<String, dynamic>))
        .toList();
    final title = (data['channel'] as Map<String, dynamic>?)?['title'] as String? ?? '';
    return (items, title, data['has_more'] as bool? ?? false);
  }

  Future<(List<Video>, String)> playlistVideos(
    String playlistId, {
    String platform = 'youtube',
    int limit = 200,
  }) async {
    final r = await _request('GET', '/api/ytplaylists/${_enc(playlistId)}/videos',
        query: {'platform': platform, 'limit': limit});
    final data = r.data as Map<String, dynamic>;
    final items = (data['items'] as List<dynamic>? ?? const [])
        .map((e) => Video.fromJson(e as Map<String, dynamic>))
        .toList();
    return (items, data['title'] as String? ?? '');
  }

  Future<VideoDetails> videoDetails(String videoId, {String platform = 'youtube'}) async {
    final r = await _request('GET', '/api/videos/${_enc(videoId)}',
        query: {'platform': platform});
    return VideoDetails.fromJson(r.data as Map<String, dynamic>);
  }

  Future<StreamInfo> videoStreams(
    String videoId, {
    String platform = 'youtube',
    int maxHeight = 1440,
    bool audioOnly = false,
  }) async {
    final r = await _request('GET', '/api/videos/${_enc(videoId)}/streams', query: {
      'platform': platform,
      'max_height': maxHeight,
      'audio_only': audioOnly,
      'sub_langs': _kSubLangs,
    });
    return StreamInfo.fromJson(r.data as Map<String, dynamic>);
  }

  Future<List<Video>> related(String videoId,
      {String platform = 'youtube', int limit = 20}) async {
    final r = await _request('GET', '/api/videos/${_enc(videoId)}/related',
        query: {'platform': platform, 'limit': limit});
    return ((r.data as Map<String, dynamic>)['items'] as List<dynamic>? ?? const [])
        .map((e) => Video.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<SponsorSegment>> sponsorSegments(String videoId,
      {String platform = 'youtube'}) async {
    final r = await _request('GET', '/api/videos/${_enc(videoId)}/sponsor',
        query: {'platform': platform});
    return ((r.data as Map<String, dynamic>)['segments'] as List<dynamic>? ??
            const [])
        .map((e) => SponsorSegment.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Likes the video, or drops an existing like with `rating: 'none'`.
  Future<void> likeVideo(String videoId,
          {String platform = 'youtube', String rating = 'like'}) =>
      _request('POST', '/api/videos/${_enc(videoId)}/like',
          query: {'platform': platform, 'rating': rating});

  /// The signed-in account's rating: 'like', 'dislike' or 'none'.
  Future<String> videoRating(String videoId, {String platform = 'youtube'}) async {
    final r = await _request('GET', '/api/videos/${_enc(videoId)}/rating',
        query: {'platform': platform});
    return (r.data as Map<String, dynamic>)['rating'] as String? ?? 'none';
  }

  /// Posts a top-level comment and returns it as stored server-side.
  Future<Comment> commentVideo(String videoId, String text,
      {String platform = 'youtube'}) async {
    final r = await _request('POST', '/api/videos/${_enc(videoId)}/comment',
        query: {'platform': platform}, data: {'text': text});
    return Comment.fromJson(r.data as Map<String, dynamic>);
  }

  /// One page of top-level comments (YouTube and Odysee). Pass the previous
  /// page's [CommentsPage.nextCursor] back as [cursor] for the next one.
  Future<CommentsPage> videoComments(
    String videoId, {
    String platform = 'youtube',
    String? cursor,
    int pageSize = 50,
  }) async {
    final r = await _request('GET', '/api/videos/${_enc(videoId)}/comments', query: {
      'platform': platform,
      'page_size': pageSize,
      if (cursor != null) 'cursor': cursor,
    });
    return CommentsPage.fromJson(r.data as Map<String, dynamic>);
  }

  /// One page of replies to a top-level comment.
  Future<CommentsPage> commentReplies(
    String videoId,
    String commentId, {
    String platform = 'youtube',
    String? cursor,
    int pageSize = 50,
  }) async {
    final r = await _request(
      'GET',
      '/api/videos/${_enc(videoId)}/comments/${_enc(commentId)}/replies',
      query: {
        'platform': platform,
        'page_size': pageSize,
        if (cursor != null) 'cursor': cursor,
      },
    );
    return CommentsPage.fromJson(r.data as Map<String, dynamic>);
  }

  /// Posts a reply and returns it as stored server-side.
  Future<Comment> replyComment(String videoId, String commentId, String text,
      {String platform = 'youtube'}) async {
    final r = await _request(
      'POST',
      '/api/videos/${_enc(videoId)}/comments/${_enc(commentId)}/reply',
      query: {'platform': platform},
      data: {'text': text},
    );
    return Comment.fromJson(r.data as Map<String, dynamic>);
  }

  // ─── Followed channels ───

  Future<List<FollowedChannel>> channels() async {
    final r = await _request('GET', '/api/channels');
    return (r.data as List<dynamic>)
        .map((e) => FollowedChannel.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<FollowedChannel> followChannel(String ref) async {
    final r = await _request('POST', '/api/channels', data: {'ref': ref});
    return FollowedChannel.fromJson(r.data as Map<String, dynamic>);
  }

  Future<void> unfollowChannel(String channelId) =>
      _request('DELETE', '/api/channels/${_enc(channelId)}');

  // ─── History ───

  Future<List<HistoryEntry>> history({int limit = 200}) async {
    final r = await _request('GET', '/api/history', query: {'limit': limit});
    return (r.data as List<dynamic>)
        .map((e) => HistoryEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> recordWatch(Video video) =>
      _request('POST', '/api/history', data: {'video': video.toJson()});

  Future<Set<String>> watchedIds() async {
    final r = await _request('GET', '/api/history/watched-ids');
    return ((r.data as Map<String, dynamic>)['ids'] as List<dynamic>)
        .map((e) => e as String)
        .toSet();
  }

  Future<void> savePosition(String videoId, double position, {double? duration}) =>
      _request('PUT', '/api/history/${_enc(videoId)}/position',
          data: {'position': position, 'duration': duration});

  Future<ResumeInfo?> resume(String videoId) async {
    try {
      final r = await _request('GET', '/api/history/${_enc(videoId)}/resume');
      return ResumeInfo.fromJson(r.data as Map<String, dynamic>);
    } on ApiException catch (e) {
      if (e.statusCode == 404) return null;
      rethrow;
    }
  }

  Future<void> removeWatch(String videoId) =>
      _request('DELETE', '/api/history/${_enc(videoId)}');

  // ─── Local playlists ───

  Future<List<LocalPlaylist>> playlists() async {
    final r = await _request('GET', '/api/playlists');
    return (r.data as List<dynamic>)
        .map((e) => LocalPlaylist.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Returns null when the name is already taken (409).
  Future<LocalPlaylist?> createPlaylist(String name) async {
    try {
      final r = await _request('POST', '/api/playlists', data: {'name': name});
      return LocalPlaylist.fromJson(r.data as Map<String, dynamic>);
    } on ApiException catch (e) {
      if (e.statusCode == 409) return null;
      rethrow;
    }
  }

  /// Returns false when the name is already taken (409).
  Future<bool> renamePlaylist(int id, String name) async {
    try {
      await _request('PATCH', '/api/playlists/$id', data: {'name': name});
      return true;
    } on ApiException catch (e) {
      if (e.statusCode == 409) return false;
      rethrow;
    }
  }

  Future<void> deletePlaylist(int id) => _request('DELETE', '/api/playlists/$id');

  Future<List<PlaylistItem>> playlistItems(int id) async {
    final r = await _request('GET', '/api/playlists/$id/items');
    return (r.data as List<dynamic>)
        .map((e) => PlaylistItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Returns false when the item is already in the playlist (409).
  Future<bool> addPlaylistItem(int id, Video video) async {
    try {
      await _request('POST', '/api/playlists/$id/items', data: {'video': video.toJson()});
      return true;
    } on ApiException catch (e) {
      if (e.statusCode == 409) return false;
      rethrow;
    }
  }

  Future<void> removePlaylistItem(int id, int position) =>
      _request('DELETE', '/api/playlists/$id/items/$position');

  // ─── Auth & lives ───

  Future<bool> authStatus() async {
    final r = await _request('GET', '/api/auth/youtube/status');
    return (r.data as Map<String, dynamic>)['authenticated'] as bool? ?? false;
  }

  Future<List<LiveItem>> lives() async {
    final r = await _request('GET', '/api/lives');
    return (r.data as List<dynamic>)
        .map((e) => LiveItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<ChatPage> liveChat(String videoId,
      {required String platform, int cursor = 0}) async {
    final r = await _request('GET', '/api/lives/${_enc(videoId)}/chat',
        query: {'platform': platform, 'cursor': cursor});
    return ChatPage.fromJson(r.data as Map<String, dynamic>);
  }
}
