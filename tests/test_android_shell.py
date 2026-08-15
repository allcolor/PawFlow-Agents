"""Android shell: edge-to-edge insets, keyboard resize, collapsible chrome.

targetSdk 35 enforces edge-to-edge on Android 15+. Without insets handling
the webchat composer sat under the navigation bar and the WebView jumped
while typing. And the native chrome (toolbar + tab strip) folds away to the
right like a drawer behind a floating grip so the webchat gets the whole
screen.
"""

from pathlib import Path

MAIN = Path("pawflow-android/app/src/main/java/org/allcolor/pawflow/"
            "MainActivity.java").read_text(encoding="utf-8")
MANIFEST = Path("pawflow-android/app/src/main/AndroidManifest.xml"
                ).read_text(encoding="utf-8")


def test_every_screen_root_goes_through_the_insets_helper():
    assert "private void setScreen(View root)" in MAIN
    assert "WindowInsets.Type.systemBars()" in MAIN
    assert "WindowInsets.Type.displayCutout()" in MAIN
    assert "WindowInsets.Type.ime()" in MAIN
    assert "WindowInsets.CONSUMED" in MAIN
    # Gated: older releases keep the classic decor-fitted layout.
    assert "Build.VERSION.SDK_INT >= 35" in MAIN
    # Exactly one raw setContentView remains — the helper's own.
    assert MAIN.count("setContentView(") == 1
    assert MAIN.count("setScreen(") >= 5  # definition + the four screens


def test_keyboard_resizes_the_layout():
    assert 'android:windowSoftInputMode="adjustResize"' in MANIFEST


def test_webchat_chrome_folds_behind_a_floating_grip():
    assert 'Button grip = button("\\u2261");' in MAIN
    assert "chrome.animate().translationX(chrome.getWidth())" in MAIN
    assert "chrome.setVisibility(View.GONE)" in MAIN
    # The grip survives tab switches: it lives beside webContainer, which is
    # cleared by switchChatTab, never inside it.
    assert "webStack.addView(grip, gripParams);" in MAIN
    assert "webContainer.addView(grip" not in MAIN


def test_webview_downloads_go_through_download_manager():
    # A WebView silently ignores downloads without a DownloadListener.
    assert "view.setDownloadListener(" in MAIN
    assert "android.app.DownloadManager.Request" in MAIN
    # Authenticated FileStore URLs need the session cookie.
    assert 'request.addRequestHeader("Cookie", cookies);' in MAIN
    assert "DIRECTORY_DOWNLOADS" in MAIN
    assert "guessFileName" in MAIN


def test_inset_strips_match_the_app_chrome():
    assert "root.setBackgroundColor(getColor(R.color.pawflow_navy));" in MAIN
