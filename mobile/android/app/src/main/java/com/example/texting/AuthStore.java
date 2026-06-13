package com.example.texting;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.util.Base64;

import java.nio.charset.StandardCharsets;

final class AuthStore {
    private static final String PREFS = "texting_auth";

    private AuthStore() {
    }

    static void save(Context context, String host, String username, String password) {
        if (host == null || host.isEmpty()) {
            return;
        }
        SharedPreferences.Editor editor = context.getApplicationContext()
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit();
        editor.putString(key(host, "username"), username == null ? "" : username);
        editor.putString(key(host, "password"), password == null ? "" : password);
        editor.apply();
    }

    static String authorizationForAppUrl(Context context) {
        Uri uri = Uri.parse(context.getString(R.string.app_url));
        String host = uri.getHost();
        if (host == null || host.isEmpty()) {
            return "";
        }
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String username = prefs.getString(key(host, "username"), "");
        String password = prefs.getString(key(host, "password"), "");
        if (username == null || username.isEmpty()) {
            return "";
        }
        String raw = username + ":" + (password == null ? "" : password);
        return "Basic " + Base64.encodeToString(raw.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
    }

    private static String key(String host, String name) {
        return host.toLowerCase() + "." + name;
    }
}
