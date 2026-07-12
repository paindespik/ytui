/// Settings: server URL + token with a connection test, followed channels.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../state/providers.dart';
import '../state/settings.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  /// First-launch mode: no back navigation until the server is configured.
  final bool firstLaunch;

  const SettingsScreen({super.key, this.firstLaunch = false});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  late final TextEditingController _urlController;
  late final TextEditingController _tokenController;
  String? _testResult;
  bool _testing = false;

  @override
  void initState() {
    super.initState();
    final settings = ref.read(settingsProvider);
    _urlController = TextEditingController(text: settings.url);
    _tokenController = TextEditingController(text: settings.token);
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    super.dispose();
  }

  Future<void> _testConnection() async {
    setState(() {
      _testing = true;
      _testResult = null;
    });
    final api = YtuiApi(
      baseUrl: _urlController.text.trim().replaceAll(RegExp(r'/+$'), ''),
      token: _tokenController.text.trim(),
    );
    String result;
    try {
      final health = await api.health();
      // /health is public: also check an authenticated endpoint.
      await api.watchedIds();
      result = '✓ Connected (server ${health['version'] ?? '?'})';
    } on ApiException catch (e) {
      result = e.statusCode == 401
          ? '✗ Server reachable but the token is wrong'
          : '✗ ${e.toString()}';
    } catch (e) {
      result = '✗ $e';
    }
    if (mounted) {
      setState(() {
        _testing = false;
        _testResult = result;
      });
    }
  }

  Future<void> _save() async {
    await ref
        .read(settingsProvider.notifier)
        .save(_urlController.text, _tokenController.text);
    ref.invalidate(feedProvider);
    if (mounted && widget.firstLaunch) {
      // main.dart rebuilds on settingsProvider and shows the home feed.
      setState(() {});
    } else if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Saved')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final channels = widget.firstLaunch ? null : ref.watch(channelsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        automaticallyImplyLeading: !widget.firstLaunch,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Server', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _urlController,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(
              labelText: 'Server URL',
              hintText: 'https://ytui.example.com',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _tokenController,
            obscureText: true,
            decoration: const InputDecoration(
              labelText: 'API token',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              OutlinedButton(
                onPressed: _testing ? null : _testConnection,
                child: _testing
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Test connection'),
              ),
              const SizedBox(width: 12),
              FilledButton(onPressed: _save, child: const Text('Save')),
            ],
          ),
          if (_testResult != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(_testResult!),
            ),
          if (!widget.firstLaunch) ...[
            const Divider(height: 32),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('Followed channels',
                    style: Theme.of(context).textTheme.titleMedium),
                IconButton(
                  icon: const Icon(Icons.add),
                  onPressed: _addChannel,
                ),
              ],
            ),
            channels!.when(
              loading: () => const Padding(
                padding: EdgeInsets.all(16),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (e, _) => Text('$e'),
              data: (items) => Column(
                children: [
                  for (final c in items)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(c.title.isEmpty ? c.ref : c.title),
                      subtitle: Text('${c.platform} · ${c.ref}'),
                      trailing: IconButton(
                        icon: const Icon(Icons.delete_outline),
                        onPressed: () async {
                          await ref
                              .read(apiProvider)
                              .unfollowChannel(c.channelId);
                          ref.invalidate(channelsProvider);
                          ref.invalidate(feedProvider);
                        },
                      ),
                    ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Future<void> _addChannel() async {
    final controller = TextEditingController();
    final refValue = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Follow a channel'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: 'UC…, @handle or bitchute:slug',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, controller.text.trim()),
            child: const Text('Follow'),
          ),
        ],
      ),
    );
    if (refValue == null || refValue.isEmpty || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(apiProvider).followChannel(refValue);
      ref.invalidate(channelsProvider);
      ref.invalidate(feedProvider);
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.statusCode == 409
              ? 'Already followed'
              : e.statusCode == 404
                  ? 'Channel not found'
                  : e.toString())));
    }
  }
}
