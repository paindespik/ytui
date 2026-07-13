/// Search: videos, playlists and channels mixed (kind icon on each tile).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/providers.dart';
import '../theme.dart';
import '../widgets/app_state_views.dart';
import '../widgets/responsive.dart';
import '../widgets/video_tile.dart';

class SearchScreen extends ConsumerStatefulWidget {
  const SearchScreen({super.key});

  @override
  ConsumerState<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends ConsumerState<SearchScreen> {
  String _query = '';
  String _source = 'youtube';
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Container(
          height: 40,
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(kRadiusLg),
          ),
          child: TextField(
            controller: _controller,
            autofocus: true,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              hintText: 'Search ${_source == 'odysee' ? 'Odysee' : 'YouTube'}…',
              prefixIcon: const Icon(Icons.search),
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(vertical: 10),
            ),
            onSubmitted: (value) => setState(() => _query = value.trim()),
          ),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.arrow_forward),
            tooltip: 'Rechercher',
            onPressed: () => setState(() => _query = _controller.text.trim()),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: kGutter, vertical: 8),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(
                  value: 'youtube',
                  label: Text('YouTube'),
                  icon: Icon(Icons.play_circle_outline),
                ),
                ButtonSegment(
                  value: 'odysee',
                  label: Text('Odysee'),
                  icon: Icon(Icons.explore_outlined),
                ),
              ],
              selected: {_source},
              onSelectionChanged: (selection) =>
                  setState(() => _source = selection.first),
            ),
          ),
          Expanded(
            child: _query.isEmpty
                ? const AppEmpty(
                    icon: Icons.search,
                    message: 'Enter a search term to find videos',
                  )
                : ref.watch(searchProvider((_query, _source))).when(
                      loading: () => const AppLoading(),
                      error: (e, _) => AppError.from(e,
                          onRetry: () => ref
                              .invalidate(searchProvider((_query, _source)))),
                      data: (items) => items.isEmpty
                          ? const AppEmpty(
                              icon: Icons.search_off,
                              message: 'No results found',
                            )
                          : ResponsiveCenter(
                              child: ListView(
                                children: [
                                  for (final v in items) VideoTile(video: v)
                                ],
                              ),
                            ),
                    ),
          ),
        ],
      ),
    );
  }
}
