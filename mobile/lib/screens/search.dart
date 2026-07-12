/// Search: videos, playlists and channels mixed (kind icon on each tile).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/providers.dart';
import '../widgets/video_tile.dart';

class SearchScreen extends ConsumerStatefulWidget {
  const SearchScreen({super.key});

  @override
  ConsumerState<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends ConsumerState<SearchScreen> {
  String _query = '';
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
        title: TextField(
          controller: _controller,
          autofocus: true,
          textInputAction: TextInputAction.search,
          decoration: const InputDecoration(
            hintText: 'Search YouTube…',
            border: InputBorder.none,
          ),
          onSubmitted: (value) => setState(() => _query = value.trim()),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.search),
            onPressed: () => setState(() => _query = _controller.text.trim()),
          ),
        ],
      ),
      body: _query.isEmpty
          ? const Center(child: Text('Type a query'))
          : ref.watch(searchProvider(_query)).when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => Center(child: Text('$e')),
                data: (items) => ListView(
                  children: [for (final v in items) VideoTile(video: v)],
                ),
              ),
    );
  }
}
