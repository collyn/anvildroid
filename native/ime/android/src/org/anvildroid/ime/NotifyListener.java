package org.anvildroid.ime;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.Icon;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Mirrors Android notifications to the host desktop, like ARC on ChromeOS.
 *
 * Only the AnvilDroid worker (root, entering this container's network
 * namespace) may connect to the abstract socket. It receives a snapshot and
 * then posted/removed events, and may click, invoke an action (with inline
 * reply text) or dismiss a notification by key. Per-app policy is applied by
 * the host; this service only mirrors.
 */
public final class NotifyListener extends NotificationListenerService {
    static final String SOCKET = "org.anvildroid.notify.v1";
    static final int LIMIT = 262144;
    static final int ICON_SIZE = 96;
    private static final String[] SKIPPED = {"android", "com.android.systemui", "org.anvildroid.ime"};

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<String, String> appIcons = new HashMap<>();
    private final Map<String, String> labels = new HashMap<>();
    private volatile boolean running;
    private volatile Peer peer;
    private LocalServerSocket server;

    @Override public void onListenerConnected() {
        super.onListenerConnected();
        if (running) return;
        running = true;
        new Thread(this::accept, "anvildroid-notify-accept").start();
    }

    // API 24+ callback (compiled against API 23, so no @Override); the
    // superclass implementation is empty. onDestroy covers older releases.
    public void onListenerDisconnected() { shutdown(); }

    @Override public void onDestroy() {
        shutdown();
        super.onDestroy();
    }

    private void shutdown() {
        running = false;
        Peer current = peer;
        if (current != null) current.close();
        try { if (server != null) server.close(); } catch (IOException ignored) {}
        server = null;
    }

    private void accept() {
        try {
            server = new LocalServerSocket(SOCKET);
            while (running) {
                LocalSocket socket = server.accept();
                // Only the host worker (root) may read notification content.
                if (socket.getPeerCredentials().getUid() != 0) { socket.close(); continue; }
                socket.setSoTimeout(0);
                Peer next = new Peer(socket);
                main.post(() -> {
                    if (!running) { next.close(); return; }
                    if (peer != null) peer.close();
                    peer = next;
                    next.start();
                    sendSnapshot(next);
                });
            }
        } catch (IOException e) {
            if (running) android.util.Log.e("AnvilDroidNotify", "Notification socket unavailable");
        }
    }

    @Override public void onNotificationPosted(StatusBarNotification sbn) {
        Peer current = peer;
        if (current == null) return;
        JSONObject item = describe(sbn);
        try {
            if (item != null) current.send(new JSONObject().put("v", 1).put("type", "posted").put("item", item));
            else current.send(new JSONObject().put("v", 1).put("type", "removed").put("key", sbn.getKey()));
        } catch (JSONException ignored) {}
    }

    @Override public void onNotificationRemoved(StatusBarNotification sbn) {
        Peer current = peer;
        if (current == null) return;
        try {
            current.send(new JSONObject().put("v", 1).put("type", "removed").put("key", sbn.getKey()));
        } catch (JSONException ignored) {}
    }

    private void sendSnapshot(Peer target) {
        JSONArray items = new JSONArray();
        try {
            StatusBarNotification[] active = getActiveNotifications();
            if (active != null)
                for (StatusBarNotification sbn : active) {
                    JSONObject item = describe(sbn);
                    if (item != null) items.put(item);
                }
            target.send(new JSONObject().put("v", 1).put("type", "snapshot").put("items", items));
        } catch (JSONException | RuntimeException ignored) {}
    }

    /** Null when the notification should not reach the desktop at all. */
    private JSONObject describe(StatusBarNotification sbn) {
        Notification n = sbn.getNotification();
        String pkg = sbn.getPackageName();
        for (String skipped : SKIPPED) if (skipped.equals(pkg)) return null;
        int flags = n.flags;
        // ChromeOS shows a group's children, not the empty summary, and keeps
        // ongoing/foreground-service status out of the message center.
        if ((flags & Notification.FLAG_GROUP_SUMMARY) != 0) return null;
        if ((flags & (Notification.FLAG_ONGOING_EVENT | Notification.FLAG_FOREGROUND_SERVICE)) != 0) {
            Bundle extras = n.extras;
            if (extras == null || extras.getInt(Notification.EXTRA_PROGRESS_MAX, 0) <= 0) return null;
        }
        if (n.priority == Notification.PRIORITY_MIN) return null;
        Bundle extras = n.extras != null ? n.extras : new Bundle();
        CharSequence title = extras.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
        CharSequence text = big != null ? big : extras.getCharSequence(Notification.EXTRA_TEXT);
        if (title == null && text == null) return null;
        try {
            JSONObject item = new JSONObject()
                .put("key", sbn.getKey())
                .put("package", pkg)
                .put("app", label(pkg))
                .put("title", clip(title, 512))
                .put("text", clip(text, 2048))
                .put("sub_text", clip(extras.getCharSequence(Notification.EXTRA_SUB_TEXT), 256))
                .put("when", n.when)
                .put("category", n.category == null ? "" : n.category)
                .put("alert", (flags & Notification.FLAG_ONLY_ALERT_ONCE) == 0)
                .put("auto_cancel", (flags & Notification.FLAG_AUTO_CANCEL) != 0)
                .put("clickable", n.contentIntent != null)
                .put("priority", n.priority);
            int max = extras.getInt(Notification.EXTRA_PROGRESS_MAX, 0);
            if (max > 0 || extras.getBoolean(Notification.EXTRA_PROGRESS_INDETERMINATE, false))
                item.put("progress", new JSONObject().put("max", max)
                    .put("value", extras.getInt(Notification.EXTRA_PROGRESS, 0))
                    .put("indeterminate", extras.getBoolean(Notification.EXTRA_PROGRESS_INDETERMINATE, false)));
            JSONArray actions = new JSONArray();
            if (n.actions != null)
                for (int i = 0; i < n.actions.length && i < 3; i++) {
                    Notification.Action action = n.actions[i];
                    if (action == null || action.title == null || action.actionIntent == null) continue;
                    actions.put(new JSONObject().put("index", i).put("title", clip(action.title, 64))
                        .put("reply", replyInput(action) != null));
                }
            item.put("actions", actions);
            String icon = largeIcon(n);
            item.put("icon", icon != null ? icon : appIcon(pkg));
            return item;
        } catch (JSONException e) {
            return null;
        }
    }

    private static String clip(CharSequence value, int limit) {
        if (value == null) return "";
        String text = value.toString();
        if (text.length() <= limit) return text;
        int end = limit;
        if (Character.isHighSurrogate(text.charAt(end - 1))) end--;
        return text.substring(0, end) + "…";
    }

    private String label(String pkg) {
        String cached = labels.get(pkg);
        if (cached != null) return cached;
        String value = pkg;
        try {
            PackageManager pm = getPackageManager();
            ApplicationInfo info = pm.getApplicationInfo(pkg, 0);
            value = clip(pm.getApplicationLabel(info), 128);
        } catch (PackageManager.NameNotFoundException ignored) {}
        labels.put(pkg, value);
        return value;
    }

    private String largeIcon(Notification n) {
        try {
            Icon icon = n.getLargeIcon();
            return icon == null ? null : png(icon.loadDrawable(this));
        } catch (RuntimeException e) {
            return null;
        }
    }

    private String appIcon(String pkg) {
        if (appIcons.containsKey(pkg)) return appIcons.get(pkg);
        String value = "";
        try { value = png(getPackageManager().getApplicationIcon(pkg)); }
        catch (PackageManager.NameNotFoundException | RuntimeException ignored) {}
        if (value == null) value = "";
        appIcons.put(pkg, value);
        return value;
    }

    private static String png(Drawable drawable) {
        if (drawable == null) return null;
        Bitmap bitmap;
        if (drawable instanceof BitmapDrawable && ((BitmapDrawable) drawable).getBitmap() != null) {
            bitmap = Bitmap.createScaledBitmap(((BitmapDrawable) drawable).getBitmap(), ICON_SIZE, ICON_SIZE, true);
        } else {
            bitmap = Bitmap.createBitmap(ICON_SIZE, ICON_SIZE, Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(bitmap);
            drawable.setBounds(0, 0, ICON_SIZE, ICON_SIZE);
            drawable.draw(canvas);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out);
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
    }

    private static RemoteInput replyInput(Notification.Action action) {
        RemoteInput[] inputs = action.getRemoteInputs();
        if (inputs == null) return null;
        for (RemoteInput input : inputs)
            if (input != null && input.getAllowFreeFormInput()) return input;
        return null;
    }

    private StatusBarNotification find(String key) {
        try {
            StatusBarNotification[] found = getActiveNotifications(new String[]{key});
            return found != null && found.length == 1 && key.equals(found[0].getKey()) ? found[0] : null;
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** Host requests run on the main thread. */
    private void receive(JSONObject msg) {
        String op = msg.optString("op"), key = msg.optString("key");
        if ("ping".equals(op)) {
            Peer current = peer;
            try { if (current != null) current.send(new JSONObject().put("v", 1).put("type", "pong")); }
            catch (JSONException ignored) {}
            return;
        }
        if (key.isEmpty() || key.length() > 1024) return;
        StatusBarNotification sbn = find(key);
        if (sbn == null) return;
        Notification n = sbn.getNotification();
        try {
            switch (op) {
                case "click":
                    if (n.contentIntent != null) n.contentIntent.send();
                    if ((n.flags & Notification.FLAG_AUTO_CANCEL) != 0) cancelNotification(key);
                    break;
                case "action": {
                    int index = msg.optInt("index", -1);
                    if (n.actions == null || index < 0 || index >= n.actions.length || n.actions[index] == null) return;
                    Notification.Action action = n.actions[index];
                    RemoteInput input = replyInput(action);
                    String reply = msg.optString("reply", null);
                    if (input != null && reply != null && Wire.validText(reply)) {
                        Intent fill = new Intent();
                        Bundle results = new Bundle();
                        results.putCharSequence(input.getResultKey(), reply);
                        RemoteInput.addResultsToIntent(new RemoteInput[]{input}, fill, results);
                        action.actionIntent.send(this, 0, fill);
                    } else {
                        action.actionIntent.send();
                    }
                    break;
                }
                case "dismiss":
                    if ((n.flags & (Notification.FLAG_ONGOING_EVENT | Notification.FLAG_NO_CLEAR)) == 0)
                        cancelNotification(key);
                    break;
                default:
                    break;
            }
        } catch (PendingIntent.CanceledException ignored) {}
    }

    private final class Peer {
        final LocalSocket socket;
        final ArrayBlockingQueue<JSONObject> output = new ArrayBlockingQueue<>(256);
        volatile boolean closed;
        Peer(LocalSocket socket) { this.socket = socket; }
        void send(JSONObject msg) { if (!closed && !output.offer(msg)) close(); }
        void close() {
            closed = true;
            // close() alone does not reach the peer for accepted LocalSockets;
            // shutdown makes the host see EOF and reconnect.
            try { socket.shutdownInput(); } catch (IOException ignored) {}
            try { socket.shutdownOutput(); } catch (IOException ignored) {}
            try { socket.close(); } catch (IOException ignored) {}
        }
        void start() {
            new Thread(() -> {
                try {
                    DataOutputStream out = new DataOutputStream(socket.getOutputStream());
                    while (!closed) {
                        JSONObject msg = output.poll(1, TimeUnit.SECONDS);
                        if (msg != null) Wire.write(out, msg, LIMIT);
                    }
                } catch (Exception ignored) { close(); }
            }, "anvildroid-notify-output").start();
            new Thread(() -> {
                try {
                    DataInputStream in = new DataInputStream(socket.getInputStream());
                    while (!closed) {
                        JSONObject msg = Wire.read(in);
                        main.post(() -> receive(msg));
                    }
                } catch (Exception ignored) {
                } finally {
                    close();
                    main.post(() -> { if (peer == this) peer = null; });
                }
            }, "anvildroid-notify-input").start();
        }
    }
}
