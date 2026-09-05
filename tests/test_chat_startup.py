"""Startup cache keys, locale selection and ordered local vendor contracts."""

import hashlib
import io
import json
import os
import re
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jinja2 import FileSystemLoader

from tasks.io import serve_chat_ui as ui


def _catalogs(html):
    return json.loads(html.split('window.PAWFLOW_I18N_CATALOGS=', 1)[1].split(';</script>', 1)[0])


def test_local_deferred_dependencies_and_content_keys():
    html = ui.render_chat_page()
    tags = re.findall(r'<script([^>]+)src="([^"]+)"[^>]*>', html)
    assert all('defer' in attrs and url.startswith('/chat/js/') for attrs, url in tags)
    modules = [url.split('/chat/js/', 1)[1].split('?')[0] for _, url in tags]
    assert modules == list(ui._VENDOR_ASSETS[:2]) + [
        name for name in ui._JS_MODULES
        if (ui._CHAT_UI_DIR / name).is_file()
        and name != 'plans_panel.js' and name not in ui._LAZY_JS_MODULES
    ]
    assert modules.index('vendor/rxjs-7.8.2.umd.min.js') < modules.index('rxbus.js')
    assert modules.index('vendor/highlight-11.9.0.min.js') < modules.index('messages_markdown.js')
    assert 'usage_dashboard.js' not in modules
    assert 'window.PAWFLOW_LAZY_URLS=' in html
    assert re.search(r'window.PAWFLOW_ASSET_VERSION="[0-9a-f]{8}"', html)
    for path, version in re.findall(r'/chat/js/([^"?]+)[?]v=([0-9a-f]+)', html):
        assert version == hashlib.sha256((ui._CHAT_UI_DIR / path).read_bytes()).hexdigest()[:16]
    assert html.index('window.__pawflowAssetLoadFailed=') < html.index('src="/chat/js/vendor/')


@pytest.fixture
def assets(tmp_path, monkeypatch):
    for name, content in {
        'first.js': 'window.first = 1;', 'second.js': 'window.second = 2;',
        'css/base.css': 'body{color:red}', 'templates/chat.html': '<body>chat</body>',
        'i18n/en.json': '{"ready":"Ready"}',
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(ui, '_CHAT_UI_DIR', tmp_path)
    monkeypatch.setattr(ui, '_CSS_DIR', tmp_path / 'css')
    monkeypatch.setattr(ui, '_TEMPLATES_DIR', tmp_path / 'templates')
    monkeypatch.setattr(ui, '_JS_MODULES', ['first.js', 'second.js'])
    monkeypatch.setattr(ui, '_VENDOR_ASSETS', ())
    monkeypatch.setattr(ui, '_asset_manifest_cache', None)
    monkeypatch.setattr(ui, '_asset_digest_cache', {})
    clock = [100.0]
    monkeypatch.setattr(ui.time, 'monotonic', lambda: clock[0])
    yield tmp_path, clock
    ui._invalidate_asset_cache()


def test_manifest_coalesces_warm_stat_scans_and_byte_reads(assets, monkeypatch):
    _, clock = assets
    signatures, reads = [], []
    signature = ui._asset_signature
    read_bytes = Path.read_bytes

    def scan():
        signatures.append(True)
        return signature()

    def read(path):
        reads.append(path.name)
        return read_bytes(path)

    monkeypatch.setattr(ui, '_asset_signature', scan)
    monkeypatch.setattr(Path, 'read_bytes', read)
    first = ui._asset_manifest()
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(result == first for result in pool.map(lambda _: ui._asset_manifest(), range(20)))
    assert len(signatures) == 1
    assert len(reads) == 4
    clock[0] += 1.1
    assert ui._asset_manifest() == first
    assert len(signatures) == 2
    assert len(reads) == 4


def test_only_changed_bytes_invalidate_asset_urls_and_templates_keep_reload_signal(assets):
    root, clock = assets
    sig, before = ui._asset_manifest()
    path = root / 'first.js'
    old_stat = path.stat()
    path.write_text('window.first = 9;')
    os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    clock[0] += 1.1
    _, after = ui._asset_manifest()
    assert {name for name in before if before[name] != after[name]} == {'first.js'}
    (root / 'templates/chat.html').write_text('<body>new shell</body>')
    clock[0] += 1.1
    new_sig, template_versions = ui._asset_manifest()
    assert template_versions == after
    assert ui._compute_js_version(new_sig) != ui._compute_js_version(sig)
    path.write_bytes(path.read_bytes())
    clock[0] += 1.1
    assert ui._asset_manifest()[1] == after


def test_explicit_hotpatch_invalidation_rehashes_even_preserved_signatures(assets, monkeypatch):
    root, _ = assets
    signature, before = ui._asset_manifest()
    monkeypatch.setattr(ui, '_asset_signature', lambda: signature)
    (root / 'second.js').write_text('window.second = 8;')
    assert ui._asset_manifest()[1] == before
    ui._invalidate_asset_cache()
    after = ui._asset_manifest()[1]
    assert before['first.js'] == after['first.js']
    assert before['second.js'] != after['second.js']


@pytest.mark.parametrize('mtime_ns', [0, -1_000_000_000])
@pytest.mark.parametrize('empty', [False, True])
def test_manifest_keeps_epoch_assets_and_distinguishes_missing_files(assets, monkeypatch, mtime_ns, empty):
    root, clock = assets
    names = ['first.js', 'second.js', 'css/base.css', 'i18n/en.json', 'vendor.js']
    (root / 'vendor.js').write_text('window.vendor = 1;')
    monkeypatch.setattr(ui, '_VENDOR_ASSETS', ('vendor.js', 'missing.js'))
    for name in names:
        path = root / name
        if empty:
            path.write_bytes(b'')
        os.utime(path, ns=(mtime_ns, mtime_ns))
    expected = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()[:16] for name in names}
    assert ui._asset_manifest()[1] == expected
    (root / 'first.js').unlink()
    clock[0] += 1.1
    assert ui._asset_manifest()[1] == {name: value for name, value in expected.items() if name != 'first.js'}
    (root / 'first.js').write_bytes(b'window.restored = 1;')
    os.utime(root / 'first.js', ns=(mtime_ns, mtime_ns))
    clock[0] += 1.1
    expected['first.js'] = hashlib.sha256((root / 'first.js').read_bytes()).hexdigest()[:16]
    assert ui._asset_manifest()[1] == expected


def test_render_chat_page_from_epoch_hotpatch_archive(tmp_path, monkeypatch):
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode='w') as archive:
        for path in sorted(ui._CHAT_UI_DIR.rglob('*')):
            if not path.is_file():
                continue
            data = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(ui._CHAT_UI_DIR).as_posix())
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    archive_bytes.seek(0)
    with tarfile.open(fileobj=archive_bytes) as archive:
        archive.extractall(tmp_path, filter='data')
    assert (tmp_path / 'css/10_chrome.css').stat().st_mtime_ns == 0
    monkeypatch.setattr(ui, '_CHAT_UI_DIR', tmp_path)
    monkeypatch.setattr(ui, '_CSS_DIR', tmp_path / 'css')
    monkeypatch.setattr(ui, '_TEMPLATES_DIR', tmp_path / 'templates')
    monkeypatch.setattr(ui, '_env', ui._env.overlay(loader=FileSystemLoader(str(tmp_path / 'templates'))))
    monkeypatch.setattr(ui, '_asset_manifest_cache', None)
    monkeypatch.setattr(ui, '_asset_digest_cache', {})
    monkeypatch.setattr(ui, '_i18n_block_cache', {})
    html = ui.render_chat_page(language='fr')
    for name in [*ui._JS_MODULES, *ui._VENDOR_ASSETS, *('css/' + name for name in ui._CSS_MODULES)]:
        if name != 'plans_panel.js' and (tmp_path / name).is_file():
            version = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()[:16]
            assert '/chat/js/' + name + '?v=' + version in html
    assert set(_catalogs(html)) == {'en', 'fr'}


@pytest.mark.parametrize(('preference', 'expected'), [
    ('en', {'en'}), ('fr-FR', {'en', 'fr'}), ('es_ES', {'en', 'es'}),
    ('de, fr;q=0.4, es;q=0.9', {'en', 'es'}),
    ('fr;q=0, en;q=0.8', {'en'}),
    ('../../secret', {'en'}), ('fr;q=broken', {'en'}),
])
def test_selected_locale_and_english_only(preference, expected):
    html = ui.render_chat_page(language=preference)
    assert set(_catalogs(html)) == expected
    assert _catalogs(html)['en']['ready'] == 'Ready'


def test_locale_json_cannot_close_the_boot_script(tmp_path, monkeypatch):
    (tmp_path / 'i18n').mkdir()
    (tmp_path / 'i18n/languages.json').write_text('[{"code":"en","label":"English"}]')
    (tmp_path / 'i18n/en.json').write_text(json.dumps({'ready': '</script><script>bad()</script>'}))
    monkeypatch.setattr(ui, '_CHAT_UI_DIR', tmp_path)
    monkeypatch.setattr(ui, '_i18n_block_cache', {})
    html = ui._initial_i18n_block()
    assert html.count('</script>') == 1
    assert _catalogs(html)['en']['ready'] == '</script><script>bad()</script>'


def test_request_language_cookie_precedes_accept_language(monkeypatch):
    from core import FlowFile
    monkeypatch.setattr(ui, '_initial_theme_block', lambda _: '')
    task = ui.ServeChatUITask({})
    request = FlowFile(content=b'')
    request.set_attribute('http.header.cookie', 'pawflow_language=fr')
    request.set_attribute('http.header.accept-language', 'es-ES, en;q=0.8')
    assert set(_catalogs(task.execute(request)[0].get_content().decode())) == {'en', 'fr'}
    request.set_attribute('http.header.cookie', '')
    assert set(_catalogs(task.execute(request)[0].get_content().decode())) == {'en', 'es'}
