/// resolvedChannelProvider: channel id resolved on demand for videos that do
/// not carry one (flat playlist extraction), failing soft to null.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/api/client.dart';
import 'package:ytui_mobile/state/providers.dart';

class _DetailsAdapter implements HttpClientAdapter {
  final int statusCode;
  final String body;

  _DetailsAdapter({this.statusCode = 200, this.body = '{}'});

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? _,
      Future<void>? __) async {
    return ResponseBody.fromString(
      body,
      statusCode,
      headers: {Headers.contentTypeHeader: [Headers.jsonContentType]},
    );
  }

  @override
  void close({bool force = false}) {}
}

ProviderContainer _container(_DetailsAdapter adapter) {
  final dio = Dio(BaseOptions(baseUrl: 'https://server.test'))
    ..httpClientAdapter = adapter;
  return ProviderContainer(overrides: [
    apiProvider.overrideWithValue(
        YtuiApi(baseUrl: 'https://server.test', token: 'sekret', dio: dio)),
  ]);
}

void main() {
  test('resolves the channel id from the details endpoint', () async {
    final container = _container(_DetailsAdapter(
      body: jsonEncode({
        'video_id': 'abc',
        'title': 't',
        'channel_id': 'UC123',
        'channel_title': 'Chan',
      }),
    ));
    addTearDown(container.dispose);

    final channelId =
        await container.read(resolvedChannelProvider(('youtube', 'abc')).future);
    expect(channelId, 'UC123');
  });

  test('fails soft to null on an empty channel id', () async {
    final container = _container(_DetailsAdapter(
      body: jsonEncode({
        'video_id': 'abc',
        'title': 't',
        'channel_id': '',
      }),
    ));
    addTearDown(container.dispose);

    final channelId =
        await container.read(resolvedChannelProvider(('youtube', 'abc')).future);
    expect(channelId, isNull);
  });

  test('fails soft to null on a 404', () async {
    final container = _container(
        _DetailsAdapter(statusCode: 404, body: jsonEncode({'detail': 'nope'})));
    addTearDown(container.dispose);

    final channelId =
        await container.read(resolvedChannelProvider(('youtube', 'abc')).future);
    expect(channelId, isNull);
  });

  test('caches per platform-qualified id', () async {
    var calls = 0;
    final adapter = _CountingAdapter();
    final container = ProviderContainer(overrides: [
      apiProvider.overrideWithValue(YtuiApi(
        baseUrl: 'https://server.test',
        token: 'sekret',
        dio: Dio(BaseOptions(baseUrl: 'https://server.test'))
          ..httpClientAdapter = adapter,
      )),
    ]);
    addTearDown(container.dispose);
    adapter.onRequest = (options) async {
      calls += 1;
      return ResponseBody.fromString(
        jsonEncode({'video_id': 'abc', 'channel_id': 'UC123'}),
        200,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]},
      );
    };

    final a =
        await container.read(resolvedChannelProvider(('youtube', 'abc')).future);
    final b =
        await container.read(resolvedChannelProvider(('youtube', 'abc')).future);
    expect(a, 'UC123');
    expect(b, 'UC123');
    expect(calls, 1, reason: 'the second read must hit the cache');
  });
}

/// Adapter with a swappable handler and a call counter.
class _CountingAdapter implements HttpClientAdapter {
  Future<ResponseBody> Function(RequestOptions options)? onRequest;

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? _,
      Future<void>? __) async {
    return onRequest!(options);
  }

  @override
  void close({bool force = false}) {}
}
