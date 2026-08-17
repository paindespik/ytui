/// Channel screen: latest videos, in-channel search and follow button.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../state/providers.dart';
import '../state/queue.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

class ChannelScreen extends ConsumerStatefulWidget {
  final String channelId;
  final String platform;
  final String title;

  const ChannelScreen({
    super.key,
    required this.channelId,
    this.platform = 'youtube',
    this.title = '',
  });

  @override
  ConsumerState<ChannelScreen> createState() => _ChannelScreenState();
}

class _ChannelScreenState extends ConsumerState<ChannelScreen> {
  /// Search bar shown in place of the title.
  bool _searching = false;

  /// Submitted query; empty means "list the whole channel".
  String _query = '';

  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// The family key of the list currently displayed.
  (String, String, String) get _arg =>
      (widget.channelId, widget.platform, _query);

  void _submit(String value) => setState(() => _query = value.trim());

  void _closeSearch() {
    _controller.clear();
    setState(() {
      _searching = false;
      _query = '';
    });
  }

  @override
  Widget build(BuildContext context) {
    final data = ref.watch(channelVideosProvider(_arg));
    final colorScheme = Theme.of(context).colorScheme;
    // Live badge: the channel appears in the lives endpoint (Twitch, TikTok…).
    final isLive = (ref.watch(livesProvider).valueOrNull ?? const <LiveItem>[])
        .any((l) => l.video.channelId == widget.channelId);
    final isTikTok = widget.platform == 'tiktok';
    final body = _buildBody(data, colorScheme);

    return Scaffold(
      appBar: AppBar(
        title: _searching
            ? Container(
                height: 40,
                decoration: BoxDecoration(
                  color: colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(kRadiusLg),
                ),
                child: TextField(
                  controller: _controller,
                  autofocus: true,
                  textInputAction: TextInputAction.search,
                  decoration: const InputDecoration(
                    hintText: 'Search this channel…',
                    prefixIcon: Icon(Icons.search),
                    border: InputBorder.none,
                    contentPadding: EdgeInsets.symmetric(vertical: 10),
                  ),
                  onSubmitted: _submit,
                ),
              )
            : Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Flexible(
                    child: Text(
                      data.valueOrNull?.title.isNotEmpty == true
                          ? data.valueOrNull!.title
                          : widget.title,
                      style: const TextStyle(fontWeight: FontWeight.bold),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (isLive)
                    Padding(
                      padding: const EdgeInsets.only(left: 8),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color: colorScheme.errorContainer,
                          borderRadius: BorderRadius.circular(kRadiusSm),
                        ),
                        child: Text(
                          '● en direct',
                          style: TextStyle(
                            color: colorScheme.onErrorContainer,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
        actions: _searching
            ? [
                IconButton(
                  icon: const Icon(Icons.close),
                  tooltip: 'Fermer la recherche',
                  onPressed: _closeSearch,
                ),
              ]
            : [
                IconButton(
                  icon: const Icon(Icons.search),
                  tooltip: 'Rechercher dans la chaîne',
                  onPressed: () => setState(() => _searching = true),
                ),
                IconButton(
                  icon: const Icon(Icons.person_add),
                  tooltip: 'Suivre',
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.of(context);
                    final ref_ = switch (widget.platform) {
                      'bitchute' => 'bitchute:${widget.channelId}',
                      'odysee' => 'odysee:${widget.channelId}',
                      'crowdbunker' => 'crowdbunker:${widget.channelId}',
                      _ => widget.channelId,
                    };
                    try {
                      await ref.read(apiProvider).followChannel(ref_);
                      messenger.showSnackBar(
                          const SnackBar(content: Text('Channel followed')));
                    } on ApiException catch (e) {
                      messenger.showSnackBar(SnackBar(
                          content: Text(e.statusCode == 409
                              ? 'Already followed'
                              : e.toString())));
                    }
                  },
                ),
              ],
      ),
      body: isTikTok
          ? Column(
              children: [
                // TikTok channel video pages are not scrapable without auth:
                // the channel only surfaces through the lives tab.
                Padding(
                  padding: const EdgeInsets.all(kGutter),
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(kRadiusMd),
                    ),
                    child: Row(
                      children: [
                        Icon(Icons.live_tv,
                            color: colorScheme.onErrorContainer),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            "Chaîne en direct uniquement — voir l'onglet Lives",
                            style:
                                TextStyle(color: colorScheme.onErrorContainer),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                Expanded(child: body),
              ],
            )
          : body,
    );
  }

  Widget _buildBody(AsyncValue<ChannelVideos> data, ColorScheme colorScheme) {
    return data.when(
        loading: () => const AppLoading(),
        error: (e, _) => AppError.from(e,
            onRetry: () => ref.invalidate(channelVideosProvider(_arg))),
        data: (result) {
          final videos = result.videos;
          if (videos.isEmpty) {
            return _query.isEmpty
                ? const AppEmpty(
                    message: 'This channel has no videos',
                    icon: Icons.videocam_off_outlined,
                  )
                : AppEmpty(
                    message: 'No videos matching "$_query"',
                    icon: Icons.search_off,
                  );
          }
          return ResponsiveCenter(
            // Reaching the end pulls the next page in, so neither a thumb nor a
            // D-pad has to hunt for the footer button after 50 tiles.
            child: NotificationListener<ScrollUpdateNotification>(
              onNotification: (notification) {
                final m = notification.metrics;
                if (result.hasMore &&
                    !result.loadingMore &&
                    m.axis == Axis.vertical &&
                    m.pixels > m.maxScrollExtent - 800) {
                  _loadMore();
                }
                return false;
              },
              child: ListView.builder(
                padding: const EdgeInsets.only(bottom: kGutter),
                // header + videos (+ load-more footer while more pages remain)
                itemCount: videos.length + (result.hasMore ? 2 : 1),
                itemBuilder: (context, index) {
                  if (index == 0) {
                    // Prominent "Play all" header; in search mode it plays the
                    // matching videos instead of the whole channel.
                    return Padding(
                      padding: const EdgeInsets.fromLTRB(
                          kGutter, kGutter, kGutter, 8),
                      child: FilledButton.icon(
                        onPressed: () {
                          ref.read(queueProvider.notifier).play(videos);
                          context.push('/player');
                        },
                        icon: const Icon(Icons.play_arrow),
                        label: Text(_query.isEmpty
                            ? 'Play all ${videos.length} videos'
                            : 'Play ${videos.length} results'),
                        style: FilledButton.styleFrom(
                          backgroundColor: colorScheme.primary,
                          foregroundColor: colorScheme.onPrimary,
                          minimumSize: const Size.fromHeight(48),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(kRadiusMd),
                          ),
                        ),
                      ),
                    );
                  }
                  final videoIndex = index - 1;
                  if (videoIndex == videos.length) {
                    return _loadMoreFooter(result);
                  }
                  return VideoTile(
                    video: videos[videoIndex],
                    onTap: () {
                      ref
                          .read(queueProvider.notifier)
                          .play(videos, startIndex: videoIndex);
                      context.push('/player');
                    },
                  );
                },
              ),
            ),
          );
        });
  }

  /// Fetches the next page; the notifier ignores redundant calls itself.
  Future<void> _loadMore() async {
    final messenger = ScaffoldMessenger.of(context);
    final error =
        await ref.read(channelVideosProvider(_arg).notifier).loadMore();
    if (error != null) {
      messenger.showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  /// Footer below the last video: fetches the next page of older videos.
  Widget _loadMoreFooter(ChannelVideos result) {
    if (result.loadingMore) {
      return const Padding(
        padding: EdgeInsets.all(kGutter),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(kGutter, 8, kGutter, kGutter),
      child: OutlinedButton.icon(
        onPressed: _loadMore,
        icon: const Icon(Icons.expand_more),
        label: Text(_query.isEmpty ? 'Load older videos' : 'Load more results'),
        style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
      ),
    );
  }
}
