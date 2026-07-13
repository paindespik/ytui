/// Settings: server URL + token with a connection test, followed channels.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/client.dart';
import '../state/providers.dart';
import '../state/settings.dart';
import '../theme.dart';
import '../widgets/responsive.dart';

/// Success feedback color (green) — distinct from the brand-red primary.
const _kSuccessBg = Color(0xFF1B5E20);
const _kSuccessIcon = Color(0xFF4CAF50);

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
    final colorScheme = Theme.of(context).colorScheme;
    final isSuccess = _testResult?.startsWith('✓') ?? false;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        automaticallyImplyLeading: !widget.firstLaunch,
      ),
      body: ResponsiveCenter(
        child: ListView(
          padding: const EdgeInsets.all(kGutter),
          children: [
            // ─────────────────────── Server Section ───────────────────────
            Card(
              child: Padding(
                padding: const EdgeInsets.all(kGutter),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Server', style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _urlController,
                      keyboardType: TextInputType.url,
                      decoration: const InputDecoration(
                        labelText: 'Server URL',
                        hintText: 'https://ytui.example.com',
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _tokenController,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'API token',
                      ),
                    ),
                    const SizedBox(height: 16),
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
                    if (_testResult != null) ...[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 10,
                        ),
                        decoration: BoxDecoration(
                          color: isSuccess
                              ? _kSuccessBg.withValues(alpha: 0.4)
                              : colorScheme.errorContainer,
                          borderRadius: BorderRadius.circular(kRadiusSm),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              isSuccess ? Icons.check_circle : Icons.error,
                              size: 18,
                              color: isSuccess
                                  ? _kSuccessIcon
                                  : colorScheme.error,
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                _testResult!,
                                style: TextStyle(
                                  color: isSuccess
                                      ? colorScheme.onSurface
                                      : colorScheme.onErrorContainer,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),

            // ─────────────────── Followed Channels Section ───────────────────
            if (!widget.firstLaunch) ...[
              const SizedBox(height: 24),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(kGutter),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            'Followed channels',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          IconButton(
                            icon: const Icon(Icons.add),
                            onPressed: _addChannel,
                            tooltip: 'Follow a channel',
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      channels!.when(
                        loading: () => const Padding(
                          padding: EdgeInsets.symmetric(vertical: 24),
                          child: Center(child: CircularProgressIndicator()),
                        ),
                        error: (e, _) => Padding(
                          padding: const EdgeInsets.symmetric(vertical: 16),
                          child: Row(
                            children: [
                              Icon(Icons.error_outline,
                                  size: 18, color: colorScheme.error),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  '$e',
                                  style: TextStyle(color: colorScheme.error),
                                ),
                              ),
                            ],
                          ),
                        ),
                        data: (items) => items.isEmpty
                            ? Padding(
                                padding: const EdgeInsets.symmetric(vertical: 16),
                                child: Text(
                                  'No channels followed yet',
                                  style: TextStyle(
                                    color: colorScheme.onSurfaceVariant,
                                  ),
                                ),
                              )
                            : Column(
                                children: [
                                  for (final c in items)
                                    ListTile(
                                      contentPadding: EdgeInsets.zero,
                                      title:
                                          Text(c.title.isEmpty ? c.ref : c.title),
                                      subtitle: Text('${c.platform} · ${c.ref}'),
                                      trailing: IconButton(
                                        icon: const Icon(Icons.delete_outline),
                                        tooltip: 'Unfollow',
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
                  ),
                ),
              ),
            ],
          ],
        ),
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
            hintText: 'UC…, @handle, bitchute:slug or odysee:@name:claim',
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
