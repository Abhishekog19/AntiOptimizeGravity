"""Diagnostic v2: verifies title-based panel detection on live CDP."""
import sys, json, urllib.request
sys.path.insert(0, 'notifier')
from notifier import _is_settings_panel, cdp_evaluate, _get_all_page_targets

try:
    targets = _get_all_page_targets(9222)
except Exception as e:
    print(f'CDP not reachable: {e}')
    sys.exit(1)

pages = targets
print(f'Total CDP page targets: {len(pages)}')
print()

settings_pages = [t for t in pages if _is_settings_panel(t)]
main_pages     = [t for t in pages if not _is_settings_panel(t)]

print(f'Settings panel targets ({len(settings_pages)}):')
for t in settings_pages:
    print(f'  [{t["id"][:8]}] title={t.get("title","")!r}  url={t["url"][:70]}')

print()
print(f'Main editor targets ({len(main_pages)}):')
for t in main_pages:
    print(f'  [{t["id"][:8]}] title={t.get("title","")!r}  url={t["url"][:70]}')
    ws = t.get('webSocketDebuggerUrl', '')
    if ws:
        title_r = cdp_evaluate(t, 'document.title', timeout=3.0)
        print(f'  document.title = {title_r!r}')
    else:
        print('  NO WebSocket URL')
    print()

if not main_pages:
    print('WARNING: No main editor targets found!')
    print('This means all CDP targets are being classified as Settings panel.')
    print('Titles seen:', [t.get("title","") for t in pages])

print('=== DONE ===')
