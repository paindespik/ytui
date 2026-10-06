/// Channel screen: videos (newest or most viewed first), in-channel search and
/// follow button, plus a Playlists tab on platforms that list them (YouTube,
/// BitChute).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/open_player.dart';
import '../widgets/responsive.dart';
import '../widgets/screen_focus.dart';
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

class _ChannelScreenState extends ConsumerState<ChannelScreen>
    with SingleTickerProviderStateMixin {
  /// Search bar shown in place of the title.
  bool _searching = false;

  /// Submitted query; empty means "list the whole channel".
  String _query = '';

  /// Listing order: 'date' (newest first) or 'views' (most viewed first),
  /// on [kChannelSortPlatforms] only. A search keeps its own order.
  String _sort = 'date';

  bool get _canSort => kChannelSortPlatforms.contains(widget.platform);

  final _controller = TextEditingController();

  /// Vidéos / Playlists tabs; null on platforms without channel playlists.
  TabController? _tabs;

  bool get _hasPlaylists => kChannelPlaylistPlatforms.contains(widget.platform);

  @override
  void initState() {
    super.initState();
    if (_hasPlaylists) _tabs = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabs?.dispose();
    _controller.dispose();
    super.dispose();
  }

  /// The family key of the list currently displayed (a search ignores the
  /// order, so its key does not depend on it).
  (String, String, String, String) get _arg => (
        widget.channelId,
        widget.platform,
        _query,
        _query.isEmpty ? _sort : 'date',
      );

  /// The search looks for videos: bring their tab to the front.
  void _showVideosTab() {
    final tabs = _tabs;
    if (tabs != null && tabs.index != 0) tabs.animateTo(0);
  }

  void _submit(String value) {
    _showVideosTab();
    setState(() => _query = value.trim());
  }

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
    final listBody = _buildBody(data, colorScheme);
    // The order bar stays above the list while a new order loads.
    final videosBody = _canSort && _query.isEmpty
        ? Column(children: [_sortBar(), Expanded(child: listBody)])
        : listBody;
    final tabs = _tabs;
    final body = tabs == null
        ? videosBody
        : AnimatedBuilder(
            animation: tabs,
            builder: (context, _) => TabBarView(
              controller: tabs,
              children: [
                // Kept alive: coming back to a tab keeps its scroll position
                // and its loaded pages. The hidden page stays in the tree, so
                // it is kept out of D-pad traversal.
                ExcludeFocus(
                  excluding: tabs.index != 0,
                  child: _KeepAlive(child: videosBody),
                ),
                ExcludeFocus(
                  excluding: tabs.index != 1,
                  child: _KeepAlive(
                    child: _ChannelPlaylistsTab(
                      channelId: widget.channelId,
                      platform: widget.platform,
                    ),
                  ),
                ),
              ],
            ),
          );

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
                    hintText: 'Rechercher dans la chaîne…',
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
                  onPressed: () {
                    _showVideosTab();
                    setState(() => _searching = true);
                  },
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
                          const SnackBar(content: Text('Chaîne suivie')));
                    } on ApiException catch (e) {
                      messenger.showSnackBar(SnackBar(
                          content: Text(e.statusCode == 409
                              ? 'Déjà suivie'
                              : e.toString())));
                    }
                  },
                ),
              ],
        bottom: tabs == null
            ? null
            : TabBar(
                controller: tabs,
                tabs: const [
                  Tab(text: 'Vidéos'),
                  Tab(text: 'Playlists'),
                ],
              ),
      ),
      body: ScreenFocus(child: isTikTok
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
          : body),
    );
  }

  Widget _sortBar() => ResponsiveCenter(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(kGutter, 8, kGutter, 0),
          child: SegmentedButton<String>(
            segments: const [
              ButtonSegment(
                value: 'date',
                label: Text('Plus récentes'),
                icon: Icon(Icons.schedule),
              ),
              ButtonSegment(
                value: 'views',
                label: Text('Plus vues'),
                icon: Icon(Icons.trending_up),
              ),
            ],
            selected: {_sort},
            showSelectedIcon: false,
            onSelectionChanged: (selection) =>
                setState(() => _sort = selection.first),
          ),
        ),
      );

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
                    message: 'Cette chaîne n’a aucune vidéo',
                    icon: Icons.videocam_off_outlined,
                  )
                : AppEmpty(
                    message: 'Aucune vidéo pour « $_query »',
                    icon: Icons.search_off,
                  );
          }
          return ResponsiveCenter(
            // Reaching the end pulls the next page in, so neither a thumb nor a
            // D-pad has to hunt for the footer button after 50 tiles.
            child: _PagedListener(
              result: result,
              onLoadMore: _loadMore,
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
                        onPressed: () => openPlayer(context, ref, videos),
                        icon: const Icon(Icons.play_arrow),
                        label: Text(_query.isEmpty
                            ? 'Lire les ${videos.length} vidéos'
                            : 'Lire les ${videos.length} résultats'),
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
                    return _LoadMoreFooter(
                      loading: result.loadingMore,
                      label: _query.isNotEmpty
                          ? 'Charger plus de résultats'
                          : _arg.$4 == 'views'
                              ? 'Charger plus de vidéos'
                              : 'Charger des vidéos plus anciennes',
                      onPressed: _loadMore,
                    );
                  }
                  return VideoTile(
                    video: videos[videoIndex],
                    onTap: () => openPlayer(context, ref, videos,
                        startIndex: videoIndex),
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
}

/// The channel's playlists: fetched the first time the tab is shown, paged
/// like the videos. A tile opens the playlist (VideoTile's default tap).
class _ChannelPlaylistsTab extends ConsumerWidget {
  final String channelId;
  final String platform;

  const _ChannelPlaylistsTab({required this.channelId, required this.platform});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final arg = (channelId, platform);
    final data = ref.watch(channelPlaylistsProvider(arg));

    Future<void> loadMore() async {
      final messenger = ScaffoldMessenger.of(context);
      final error =
          await ref.read(channelPlaylistsProvider(arg).notifier).loadMore();
      if (error != null) {
        messenger.showSnackBar(SnackBar(content: Text('$error')));
      }
    }

    return data.when(
      loading: () => const AppLoading(),
      error: (e, _) => AppError.from(e,
          onRetry: () => ref.invalidate(channelPlaylistsProvider(arg))),
      data: (result) {
        final playlists = result.videos;
        if (playlists.isEmpty) {
          return const AppEmpty(
            message: 'Cette chaîne n’a aucune playlist',
            icon: Icons.playlist_remove,
          );
        }
        return ResponsiveCenter(
          child: _PagedListener(
            result: result,
            onLoadMore: loadMore,
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: playlists.length + (result.hasMore ? 1 : 0),
              itemBuilder: (context, index) {
                if (index == playlists.length) {
                  return _LoadMoreFooter(
                    loading: result.loadingMore,
                    label: 'Charger plus de playlists',
                    onPressed: loadMore,
                  );
                }
                return VideoTile(video: playlists[index]);
              },
            ),
          ),
        );
      },
    );
  }
}

/// Calls [onLoadMore] when the list nears its end while pages remain.
class _PagedListener extends StatelessWidget {
  final ChannelVideos result;
  final Future<void> Function() onLoadMore;
  final Widget child;

  const _PagedListener({
    required this.result,
    required this.onLoadMore,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    return NotificationListener<ScrollUpdateNotification>(
      onNotification: (notification) {
        final m = notification.metrics;
        if (result.hasMore &&
            !result.loadingMore &&
            m.axis == Axis.vertical &&
            m.pixels > m.maxScrollExtent - 800) {
          onLoadMore();
        }
        return false;
      },
      child: child,
    );
  }
}

/// Footer below the last item: fetches the next page.
class _LoadMoreFooter extends StatelessWidget {
  final bool loading;
  final String label;
  final VoidCallback onPressed;

  const _LoadMoreFooter({
    required this.loading,
    required this.label,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    if (loading) {
      return const Padding(
        padding: EdgeInsets.all(kGutter),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(kGutter, 8, kGutter, kGutter),
      child: OutlinedButton.icon(
        onPressed: onPressed,
        icon: const Icon(Icons.expand_more),
        label: Text(label),
        style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
      ),
    );
  }
}

/// Keeps a TabBarView page (and the providers it watches) alive while
/// another tab is shown.
class _KeepAlive extends StatefulWidget {
  final Widget child;

  const _KeepAlive({required this.child});

  @override
  State<_KeepAlive> createState() => _KeepAliveState();
}

class _KeepAliveState extends State<_KeepAlive>
    with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return widget.child;
  }
}
