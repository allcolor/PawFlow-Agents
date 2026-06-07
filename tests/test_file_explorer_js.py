from pathlib import Path


def test_file_explorer_status_does_not_shadow_translation_function():
    src = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")

    assert "let t=t(" not in src
    assert "let statusText=t('itemsCount'" in src


def test_file_explorer_template_evaluates_i18n_labels():
    src = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")

    assert "placeholder=\"' + t('searchPlaceholder') + '\"" not in src
    assert "title=\"' + t('refresh') + '\"" not in src
    assert "' + t('upload') + '" not in src
    assert "' + t('fileName') + '" not in src
    assert "' + t('fileSize') + '" not in src
    assert "' + t('modified') + '" not in src
    assert "${t('searchPlaceholder')}" in src
    assert "${t('fileName')}" in src
    assert "${t('fileSize')}" in src
    assert "${t('modified')}" in src
