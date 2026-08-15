"""Deep-linking a conversation: URL param, context menu, Android new tab.

A specific conversation opens via `?conversation_id=<id>` (browser and app).
The sidebar's right-click menu offers "Open in new tab", and the Android
WebView turns window.open/target=_blank into a new native chat tab instead
of silently dropping it.
"""

import json
from pathlib import Path

MENU_JS = Path("tasks/io/chat_ui/conversations_menu.js").read_text(encoding="utf-8")
BOOT_JS = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")
MAIN = Path("pawflow-android/app/src/main/java/org/allcolor/pawflow/"
            "MainActivity.java").read_text(encoding="utf-8")


def test_boot_resumes_the_conversation_named_in_the_url():
    assert ("new URLSearchParams(window.location.search)"
            ".get('conversation_id')") in BOOT_JS
    assert "resumeConv(requestedCid)" in BOOT_JS


def test_context_menu_offers_open_in_new_tab():
    assert "t('openInNewTab')" in MENU_JS
    assert "'?conversation_id=' + encodeURIComponent(cid)" in MENU_JS
    assert "'_blank'" in MENU_JS
    for lang in ("en", "fr", "es"):
        data = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json")
                          .read_text(encoding="utf-8"))
        assert data["openInNewTab"], lang


def test_android_window_open_becomes_a_native_chat_tab():
    assert "settings.setSupportMultipleWindows(true);" in MAIN
    assert "public boolean onCreateWindow(" in MAIN
    assert "transport.setWebView(tab);" in MAIN
    assert "resultMsg.sendToTarget();" in MAIN
