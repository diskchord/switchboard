package com.example.texting;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.webkit.HttpAuthHandler;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class MainActivity extends Activity {
    private WebView webView;
    private SwipeRefreshLayout swipeRefreshLayout;
    private volatile boolean nativePullRefreshEnabled = false;
    private static final int NOTIFICATION_PERMISSION_REQUEST = 40;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        MobileNotificationClient.ensureNotificationChannel(this);
        requestNotificationPermissionIfNeeded();
        NotificationPollService.schedule(this);

        swipeRefreshLayout = new SwipeRefreshLayout(this);
        swipeRefreshLayout.setColorSchemeColors(0xFF127F73, 0xFF2563EB);
        swipeRefreshLayout.setEnabled(false);

        webView = new WebView(this);
        swipeRefreshLayout.addView(webView, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
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
        setContentView(swipeRefreshLayout);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        webView.addJavascriptInterface(new AndroidBridge(), "SwitchboardAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                showAuthDialog(handler, host, realm);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (swipeRefreshLayout != null) {
                    swipeRefreshLayout.setRefreshing(false);
                }
            }
        });
        webView.loadUrl(urlForIntent(getIntent()));
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

    private String urlForIntent(Intent intent) {
        int conversationId = conversationIdFromIntent(intent);
        if (conversationId <= 0) {
            return getString(R.string.app_url);
        }
        Uri base = Uri.parse(getString(R.string.app_url));
        return base.buildUpon()
            .clearQuery()
            .appendQueryParameter("conversation", String.valueOf(conversationId))
            .build()
            .toString();
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
