import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/client.dart';
import 'package:ytui_mobile/state/providers.dart';

/// Serves one video per requested offset; the second page is the last one.
class _PagingAdapter implements HttpClientAdapter {
  final List<Object?> offsets = [];
  final List<Object?> queries = [];

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? _,
      Future<void>? __) async {
    final offset = options.queryParameters['offset'] as int;
    offsets.add(offset);
    queries.add(options.queryParameters['q']);
    return ResponseBody.fromString(
      jsonEncode({
        'items': [
          {'video_id': 'v$offset', 'title': 'video $offset'},
        ],
        'channel': {'title': 'Chan'},
        'has_more': offset == 0,
      }),
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

/// Builds a container whose API talks to [adapter].
ProviderContainer _container(_PagingAdapter adapter) {
  final dio = Dio(BaseOptions(baseUrl: 'https://server.test'))
    ..httpClientAdapter = adapter;
  return ProviderContainer(overrides: [
    apiProvider.overrideWithValue(
        YtuiApi(baseUrl: 'https://server.test', token: 'sekret', dio: dio)),
  ]);
}

void main() {
  test('loadMore appends the next page then stops', () async {
    final adapter = _PagingAdapter();
    final container = _container(adapter);
    addTearDown(container.dispose);

    const arg = ('UC1', 'youtube', '');
    container.listen(channelVideosProvider(arg), (_, __) {});
    final first = await container.read(channelVideosProvider(arg).future);
    expect(first.videos.single.videoId, 'v0');
    expect(first.hasMore, isTrue);

    final notifier = container.read(channelVideosProvider(arg).notifier);
    expect(await notifier.loadMore(), isNull);

    final state = container.read(channelVideosProvider(arg)).requireValue;
    expect(state.videos.map((v) => v.videoId), ['v0', 'v1']);
    expect(state.hasMore, isFalse);
    expect(state.loadingMore, isFalse);
    expect(adapter.offsets, [0, 1]);

    // An empty query means "list the channel": no q at all, the backend
    // rejects an empty one.
    expect(adapter.queries, [null, null]);

    // No further request once the backend says there is nothing left.
    await notifier.loadMore();
    expect(adapter.offsets, [0, 1]);
  });

  test('a non-empty query is forwarded on every page', () async {
    final adapter = _PagingAdapter();
    final container = _container(adapter);
    addTearDown(container.dispose);

    const arg = ('UC1', 'youtube', 'rocket');
    container.listen(channelVideosProvider(arg), (_, __) {});
    await container.read(channelVideosProvider(arg).future);
    expect(await container
        .read(channelVideosProvider(arg).notifier)
        .loadMore(), isNull);

    expect(adapter.offsets, [0, 1]);
    expect(adapter.queries, ['rocket', 'rocket']);
  });
}
