/// Channel screen: latest videos + follow button.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../state/providers.dart';
import '../widgets/video_tile.dart';

class ChannelScreen extends ConsumerWidget {
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
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(channelVideosProvider((channelId, platform)));

    return Scaffold(
      appBar: AppBar(
        title: Text(data.valueOrNull?.$2.isNotEmpty == true
            ? data.valueOrNull!.$2
            : title),
        actions: [
          IconButton(
            icon: const Icon(Icons.person_add),
            tooltip: 'Follow',
            onPressed: () async {
              final messenger = ScaffoldMessenger.of(context);
              final ref_ = switch (platform) {
                'bitchute' => 'bitchute:$channelId',
                'odysee' => 'odysee:$channelId',
                _ => channelId,
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
      body: data.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('$e')),
        data: (result) => ListView(
          children: [for (final v in result.$1) VideoTile(video: v)],
        ),
      ),
    );
  }
}
