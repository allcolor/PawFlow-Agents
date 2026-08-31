import shutil

from chat_ui_testing import rendered_chat_html
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "file_explorer_spec.js"


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


def test_file_explorer_streams_uploads_and_keeps_mobile_toolbar_visible():
    src = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")
    template = rendered_chat_html()

    upload = src[src.index("async function _feUploadFiles(files)"):src.index("\nfunction _feCopyToStore")]
    assert "uploadFileToRelay(" in upload
    assert "FileReader" not in upload
    assert "readAsDataURL" not in upload
    assert "base64" not in upload
    assert "if(_fe.surface)_feNav(_fe.path);" in upload
    mobile = template[template.index("@media (max-width:768px){", template.index("/* File Explorer */")):]
    assert ".fe-toolbar{display:grid;" in mobile
    assert ".fe-panel{background:#1a1a2e;width:100%;height:100%;" in template
    assert ".fe-overlay" not in template


def test_file_explorer_preview_uses_relay_read_and_blob_viewer():
    explorer = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")
    viewer = Path("tasks/io/chat_ui/file_viewer.js").read_text(encoding="utf-8")
    preview = explorer[
        explorer.index("function _fePreview(name)") : explorer.index("\nfunction _feSearch")
    ]

    assert "action$('fs_read_file'" in preview
    assert "openFileViewer(URL.createObjectURL(blob),name,blob)" in preview
    assert "'/fs/'" not in preview
    assert "function openFileViewer(filenameOrUrl, displayName, sourceBlob)" in viewer
    assert "!filenameOrUrl.startsWith('blob:')" in viewer


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_file_explorer_is_valid_javascript():
    proc = subprocess.run(
        ["node", "--check", "tasks/io/chat_ui/file_explorer.js"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0, (
        "JavaScript syntax check failed:\n" + proc.stdout + proc.stderr)


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_file_explorer_preview_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)
