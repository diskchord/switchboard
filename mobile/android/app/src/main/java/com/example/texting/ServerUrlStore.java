package com.example.texting;

import android.content.Context;
import android.content.SharedPreferences;

final class ServerUrlStore {
    private static final String PREFS_NAME = "switchboard";
    private static final String PREF_SERVER_URL = "server_url";

    private ServerUrlStore() {
    }

    static String get(Context context) {
        Context appContext = context.getApplicationContext();
        SharedPreferences prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String saved = prefs.getString(PREF_SERVER_URL, "");
        if (saved != null && !saved.trim().isEmpty()) {
            return saved.trim();
        }
        return appContext.getString(R.string.app_url).trim();
    }

    static void set(Context context, String url) {
        context.getApplicationContext()
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(PREF_SERVER_URL, url == null ? "" : url.trim())
            .apply();
    }
}
