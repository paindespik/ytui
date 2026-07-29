/// Hand-written models mirroring the ytui backend JSON schemas (Phase A).
library;

class Video {
  final String videoId;
  final String title;
  final String channelTitle;
  final String channelId;
  final DateTime? published;
  final int? duration; // seconds
  final String thumbnailUrl;
  final String kind; // video | playlist | channel
  final String platform; // youtube | bitchute | odysee
  final String playlistId;
  final String url;

  const Video({
    required this.videoId,
    required this.title,
    this.channelTitle = '',
    this.channelId = '',
    this.published,
    this.duration,
    this.thumbnailUrl = '',
    this.kind = 'video',
    this.platform = 'youtube',
    this.playlistId = '',
    this.url = '',
  });

  factory Video.fromJson(Map<String, dynamic> json) => Video(
        videoId: json['video_id'] as String,
        title: json['title'] as String? ?? '',
        channelTitle: json['channel_title'] as String? ?? '',
        channelId: json['channel_id'] as String? ?? '',
        published: json['published'] != null
            ? DateTime.tryParse(json['published'] as String)
            : null,
        duration: json['duration'] as int?,
        thumbnailUrl: json['thumbnail_url'] as String? ?? '',
        kind: json['kind'] as String? ?? 'video',
        platform: json['platform'] as String? ?? 'youtube',
        playlistId: json['playlist_id'] as String? ?? '',
        url: json['url'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => {
        'video_id': videoId,
        'title': title,
        'channel_title': channelTitle,
        'channel_id': channelId,
        'published': published?.toUtc().toIso8601String(),
        'duration': duration,
        'thumbnail_url': thumbnailUrl,
        'kind': kind,
        'platform': platform,
        'playlist_id': playlistId,
      };

  String get durationLabel {
    final d = duration;
    if (d == null || d <= 0) return '';
    final h = d ~/ 3600, m = (d % 3600) ~/ 60, s = d % 60;
    String two(int v) => v.toString().padLeft(2, '0');
    return h > 0 ? '$h:${two(m)}:${two(s)}' : '$m:${two(s)}';
  }
}

class FeedResult {
  final List<Video> videos;
  final List<String> warnings;

  const FeedResult({required this.videos, this.warnings = const []});

  factory FeedResult.fromJson(Map<String, dynamic> json) => FeedResult(
        videos: (json['videos'] as List<dynamic>)
            .map((e) => Video.fromJson(e as Map<String, dynamic>))
            .toList(),
        warnings: (json['warnings'] as List<dynamic>? ?? [])
            .map((e) => e as String)
            .toList(),
      );
}

class VideoDetails {
  final String videoId;
  final String title;
  final String channelId;
  final String channelTitle;
  final String description;
  final int? viewCount;
  final int? likeCount;
  final int? duration;
  final String uploadDate;

  const VideoDetails({
    this.videoId = '',
    this.title = '',
    this.channelId = '',
    this.channelTitle = '',
    this.description = '',
    this.viewCount,
    this.likeCount,
    this.duration,
    this.uploadDate = '',
  });

  factory VideoDetails.fromJson(Map<String, dynamic> json) => VideoDetails(
        videoId: json['video_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        channelId: json['channel_id'] as String? ?? '',
        channelTitle: json['channel_title'] as String? ?? '',
        description: json['description'] as String? ?? '',
        viewCount: json['view_count'] as int?,
        likeCount: json['like_count'] as int?,
        duration: json['duration'] as int?,
        uploadDate: json['upload_date'] as String? ?? '',
      );
}

class SubtitleTrackInfo {
  final String lang;
  final String label;
  final String url;
  final bool auto;

  const SubtitleTrackInfo({
    required this.lang,
    this.label = '',
    required this.url,
    this.auto = false,
  });

  factory SubtitleTrackInfo.fromJson(Map<String, dynamic> json) =>
      SubtitleTrackInfo(
        lang: json['lang'] as String? ?? '',
        label: json['label'] as String? ?? '',
        url: json['url'] as String? ?? '',
        auto: json['auto'] as bool? ?? false,
      );
}

class StreamInfo {
  final String kind; // hls | progressive | split
  final String url;
  final String? videoUrl;
  final String? audioUrl;
  final String title;
  final int? duration;
  final DateTime? expiresAt;
  final int? height;
  final List<SubtitleTrackInfo> subtitles;

  const StreamInfo({
    required this.kind,
    required this.url,
    this.videoUrl,
    this.audioUrl,
    this.title = '',
    this.duration,
    this.expiresAt,
    this.height,
    this.subtitles = const [],
  });

  factory StreamInfo.fromJson(Map<String, dynamic> json) => StreamInfo(
        kind: json['kind'] as String,
        url: json['url'] as String,
        videoUrl: json['video_url'] as String?,
        audioUrl: json['audio_url'] as String?,
        title: json['title'] as String? ?? '',
        duration: json['duration'] as int?,
        height: json['height'] as int?,
        expiresAt: json['expires_at'] != null
            ? DateTime.tryParse(json['expires_at'] as String)
            : null,
        subtitles: (json['subtitles'] as List<dynamic>? ?? const [])
            .map((e) => SubtitleTrackInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class SponsorSegment {
  final String category;
  final double start;
  final double end;

  const SponsorSegment({
    required this.category,
    required this.start,
    required this.end,
  });

  factory SponsorSegment.fromJson(Map<String, dynamic> json) => SponsorSegment(
        category: json['category'] as String? ?? '',
        start: (json['start'] as num? ?? 0).toDouble(),
        end: (json['end'] as num? ?? 0).toDouble(),
      );
}

class FollowedChannel {
  final String ref;
  final String channelId;
  final String title;
  final String platform;

  const FollowedChannel({
    required this.ref,
    required this.channelId,
    this.title = '',
    this.platform = 'youtube',
  });

  factory FollowedChannel.fromJson(Map<String, dynamic> json) => FollowedChannel(
        ref: json['ref'] as String,
        channelId: json['channel_id'] as String,
        title: json['title'] as String? ?? '',
        platform: json['platform'] as String? ?? 'youtube',
      );
}

class HistoryEntry {
  final Video video;
  final double watchedAt;
  final double position;

  const HistoryEntry({
    required this.video,
    required this.watchedAt,
    this.position = 0,
  });

  factory HistoryEntry.fromJson(Map<String, dynamic> json) => HistoryEntry(
        video: Video.fromJson(json['video'] as Map<String, dynamic>),
        watchedAt: (json['watched_at'] as num).toDouble(),
        position: (json['position'] as num? ?? 0).toDouble(),
      );
}

class ResumeInfo {
  final double position;
  final double? duration;
  final String playlistId;

  const ResumeInfo({required this.position, this.duration, this.playlistId = ''});

  factory ResumeInfo.fromJson(Map<String, dynamic> json) => ResumeInfo(
        position: (json['position'] as num).toDouble(),
        duration: (json['duration'] as num?)?.toDouble(),
        playlistId: json['playlist_id'] as String? ?? '',
      );
}

/// Watch at least 95% of a video and it restarts from the beginning.
double resumeStart(double position, double? duration) {
  if (position <= 0) return 0;
  if (duration != null && duration > 0 && position / duration >= 0.95) return 0;
  return position;
}

class LocalPlaylist {
  final int id;
  final String name;
  final double createdAt;
  final int count;

  const LocalPlaylist({
    required this.id,
    required this.name,
    required this.createdAt,
    required this.count,
  });

  factory LocalPlaylist.fromJson(Map<String, dynamic> json) => LocalPlaylist(
        id: json['id'] as int,
        name: json['name'] as String,
        createdAt: (json['created_at'] as num).toDouble(),
        count: json['count'] as int? ?? 0,
      );
}

class PlaylistItem {
  final int position;
  final Video video;

  const PlaylistItem({required this.position, required this.video});

  factory PlaylistItem.fromJson(Map<String, dynamic> json) => PlaylistItem(
        position: json['position'] as int,
        video: Video.fromJson(json['video'] as Map<String, dynamic>),
      );
}

class Comment {
  final String commentId;
  final String text;
  final String channelName;
  final int? timestamp;
  final int replies;
  final int likes;
  final int dislikes;
  final bool isPinned;

  const Comment({
    required this.commentId,
    required this.text,
    this.channelName = '',
    this.timestamp,
    this.replies = 0,
    this.likes = 0,
    this.dislikes = 0,
    this.isPinned = false,
  });

  factory Comment.fromJson(Map<String, dynamic> json) => Comment(
        commentId: json['comment_id'] as String,
        text: json['text'] as String? ?? '',
        channelName: json['channel_name'] as String? ?? '',
        timestamp: json['timestamp'] as int?,
        replies: json['replies'] as int? ?? 0,
        likes: json['likes'] as int? ?? 0,
        dislikes: json['dislikes'] as int? ?? 0,
        isPinned: json['is_pinned'] as bool? ?? false,
      );
}

class CommentsPage {
  final List<Comment> items;
  final int total;

  const CommentsPage({required this.items, this.total = 0});

  factory CommentsPage.fromJson(Map<String, dynamic> json) => CommentsPage(
        items: (json['items'] as List<dynamic>)
            .map((e) => Comment.fromJson(e as Map<String, dynamic>))
            .toList(),
        total: json['total'] as int? ?? 0,
      );
}

class ChatMessage {
  final String id;
  final String author;
  final String text;
  final String? color;
  final double timestamp;

  const ChatMessage({
    required this.id,
    required this.author,
    required this.text,
    this.color,
    this.timestamp = 0,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
        id: json['id'] as String,
        author: json['author'] as String? ?? '',
        text: json['text'] as String? ?? '',
        color: json['color'] as String?,
        timestamp: (json['timestamp'] as num? ?? 0).toDouble(),
      );
}

class ChatPage {
  final List<ChatMessage> messages;
  final int cursor;
  final bool active;

  const ChatPage({required this.messages, this.cursor = 0, this.active = true});

  factory ChatPage.fromJson(Map<String, dynamic> json) => ChatPage(
        messages: (json['messages'] as List<dynamic>? ?? const [])
            .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
            .toList(),
        cursor: json['cursor'] as int? ?? 0,
        active: json['active'] as bool? ?? true,
      );
}

class LiveItem {
  final Video video;
  final DateTime detectedAt;

  const LiveItem({required this.video, required this.detectedAt});

  factory LiveItem.fromJson(Map<String, dynamic> json) => LiveItem(
        video: Video.fromJson(json['video'] as Map<String, dynamic>),
        detectedAt: DateTime.tryParse(json['detected_at'] as String? ?? '') ??
            DateTime.fromMillisecondsSinceEpoch(0),
      );
}
