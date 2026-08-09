import shutil
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


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_file_explorer_preview_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)
