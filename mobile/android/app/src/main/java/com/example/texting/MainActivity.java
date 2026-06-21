package com.example.texting;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.webkit.HttpAuthHandler;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class MainActivity extends Activity {
    private static final int SHELL_BACKGROUND = Color.rgb(12, 17, 23);
    private static final int PANEL_BACKGROUND = Color.rgb(21, 28, 36);
    private static final int TEXT_PRIMARY = Color.rgb(242, 246, 251);
    private static final int TEXT_MUTED = Color.rgb(169, 180, 194);
    private static final int ACCENT = Color.rgb(88, 166, 255);
    private WebView webView;
    private FrameLayout rootLayout;
    private SwipeRefreshLayout swipeRefreshLayout;
    private LinearLayout connectionView;
    private TextView connectionTitle;
    private TextView connectionMessage;
    private TextView connectionServer;
    private LinearLayout connectionActions;
    private volatile boolean nativePullRefreshEnabled = false;
    private static final int NOTIFICATION_PERMISSION_REQUEST = 40;
    private static final String PREFS_NAME = "switchboard";
    private static final String PREF_SERVER_URL = "server_url";
    private static final String APP_ASSET_VERSION = "9b438619";
    private boolean serverUrlDialogOpen = false;
    private boolean mainFrameLoadFailed = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();
        MobileNotificationClient.ensureNotificationChannel(this);
        requestNotificationPermissionIfNeeded();
        NotificationPollService.schedule(this);

        rootLayout = new FrameLayout(this);
        rootLayout.setBackgroundColor(SHELL_BACKGROUND);

        swipeRefreshLayout = new SwipeRefreshLayout(this);
        swipeRefreshLayout.setBackgroundColor(SHELL_BACKGROUND);
        swipeRefreshLayout.setColorSchemeColors(0xFF127F73, 0xFF2563EB);
        swipeRefreshLayout.setEnabled(false);

        webView = new WebView(this);
        webView.setBackgroundColor(SHELL_BACKGROUND);
        swipeRefreshLayout.addView(webView, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        rootLayout.addView(swipeRefreshLayout, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        connectionView = createConnectionView();
        rootLayout.addView(connectionView, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        applySystemBarInsets();
        swipeRefreshLayout.setOnChildScrollUpCallback(
            (parent, child) -> !nativePullRefreshEnabled || (webView != null && webView.getScrollY() > 0)
        );
        swipeRefreshLayout.setOnRefreshListener(() -> {
            if (webView == null || !nativePullRefreshEnabled) {
                swipeRefreshLayout.setRefreshing(false);
                return;
            }
            webView.evaluateJavascript(
                "(function(){"
                    + "return !!(window.textingRefreshFromNativePull"
                    + "&& window.textingRefreshFromNativePull());"
                    + "})()",
                value -> {
                    if (!"true".equals(value)) {
                        webView.reload();
                        return;
                    }
                    swipeRefreshLayout.postDelayed(
                        () -> swipeRefreshLayout.setRefreshing(false),
                        900
                    );
                }
            );
        });
        setContentView(rootLayout);
        showConnectingState();

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMediaPlaybackRequiresUserGesture(false);
        webView.clearCache(true);
        webView.addJavascriptInterface(new AndroidBridge(), "SwitchboardAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                showAuthDialog(handler, host, realm);
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                mainFrameLoadFailed = false;
                showConnectingState();
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (swipeRefreshLayout != null) {
                    swipeRefreshLayout.setRefreshing(false);
                }
                if (!mainFrameLoadFailed) {
                    hideConnectionState();
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request == null || !request.isForMainFrame()) {
                    return;
                }
                String description = error == null || error.getDescription() == null
                    ? "The server could not be reached."
                    : error.getDescription().toString();
                handleMainFrameLoadFailure(request.getUrl(), description);
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
                if (request == null || !request.isForMainFrame() || errorResponse == null) {
                    return;
                }
                handleMainFrameLoadFailure(
                    request.getUrl(),
                    "HTTP " + errorResponse.getStatusCode() + " " + errorResponse.getReasonPhrase()
                );
            }
        });
        webView.loadUrl(urlForIntent(getIntent()));
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void configureSystemBars() {
        Window window = getWindow();
        window.setStatusBarColor(SHELL_BACKGROUND);
        window.setNavigationBarColor(SHELL_BACKGROUND);
        int flags = window.getDecorView().getSystemUiVisibility();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags &= ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            flags &= ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
        }
        window.getDecorView().setSystemUiVisibility(flags);
    }

    private GradientDrawable roundedBackground(int color, float radiusDp) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(color);
        background.setCornerRadius(dp(radiusDp));
        return background;
    }

    private GradientDrawable outlinedBackground(int color, int strokeColor, float radiusDp) {
        GradientDrawable background = roundedBackground(color, radiusDp);
        background.setStroke(dp(1), strokeColor);
        return background;
    }

    private TextView connectionText(String text, int color, float sp, int style) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(color);
        view.setTextSize(sp);
        view.setTypeface(Typeface.DEFAULT, style);
        view.setGravity(Gravity.CENTER);
        view.setIncludeFontPadding(true);
        return view;
    }

    private Button connectionButton(String text, boolean primary) {
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setMinHeight(dp(44));
        button.setText(text);
        button.setTextColor(primary ? Color.WHITE : TEXT_PRIMARY);
        button.setTextSize(14);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setBackground(primary
            ? roundedBackground(ACCENT, 8)
            : outlinedBackground(PANEL_BACKGROUND, Color.rgb(44, 55, 69), 8));
        return button;
    }

    private LinearLayout createConnectionView() {
        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setGravity(Gravity.CENTER);
        screen.setPadding(dp(28), dp(28), dp(28), dp(28));
        screen.setBackgroundColor(SHELL_BACKGROUND);

        connectionTitle = connectionText("", TEXT_PRIMARY, 21, Typeface.BOLD);
        screen.addView(connectionTitle, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        connectionMessage = connectionText("", TEXT_MUTED, 15, Typeface.NORMAL);
        LinearLayout.LayoutParams messageParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        messageParams.setMargins(0, dp(8), 0, 0);
        screen.addView(connectionMessage, messageParams);

        connectionServer = connectionText("", TEXT_MUTED, 13, Typeface.NORMAL);
        LinearLayout.LayoutParams serverParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        serverParams.setMargins(0, dp(14), 0, 0);
        screen.addView(connectionServer, serverParams);

        connectionActions = new LinearLayout(this);
        connectionActions.setOrientation(LinearLayout.HORIZONTAL);
        connectionActions.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams actionsParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        actionsParams.setMargins(0, dp(22), 0, 0);

        Button retryButton = connectionButton("Retry", true);
        retryButton.setOnClickListener(view -> loadCurrentServerUrl());
        LinearLayout.LayoutParams retryParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        retryParams.setMargins(0, 0, dp(8), 0);
        connectionActions.addView(retryButton, retryParams);

        Button serverButton = connectionButton("Server URL", false);
        serverButton.setOnClickListener(view -> showServerUrlDialog());
        LinearLayout.LayoutParams serverButtonParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        serverButtonParams.setMargins(dp(8), 0, 0, 0);
        connectionActions.addView(serverButton, serverButtonParams);

        screen.addView(connectionActions, actionsParams);
        return screen;
    }

    private void applySystemBarInsets() {
        swipeRefreshLayout.setOnApplyWindowInsetsListener((View view, WindowInsets insets) -> {
            int left;
            int top;
            int right;
            int bottom;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                left = bars.left;
                top = bars.top;
                right = bars.right;
                bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft();
                top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(left, top, right, bottom);
            return insets.consumeSystemWindowInsets();
        });
        swipeRefreshLayout.requestApplyInsets();
    }

    private void setNativePullRefreshEnabled(boolean enabled) {
        nativePullRefreshEnabled = enabled;
        if (swipeRefreshLayout == null) {
            return;
        }
        swipeRefreshLayout.setEnabled(enabled);
        if (!enabled && swipeRefreshLayout.isRefreshing()) {
            swipeRefreshLayout.setRefreshing(false);
        }
    }

    private void showConnectingState() {
        if (connectionView == null) {
            return;
        }
        connectionTitle.setText("Connecting to Switchboard...");
        connectionMessage.setText("Opening the server.");
        connectionServer.setText("Server: " + serverUrl());
        connectionActions.setVisibility(View.GONE);
        connectionView.setVisibility(View.VISIBLE);
        if (webView != null) {
            webView.setVisibility(View.INVISIBLE);
        }
    }

    private void hideConnectionState() {
        if (connectionView != null) {
            connectionView.setVisibility(View.GONE);
        }
        if (webView != null) {
            webView.setVisibility(View.VISIBLE);
        }
    }

    private void showConnectionFailure(String details) {
        if (connectionView == null) {
            return;
        }
        connectionTitle.setText("Couldn't connect to Switchboard server.");
        connectionMessage.setText("Check your connection or update the server URL.");
        connectionServer.setText("Server: " + serverUrl());
        connectionActions.setVisibility(View.VISIBLE);
        connectionView.setVisibility(View.VISIBLE);
        if (webView != null) {
            webView.setVisibility(View.INVISIBLE);
        }
    }

    private void loadCurrentServerUrl() {
        if (webView == null) {
            return;
        }
        mainFrameLoadFailed = false;
        showConnectingState();
        webView.loadUrl(urlForIntent(getIntent()));
    }

    private class AndroidBridge {
        @JavascriptInterface
        public void setPullRefreshEnabled(boolean enabled) {
            runOnUiThread(() -> setNativePullRefreshEnabled(enabled));
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        openConversationFromIntent(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        NotificationPollService.schedule(this);
        NotificationPollService.pollNow(this);
        refreshWebPageIfStale();
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < 33) {
            return;
        }
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATION_PERMISSION_REQUEST);
    }

    private SharedPreferences appPreferences() {
        return getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
    }

    private String serverUrl() {
        String saved = appPreferences().getString(PREF_SERVER_URL, "");
        if (saved != null && !saved.trim().isEmpty()) {
            return saved.trim();
        }
        return getString(R.string.app_url).trim();
    }

    private String normalizedServerUrl(String value) {
        String trimmed = value == null ? "" : value.trim();
        if (trimmed.isEmpty()) {
            return "";
        }
        if (!trimmed.contains("://")) {
            trimmed = "https://" + trimmed;
        }
        Uri uri = Uri.parse(trimmed);
        String scheme = uri.getScheme();
        String host = uri.getHost();
        if (scheme == null || host == null || host.isEmpty()) {
            return "";
        }
        String lowerScheme = scheme.toLowerCase();
        if (!"https".equals(lowerScheme) && !"http".equals(lowerScheme)) {
            return "";
        }
        return uri.toString();
    }

    private String urlForIntent(Intent intent) {
        int conversationId = conversationIdFromIntent(intent);
        Uri base = Uri.parse(serverUrl());
        Uri.Builder builder = base.buildUpon().clearQuery().appendQueryParameter("app_v", APP_ASSET_VERSION);
        if (conversationId > 0) {
            builder.appendQueryParameter("conversation", String.valueOf(conversationId));
        }
        return builder.build().toString();
    }

    private int conversationIdFromIntent(Intent intent) {
        if (intent == null) {
            return 0;
        }
        int extra = intent.getIntExtra(NotificationPollService.EXTRA_CONVERSATION_ID, 0);
        if (extra > 0) {
            return extra;
        }
        Uri data = intent.getData();
        if (data == null) {
            return 0;
        }
        try {
            return Integer.parseInt(data.getQueryParameter("conversation"));
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private void handleMainFrameLoadFailure(Uri failingUri, String details) {
        mainFrameLoadFailed = true;
        if (swipeRefreshLayout != null) {
            swipeRefreshLayout.setRefreshing(false);
        }
        showConnectionFailure(details);
    }

    private void showServerUrlDialog() {
        if (serverUrlDialogOpen || isFinishing()) {
            return;
        }
        serverUrlDialogOpen = true;

        LinearLayout layout = new LinearLayout(this);
        int padding = (int) (20 * getResources().getDisplayMetrics().density);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(padding, padding / 2, padding, 0);

        EditText url = new EditText(this);
        url.setHint("https://switchboard.example.com");
        url.setSingleLine(true);
        url.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        url.setText(serverUrl());
        url.setSelection(url.getText().length());
        layout.addView(url, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("Server URL")
            .setMessage("Enter the Switchboard server URL.")
            .setView(layout)
            .setPositiveButton("Save", null)
            .setNegativeButton("Cancel", (dialogInterface, which) -> serverUrlDialogOpen = false)
            .setOnCancelListener(dialogInterface -> serverUrlDialogOpen = false)
            .create();
        dialog.setOnShowListener(dialogInterface ->
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
                String normalized = normalizedServerUrl(url.getText().toString());
                if (normalized.isEmpty()) {
                    url.setError("Enter a valid http:// or https:// URL.");
                    return;
                }
                appPreferences().edit().putString(PREF_SERVER_URL, normalized).apply();
                serverUrlDialogOpen = false;
                dialog.dismiss();
                loadCurrentServerUrl();
            })
        );
        dialog.show();
    }

    private void openConversationFromIntent(Intent intent) {
        int conversationId = conversationIdFromIntent(intent);
        if (conversationId <= 0 || webView == null) {
            return;
        }
        String fallbackUrl = urlForIntent(intent);
        webView.evaluateJavascript(
            "(function(){"
                + "return !!(window.textingOpenConversationFromNative"
                + "&& window.textingOpenConversationFromNative(" + conversationId + "));"
                + "})()",
            value -> {
                if (!"true".equals(value)) {
                    webView.loadUrl(fallbackUrl);
                }
            }
        );
    }

    private void refreshWebPageIfStale() {
        if (webView == null) {
            return;
        }
        webView.evaluateJavascript(
            "(function(){"
                + "return !!(window.textingRefreshIfStaleFromNative"
                + "&& window.textingRefreshIfStaleFromNative());"
                + "})()",
            null
        );
    }

    private void showAuthDialog(HttpAuthHandler handler, String host, String realm) {
        LinearLayout layout = new LinearLayout(this);
        int padding = (int) (20 * getResources().getDisplayMetrics().density);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(padding, padding / 2, padding, 0);

        EditText username = new EditText(this);
        username.setHint("Username");
        username.setSingleLine(true);
        username.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        layout.addView(username, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        EditText password = new EditText(this);
        password.setHint("Password");
        password.setSingleLine(true);
        password.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        layout.addView(password, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        new AlertDialog.Builder(this)
            .setTitle("Sign in")
            .setMessage(host + (realm == null || realm.isEmpty() ? "" : "\n" + realm))
            .setView(layout)
            .setPositiveButton("Sign in", (dialog, which) -> {
                String user = username.getText().toString();
                String pass = password.getText().toString();
                AuthStore.save(this, host, user, pass);
                handler.proceed(user, pass);
                NotificationPollService.pollNow(this);
            })
            .setNegativeButton("Cancel", (dialog, which) -> handler.cancel())
            .setOnCancelListener(dialog -> handler.cancel())
            .show();
    }

    @Override
    public void onBackPressed() {
        if (webView == null) {
            super.onBackPressed();
            return;
        }
        webView.evaluateJavascript(
            "(function(){"
	                + "if(document.body && (document.body.classList.contains('mobile-thread-open')"
	                + "|| document.body.classList.contains('conversation-selecting')"
	                + "|| (document.querySelector('#conversationSearch') && document.querySelector('#conversationSearch').value)"
	                + "|| (document.querySelector('#statsModal') && !document.querySelector('#statsModal').classList.contains('hidden'))"
                + "|| document.body.classList.contains('details-overlay-open'))){"
                + "if(window.textingCloseThreadForNativeBack){window.textingCloseThreadForNativeBack();}"
                + "else if(history.length > 1){history.back();}"
                + "else{document.body.classList.remove('mobile-thread-open');}"
                + "return true;"
                + "}"
                + "return false;"
                + "})()",
            value -> {
                if ("true".equals(value)) {
                    return;
                }
                if (webView.canGoBack()) {
                    webView.goBack();
                    return;
                }
                finish();
            }
        );
    }
}
