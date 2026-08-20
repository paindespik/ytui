/// Network-type detection and cellular playback helpers.
///
/// A phone on mobile data cannot stream what a LAN can: direct googlevideo
/// URLs resolved by the backend are paced for the backend's IP and served by
/// a far edge (measured 27-82 KB/s, unstable, vs a steady ~85 KB/s through
/// the backend proxy on the same link), and the default quality ceiling is
/// sized for Wi-Fi. Playback therefore asks [isCellularNow] at load time and
/// switches to the cellular quality cap + the backend byte proxy.
library;

import 'package:flutter/services.dart';

const _channel = MethodChannel('dev.ytui.app/device');

/// Whether the active network is cellular, reported by [MainActivity].
/// `false` on error (missing plugin in tests, non-Android platforms).
Future<bool> isCellularNow() async {
  try {
    return await _channel.invokeMethod<bool>('isCellular') ?? false;
  } catch (_) {
    return false;
  }
}

/// Rewrites an upstream media URL through the backend's same-origin proxy.
///
/// `playlist: true` targets `/api/proxy/hls`, which rewrites every URI of the
/// fetched m3u8 back through the proxy; plain byte streams (progressive MP4,
/// split DASH tracks, FLV) go through `/api/proxy`, which forwards Range
/// requests. Both endpoints authenticate with the `Authorization` header the
/// player already sends (media_kit applies `http-header-fields` globally, so
/// an external audio track added afterwards inherits it).
String proxiedStreamUrl(String serverUrl, String url, {bool playlist = false}) {
  final endpoint = playlist ? '/api/proxy/hls' : '/api/proxy';
  return '$serverUrl$endpoint?url=${Uri.encodeComponent(url)}';
}
