from pathlib import Path


def test_file_explorer_status_does_not_shadow_translation_function():
    src = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")

    assert "let t=t(" not in src
    assert "let statusText=t('itemsCount'" in src
