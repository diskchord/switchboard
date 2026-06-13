package com.example.texting;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class MobileNotificationClient {
    private static final String CHANNEL_ID = "incoming_texts";
    private static final String PREFS = "texting_notifications";
    private static final String KEY_INITIALIZED = "initialized";
    private static final String KEY_LAST_NOTIFICATION = "last_notification_key";
    private static final String KEY_POLL_INTERVAL_MILLIS = "poll_interval_millis";
    private static final long DEFAULT_POLL_INTERVAL_MILLIS = 15 * 60 * 1000L;
    private static final long MIN_POLL_INTERVAL_MILLIS = 15 * 60 * 1000L;

    private MobileNotificationClient() {
    }

    static void ensureNotificationChannel(Context context) {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null || manager.getNotificationChannel(CHANNEL_ID) != null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "Incoming texts",
            NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("Notifications for incoming text messages");
        manager.createNotificationChannel(channel);
    }

    static void poll(Context context) {
        Context appContext = context.getApplicationContext();
        ensureNotificationChannel(appContext);
        SharedPreferences prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        boolean initialized = prefs.getBoolean(KEY_INITIALIZED, false);
        String since = prefs.getString(KEY_LAST_NOTIFICATION, "");
        JSONObject payload = fetchNotifications(appContext, initialized ? since : "");
        if (payload == null) {
            return;
        }
        boolean enabled = payload.optBoolean("enabled", false);
        long previousInterval = getPollIntervalMillis(appContext);
        long nextInterval = Math.max(payload.optInt("poll_interval_minutes", 15), 15) * 60L * 1000L;
        String latestKey = payload.optString("latest_key", "");
        SharedPreferences.Editor editor = prefs.edit().putLong(KEY_POLL_INTERVAL_MILLIS, nextInterval);
        if (!initialized) {
            editor
                .putBoolean(KEY_INITIALIZED, true)
                .putString(KEY_LAST_NOTIFICATION, latestKey)
                .apply();
            if (nextInterval != previousInterval) {
                NotificationPollService.schedule(appContext);
            }
            return;
        }

        if (!enabled) {
            if (!latestKey.isEmpty()) {
                editor.putString(KEY_LAST_NOTIFICATION, latestKey);
            }
            editor.apply();
            if (nextInterval != previousInterval) {
                NotificationPollService.schedule(appContext);
            }
            return;
        }

        JSONArray notifications = payload.optJSONArray("notifications");
        if (notifications != null) {
            for (int index = 0; index < notifications.length(); index++) {
                JSONObject item = notifications.optJSONObject(index);
                if (item != null) {
                    showNotification(appContext, item);
                }
            }
        }
        if (!latestKey.isEmpty()) {
            editor.putString(KEY_LAST_NOTIFICATION, latestKey);
        }
        editor.apply();
        if (nextInterval != previousInterval) {
            NotificationPollService.schedule(appContext);
        }
    }

    static long getPollIntervalMillis(Context context) {
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return Math.max(
            prefs.getLong(KEY_POLL_INTERVAL_MILLIS, DEFAULT_POLL_INTERVAL_MILLIS),
            MIN_POLL_INTERVAL_MILLIS
        );
    }

    private static JSONObject fetchNotifications(Context context, String since) {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(notificationsUrl(context, since));
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(12000);
            connection.setReadTimeout(20000);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "SwitchboardAndroid/0.1");
            String authorization = AuthStore.authorizationForAppUrl(context);
            if (!authorization.isEmpty()) {
                connection.setRequestProperty("Authorization", authorization);
            }
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                return null;
            }
            return new JSONObject(readAll(connection.getInputStream()));
        } catch (Exception ignored) {
            return null;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static String notificationsUrl(Context context, String since) {
        Uri base = Uri.parse(context.getString(R.string.app_url));
        Uri.Builder builder = base.buildUpon()
            .encodedPath("/api/mobile/notifications")
            .clearQuery()
            .appendQueryParameter("limit", "20");
        if (since != null && !since.isEmpty()) {
            builder.appendQueryParameter("since", since);
        }
        return builder.build().toString();
    }

    private static String readAll(InputStream stream) throws Exception {
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line);
            }
        }
        return builder.toString();
    }

    private static void showNotification(Context context, JSONObject item) {
        if (Build.VERSION.SDK_INT >= 33
            && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        int conversationId = item.optInt("conversation_id", 0);
        int messageId = item.optInt("message_id", conversationId);
        String sender = item.optString("from_display", "");
        if (sender.isEmpty()) {
            sender = item.optString("title", context.getString(R.string.app_name));
        }
        String body = item.optString("text", "New text message");

        Intent intent = new Intent(context, MainActivity.class);
        intent.setAction(NotificationPollService.ACTION_OPEN_CONVERSATION);
        intent.putExtra(NotificationPollService.EXTRA_CONVERSATION_ID, conversationId);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            context,
            messageId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(context, CHANNEL_ID)
            : new Notification.Builder(context);
        builder
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(sender)
            .setContentText(body)
            .setStyle(new Notification.BigTextStyle().bigText(body))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setShowWhen(true)
            .setGroup("texts");
        if (Build.VERSION.SDK_INT < 26) {
            builder.setPriority(Notification.PRIORITY_HIGH);
        }
        manager.notify(messageId, builder.build());
    }
}
