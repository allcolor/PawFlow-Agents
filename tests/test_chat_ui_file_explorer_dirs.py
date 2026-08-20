"""File explorer: directory creation/deletion surfaces and confirmations."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_toolbar_offers_new_file_and_new_folder():
    source = _text("tasks/io/chat_ui/file_explorer.js")
    assert 'onclick="_feNewFile()" title="${t(\'newFile\')}"' in source
    assert 'onclick="_feNewDir()" title="${t(\'newFolder\')}"' in source


def test_empty_space_context_menu_creates_and_pastes():
    source = _text("tasks/io/chat_ui/file_explorer.js")
    # Without it, an empty directory offers NO way to create its first
    # entry (the row context menu needs a row to right-click).
    assert "function _feCtxEmpty(e)" in source
    assert "closest('tr[data-name]')" in source
    assert "_feCtxEmpty(e);" in source


def test_directory_delete_confirms_recursion():
    source = _text("tasks/io/chat_ui/file_explorer.js")
    assert "function _feIsDir(name)" in source
    assert "t('deleteDirConfirm',{name:name})" in source
    assert "t('deleteItemsDirConfirm',{label:label})" in source
    import json
    for lang in ("en", "fr", "es"):
        data = json.loads(_text(f"tasks/io/chat_ui/i18n/{lang}.json"))
        assert "deleteDirConfirm" in data, lang
        assert "deleteItemsDirConfirm" in data, lang
        assert "{name}" in data["deleteDirConfirm"], lang


def test_relay_delete_is_recursive_and_mkdir_exists():
    source = _text("tools/_fs_read.py")
    # The UI relies on these semantics: mkdir -p and recursive delete.
    assert "shutil.rmtree(p)" in source
    assert "mkdir(parents=True, exist_ok=True)" in source
