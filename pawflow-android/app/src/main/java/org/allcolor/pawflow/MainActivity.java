package org.allcolor.pawflow;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.browser.customtabs.CustomTabsIntent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 41;
    private static final String OAUTH_PREFS = "pawflow.mobile.oauth";
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final CryptoStore crypto = new CryptoStore();
    private ServerStore servers;
    private ServerProfile currentServer;
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private boolean webScreen;
    private final ChatTabs<WebView> chatTabs = new ChatTabs<>();
    private LinearLayout tabBar;
    private FrameLayout webContainer;

    @Override
    protected void onCreate(Bundle state) {
        setTheme(org.allcolor.pawflow.R.style.Theme_PawFlow);
        super.onCreate(state);
        servers = new ServerStore(this);
        if (!handleOAuthIntent(getIntent())) {
            showServers();
        }
    }

    // targetSdk 35 enforces edge-to-edge on Android 15+: without insets the
    // composer sits under the navigation bar and the keyboard resize is never
    // applied to the layout (the WebView jumps while typing). The screen root
    // absorbs the system bars, display cutout and IME as padding. Older
    // Android releases keep the classic decor-fitted behavior and need none.
    private void setScreen(View root) {
        if (Build.VERSION.SDK_INT >= 35) {
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars()
                                | WindowInsets.Type.displayCutout()
                                | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return WindowInsets.CONSUMED;
            });
        }
        setContentView(root);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleOAuthIntent(intent);
    }

    private void showServers() {
        webScreen = false;
        currentServer = null;
        destroyChatTabs();
        LinearLayout page = column();
        page.setPadding(dp(20), dp(20), dp(20), dp(20));
        TextView title = title("PawFlow servers");
        page.addView(title);
        TextView hint = text("Choose a server or add a new one.");
        hint.setPadding(0, dp(4), 0, dp(18));
        page.addView(hint);

        for (ServerProfile server : servers.list()) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            Button open = button(server.name + "\n" + server.baseUrl);
            row.addView(open, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1));
            Button remove = button("×");
            row.addView(remove, new LinearLayout.LayoutParams(dp(52),
                    ViewGroup.LayoutParams.WRAP_CONTENT));
            open.setOnClickListener(view -> connect(server));
            open.setOnLongClickListener(view -> {
                showServerDialog(server);
                return true;
            });
            remove.setOnClickListener(view -> confirmDelete(server));
            page.addView(row);
        }
        Button add = button("Add server");
        add.setOnClickListener(view -> showServerDialog(null));
        page.addView(add);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(page);
        setScreen(scroll);
    }

    private void showServerDialog(ServerProfile existing) {
        LinearLayout form = column();
        int padding = dp(20);
        form.setPadding(padding, 0, padding, 0);
        EditText name = input("Name", InputType.TYPE_CLASS_TEXT);
        EditText url = input("https://pawflow.example.org",
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        EditText key = input(existing == null ? "Gateway key" : "New gateway key (leave blank to keep)",
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        if (existing != null) {
            name.setText(existing.name);
            url.setText(existing.baseUrl);
        }
        form.addView(name);
        form.addView(url);
        form.addView(key);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(existing == null ? "Add PawFlow server" : "Edit PawFlow server")
                .setView(form)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Save", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(view -> {
                    try {
                        String gateway = key.getText().toString();
                        if (gateway.isBlank() && existing != null) {
                            gateway = existing.gatewayKey;
                        }
                        ServerProfile profile = new ServerProfile(
                                existing == null ? UUID.randomUUID().toString() : existing.id,
                                name.getText().toString(), url.getText().toString(), gateway);
                        servers.save(profile);
                        dialog.dismiss();
                        showServers();
                    } catch (RuntimeException error) {
                        key.setError(error.getMessage());
                    }
                }));
        dialog.show();
    }

    private void confirmDelete(ServerProfile server) {
        new AlertDialog.Builder(this)
                .setTitle("Remove " + server.name + "?")
                .setMessage("The encrypted gateway key and local profile will be deleted.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Remove", (dialog, which) -> {
                    servers.delete(server.id);
                    CookieManager.getInstance().setCookie(server.baseUrl,
                            "_pf_gw=; Path=/; Max-Age=0; Secure; HttpOnly");
                    CookieManager.getInstance().setCookie(server.baseUrl,
                            "pawflow_token=; Path=/; Max-Age=0; Secure; HttpOnly");
                    showServers();
                }).show();
    }

    private void connect(ServerProfile server) {
        currentServer = server;
        showBusy("Connecting to " + server.name + "…");
        network.execute(() -> {
            try {
                PawFlowApi.get(server, "/auth/mobile/providers");
                runOnUiThread(() -> showWebChat(server, "", ""));
            } catch (Exception error) {
                runOnUiThread(() -> showError("Connection failed", error));
            }
        });
    }

    private void showLogin(ServerProfile server) {
        destroyChatTabs();
        webScreen = false;
        currentServer = server;
        showBusy("Loading login methods…");
        network.execute(() -> {
            try {
                JSONArray providers = PawFlowApi.get(
                        server, "/auth/mobile/providers").getJSONArray("providers");
                runOnUiThread(() -> renderLogin(server, providers));
            } catch (Exception error) {
                runOnUiThread(() -> showError("Unable to load login methods", error));
            }
        });
    }

    private void renderLogin(ServerProfile server, JSONArray providers) {
        LinearLayout page = column();
        page.setPadding(dp(20), dp(20), dp(20), dp(20));
        Button back = button("← Servers");
        back.setOnClickListener(view -> showServers());
        page.addView(back);
        page.addView(title("Sign in to " + server.name));
        for (int index = 0; index < providers.length(); index++) {
            JSONObject provider = providers.optJSONObject(index);
            if (provider == null) {
                continue;
            }
            String type = provider.optString("type");
            if ("password".equals(type)) {
                addBuiltinForm(page, server);
            } else if ("oauth2".equals(type)) {
                Button oauth = button("Continue with " + provider.optString("display_name"));
                String name = provider.optString("name");
                oauth.setOnClickListener(view -> startOAuth(server, name));
                page.addView(oauth);
            }
        }
        ScrollView scroll = new ScrollView(this);
        scroll.addView(page);
        setScreen(scroll);
    }

    private void addBuiltinForm(LinearLayout page, ServerProfile server) {
        EditText username = input("Username", InputType.TYPE_CLASS_TEXT);
        EditText password = input("Password",
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        Button login = button("Sign in with PawFlow");
        login.setOnClickListener(view -> builtinLogin(
                server, username.getText().toString(), password.getText().toString()));
        page.addView(username);
        page.addView(password);
        page.addView(login);
    }

    private void startOAuth(ServerProfile server, String provider) {
        Pkce pkce = Pkce.create();
        showBusy("Opening " + provider + "…");
        network.execute(() -> {
            try {
                JSONObject request = new JSONObject()
                        .put("provider", provider)
                        .put("code_challenge", pkce.challenge);
                JSONObject response = PawFlowApi.post(server, "/auth/mobile/start", request);
                savePendingOAuth(server.id, response.getString("flow_id"), pkce.verifier);
                Uri uri = Uri.parse(response.getString("authorization_url"));
                runOnUiThread(() -> new CustomTabsIntent.Builder()
                        .setShowTitle(true)
                        .build()
                        .launchUrl(this, uri));
            } catch (Exception error) {
                runOnUiThread(() -> showError("OAuth could not start", error));
            }
        });
    }

    private void builtinLogin(ServerProfile server, String username, String password) {
        Pkce pkce = Pkce.create();
        showBusy("Signing in…");
        network.execute(() -> {
            try {
                JSONObject request = new JSONObject()
                        .put("username", username)
                        .put("password", password)
                        .put("code_challenge", pkce.challenge);
                JSONObject response = PawFlowApi.post(
                        server, "/auth/mobile/builtin", request);
                String code = response.getString("handoff_code");
                runOnUiThread(() -> showWebChat(server, code, pkce.verifier));
            } catch (Exception error) {
                runOnUiThread(() -> showError("Sign-in failed", error));
            }
        });
    }

    private boolean handleOAuthIntent(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !"pawflow".equals(data.getScheme())
                || !"oauth".equals(data.getHost())) {
            return false;
        }
        String error = data.getQueryParameter("error");
        PendingOAuth pending = loadPendingOAuth();
        if (pending == null) {
            showError("OAuth callback expired", new IllegalStateException(
                    "Start the login again from the server profile."));
            return true;
        }
        ServerProfile server = servers.find(pending.serverId);
        String flowId = data.getQueryParameter("flow_id");
        clearPendingOAuth();
        if (server == null || !pending.flowId.equals(flowId)) {
            showError("OAuth callback rejected", new IllegalStateException(
                    server == null ? "Server profile not found" : "Invalid mobile flow"));
            return true;
        }
        if (error != null) {
            currentServer = server;
            showError("OAuth failed", new IllegalStateException(
                    error));
            return true;
        }
        String code = data.getQueryParameter("code");
        if (code == null || code.isBlank()) {
            showError("OAuth callback rejected", new IllegalStateException("Invalid mobile flow"));
            return true;
        }
        showWebChat(server, code, pending.verifier);
        return true;
    }

    private void showWebChat(ServerProfile server, String handoffCode, String verifier) {
        destroyChatTabs();
        webScreen = true;
        currentServer = server;
        LinearLayout root = column();
        // The inset padding (status/navigation bars) shows the root's own
        // background: match the app chrome instead of flashing white strips.
        root.setBackgroundColor(getColor(R.color.pawflow_navy));
        // The native chrome (toolbar + tab bar) folds away to the right like
        // a drawer so the webchat gets the whole screen; a small grip in the
        // top-right corner of the web area toggles it back.
        LinearLayout chrome = column();
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(8), dp(4), dp(8), dp(4));
        toolbar.setBackgroundColor(getColor(R.color.pawflow_navy));
        Button serverButton = button("Servers");
        Button reload = button("Reload");
        TextView name = text(server.name);
        name.setTextColor(Color.WHITE);
        name.setGravity(Gravity.CENTER);
        toolbar.addView(serverButton);
        toolbar.addView(name, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        toolbar.addView(reload);
        chrome.addView(toolbar);

        HorizontalScrollView tabScroller = new HorizontalScrollView(this);
        tabScroller.setHorizontalScrollBarEnabled(false);
        tabBar = new LinearLayout(this);
        tabBar.setOrientation(LinearLayout.HORIZONTAL);
        tabScroller.addView(tabBar);
        chrome.addView(tabScroller);
        root.addView(chrome);

        // webContainer is cleared on every tab switch, so the grip lives in
        // an enclosing stack that survives it.
        FrameLayout webStack = new FrameLayout(this);
        webContainer = new FrameLayout(this);
        webStack.addView(webContainer, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        // The chrome slides horizontally, so its grip is a VERTICAL tab
        // (narrow and tall, horizontal bars) hugging the right edge — the
        // same convention as the webchat grips.
        Button grip = button("\u2261");
        grip.setAlpha(0.75f);
        grip.setPadding(0, 0, 0, 0);
        grip.setMinWidth(0);
        grip.setMinimumWidth(0);
        FrameLayout.LayoutParams gripParams = new FrameLayout.LayoutParams(
                dp(26), dp(64), Gravity.TOP | Gravity.END);
        gripParams.topMargin = dp(2);
        gripParams.rightMargin = 0;
        webStack.addView(grip, gripParams);
        root.addView(webStack, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        grip.setOnClickListener(view -> {
            if (chrome.getVisibility() == View.VISIBLE) {
                chrome.animate().translationX(chrome.getWidth()).alpha(0f)
                        .setDuration(180)
                        .withEndAction(() -> chrome.setVisibility(View.GONE));
            } else {
                chrome.setVisibility(View.VISIBLE);
                chrome.animate().translationX(0f).alpha(1f).setDuration(180);
            }
        });
        // The grip must float above the WebView added later by switchChatTab.
        grip.setElevation(dp(4));
        serverButton.setOnClickListener(view -> showServers());
        reload.setOnClickListener(view -> {
            if (webView != null) {
                webView.reload();
            }
        });
        setScreen(root);
        addChatTab(server, handoffCode, verifier);
    }

    private void addChatTab(ServerProfile server, String handoffCode, String verifier) {
        WebView tab = new WebView(this);
        configureWebView(tab, server);
        int index = chatTabs.add(tab);
        switchChatTab(index);

        if (handoffCode == null) {
            tab.loadUrl(server.baseUrl + "/chat");
        } else if (!handoffCode.isBlank()) {
            String form = "code=" + encode(handoffCode)
                    + "&code_verifier=" + encode(verifier);
            tab.postUrl(server.baseUrl + "/auth/mobile/consume",
                    form.getBytes(StandardCharsets.UTF_8));
        } else {
            String form = "secret=" + encode(server.gatewayKey) + "&next=%2Fchat";
            tab.postUrl(server.baseUrl + "/_gateway",
                    form.getBytes(StandardCharsets.UTF_8));
        }
    }

    private void switchChatTab(int index) {
        chatTabs.activate(index);
        webView = chatTabs.active();
        webContainer.removeAllViews();
        webContainer.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        renderTabBar();
    }

    private void renderTabBar() {
        tabBar.removeAllViews();
        for (int index = 0; index < chatTabs.size(); index++) {
            final int tabIndex = index;
            Button select = button("Chat " + (index + 1));
            select.setEnabled(index != chatTabs.activeIndex());
            select.setOnClickListener(view -> switchChatTab(tabIndex));
            tabBar.addView(select);

            Button close = button("×");
            close.setContentDescription("Close Chat " + (index + 1));
            close.setOnClickListener(view -> closeChatTab(tabIndex));
            tabBar.addView(close, new LinearLayout.LayoutParams(
                    dp(48), ViewGroup.LayoutParams.WRAP_CONTENT));
        }
        Button add = button("+");
        add.setContentDescription("Open another chat");
        add.setOnClickListener(view -> addChatTab(currentServer, null, null));
        tabBar.addView(add, new LinearLayout.LayoutParams(
                dp(56), ViewGroup.LayoutParams.WRAP_CONTENT));
    }

    private void closeChatTab(int index) {
        WebView closed = chatTabs.close(index);
        if (closed.getParent() instanceof ViewGroup) {
            ((ViewGroup) closed.getParent()).removeView(closed);
        }
        closed.stopLoading();
        closed.destroy();
        if (chatTabs.size() == 0) {
            showServers();
        } else {
            switchChatTab(chatTabs.activeIndex());
        }
    }

    private void destroyChatTabs() {
        for (WebView tab : chatTabs.clear()) {
            if (tab.getParent() instanceof ViewGroup) {
                ((ViewGroup) tab.getParent()).removeView(tab);
            }
            tab.stopLoading();
            tab.destroy();
        }
        webView = null;
        tabBar = null;
        webContainer = null;
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView(WebView view, ServerProfile server) {
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSafeBrowsingEnabled(true);
        // Without a DownloadListener a WebView silently ignores downloads
        // (files shared by the agent, exports). Hand them to DownloadManager
        // with the session cookie so authenticated FileStore URLs work; the
        // file lands in the system Downloads folder with a notification.
        view.setDownloadListener((url, userAgent, contentDisposition,
                mimeType, contentLength) -> {
            if (!url.startsWith("http")) {
                Toast.makeText(this, "Unsupported download URL",
                        Toast.LENGTH_LONG).show();
                return;
            }
            try {
                android.app.DownloadManager.Request request =
                        new android.app.DownloadManager.Request(Uri.parse(url));
                String cookies = CookieManager.getInstance().getCookie(url);
                if (cookies != null) {
                    request.addRequestHeader("Cookie", cookies);
                }
                request.addRequestHeader("User-Agent", userAgent);
                String name = android.webkit.URLUtil.guessFileName(
                        url, contentDisposition, mimeType);
                request.setMimeType(mimeType);
                request.setNotificationVisibility(
                        android.app.DownloadManager.Request
                                .VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                request.setDestinationInExternalPublicDir(
                        android.os.Environment.DIRECTORY_DOWNLOADS, name);
                android.app.DownloadManager manager =
                        (android.app.DownloadManager)
                                getSystemService(DOWNLOAD_SERVICE);
                manager.enqueue(request);
                Toast.makeText(this, "Downloading " + name,
                        Toast.LENGTH_SHORT).show();
            } catch (RuntimeException error) {
                Toast.makeText(this, "Download failed: " + error.getMessage(),
                        Toast.LENGTH_LONG).show();
            }
        });
        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView webView,
                    ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_CHOOSER);
                    return true;
                } catch (RuntimeException error) {
                    fileCallback = null;
                    return false;
                }
            }
        });
        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView webView,
                                                    WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (sameOrigin(server.baseUrl, uri)) {
                    if ("/auth/login".equals(uri.getPath())
                            || (uri.getPath() != null && uri.getPath().startsWith("/auth/login/"))) {
                        showLogin(server);
                        return true;
                    }
                    return false;
                }
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
                return true;
            }
        });
    }

    private static boolean sameOrigin(String baseUrl, Uri uri) {
        try {
            URI base = URI.create(baseUrl);
            int expectedPort = base.getPort() == -1 ? 443 : base.getPort();
            int actualPort = uri.getPort() == -1 ? 443 : uri.getPort();
            return "https".equalsIgnoreCase(uri.getScheme())
                    && base.getHost().equalsIgnoreCase(uri.getHost())
                    && expectedPort == actualPort;
        } catch (RuntimeException error) {
            return false;
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (webScreen && webView != null && webView.canGoBack()) {
            webView.goBack();
        } else if (currentServer != null) {
            showServers();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(
                    resultCode, data));
            fileCallback = null;
        }
    }

    @Override
    protected void onDestroy() {
        destroyChatTabs();
        network.shutdownNow();
        super.onDestroy();
    }

    private void savePendingOAuth(String serverId, String flowId, String verifier) {
        getSharedPreferences(OAUTH_PREFS, MODE_PRIVATE).edit()
                .putString("server", serverId)
                .putString("flow", flowId)
                .putString("verifier", crypto.encrypt(verifier))
                .apply();
    }

    private PendingOAuth loadPendingOAuth() {
        try {
            String server = getSharedPreferences(OAUTH_PREFS, MODE_PRIVATE)
                    .getString("server", "");
            String flow = getSharedPreferences(OAUTH_PREFS, MODE_PRIVATE)
                    .getString("flow", "");
            String encrypted = getSharedPreferences(OAUTH_PREFS, MODE_PRIVATE)
                    .getString("verifier", "");
            if (server.isBlank() || flow.isBlank() || encrypted.isBlank()) {
                return null;
            }
            return new PendingOAuth(server, flow, crypto.decrypt(encrypted));
        } catch (RuntimeException error) {
            return null;
        }
    }

    private void clearPendingOAuth() {
        getSharedPreferences(OAUTH_PREFS, MODE_PRIVATE).edit().clear().apply();
    }

    private void showBusy(String message) {
        LinearLayout page = column();
        page.setGravity(Gravity.CENTER);
        ProgressBar progress = new ProgressBar(this);
        page.addView(progress);
        page.addView(text(message));
        setScreen(page);
    }

    private void showError(String title, Exception error) {
        String message = error.getMessage() == null
                ? error.getClass().getSimpleName() : error.getMessage();
        Toast.makeText(this, title + ": " + message, Toast.LENGTH_LONG).show();
        ServerProfile retryServer = currentServer;
        if (retryServer == null) {
            showServers();
        }
        AlertDialog.Builder dialog = new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setNegativeButton("Servers", (ignored, which) -> showServers());
        if (retryServer != null) {
            dialog.setPositiveButton(
                    "Try again", (ignored, which) -> showLogin(retryServer));
        }
        dialog.show();
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(getColor(R.color.pawflow_surface));
        return layout;
    }

    private TextView title(String value) {
        TextView view = text(value);
        view.setTextSize(24);
        view.setTextColor(getColor(R.color.pawflow_navy));
        view.setPadding(0, dp(8), 0, dp(12));
        return view;
    }

    private TextView text(String value) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(16);
        view.setTextColor(getColor(R.color.pawflow_text));
        return view;
    }

    private Button button(String value) {
        Button view = new Button(this);
        view.setText(value);
        view.setAllCaps(false);
        return view;
    }

    private EditText input(String hint, int type) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setInputType(type);
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String encode(String value) {
        try {
            return URLEncoder.encode(value, StandardCharsets.UTF_8.name());
        } catch (UnsupportedEncodingException error) {
            throw new IllegalStateException("UTF-8 is unavailable", error);
        }
    }

    private record PendingOAuth(String serverId, String flowId, String verifier) {}

    private record Pkce(String verifier, String challenge) {
        static Pkce create() {
            byte[] random = new byte[64];
            new SecureRandom().nextBytes(random);
            String verifier = Base64.getUrlEncoder().withoutPadding().encodeToString(random);
            try {
                byte[] digest = MessageDigest.getInstance("SHA-256")
                        .digest(verifier.getBytes(StandardCharsets.US_ASCII));
                String challenge = Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
                return new Pkce(verifier, challenge);
            } catch (Exception error) {
                throw new IllegalStateException("PKCE is unavailable", error);
            }
        }
    }
}

