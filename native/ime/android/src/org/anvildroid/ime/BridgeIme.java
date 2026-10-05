package org.anvildroid.ime;

import android.inputmethodservice.InputMethodService;
import android.net.*;
import android.os.*;
import android.text.InputType;
import android.view.inputmethod.*;
import java.io.*;
import java.util.concurrent.ArrayBlockingQueue;
import org.json.*;

public final class BridgeIme extends InputMethodService {
    static final String SOCKET = "org.anvildroid.ime.v1";
    private final Handler main = new Handler(Looper.getMainLooper());
    private LocalServerSocket server;
    private volatile boolean running;
    private Peer peer; // main thread only
    private long generation;
    private boolean active, composing;
    private EditorInfo editor;

    @Override public boolean onEvaluateInputViewShown() { return false; }
    @Override public boolean onEvaluateFullscreenMode() { return false; }
    @Override public void onCreate() {
        super.onCreate();
        running = true;
        new Thread(() -> {
            try {
                server = new LocalServerSocket(SOCKET);
                while (running) {
                    LocalSocket socket = server.accept();
                    int uid = socket.getPeerCredentials().getUid();
                    // Android system UID is the Waydroid composer; self is for smoke tests.
                    if (uid != 1000 && uid != android.os.Process.myUid()) {
                        socket.close();
                        continue;
                    }
                    socket.setSoTimeout(10000);
                    Peer next = new Peer(socket);
                    main.post(() -> {
                        if (!running) { next.close(); return; }
                        if (peer != null) peer.close();
                        peer = next;
                        ++generation;
                        publish();
                        next.start();
                    });
                }
            } catch (IOException e) {
                if (running) android.util.Log.e("AnvilDroidIme", "Local receiver unavailable");
            }
        }, "anvildroid-ime-accept").start();
    }
    @Override public void onStartInput(EditorInfo info, boolean restarting) {
        super.onStartInput(info, restarting);
        ++generation;
        editor = info;
        active = info != null && info.inputType != InputType.TYPE_NULL;
        composing = false;
        publish();
    }
    @Override public void onFinishInput() {
        ++generation;
        active = composing = false;
        editor = null;
        publish();
        super.onFinishInput();
    }
    @Override public void onDestroy() {
        running = false;
        if (peer != null) peer.close();
        try { if (server != null) server.close(); } catch (IOException ignored) {}
        super.onDestroy();
    }
    private void publish() {
        if (peer == null) return;
        try {
            JSONObject state = new JSONObject().put("v", 1).put("op", "state")
                .put("generation", generation).put("active", active)
                .put("package", editor == null ? "" : editor.packageName)
                .put("inputType", editor == null ? 0 : editor.inputType)
                .put("imeOptions", editor == null ? 0 : editor.imeOptions);
            peer.send(state);
        } catch (JSONException ignored) {}
    }
    private void receive(Peer source, JSONObject msg) {
        if (source != peer) return;
        try {
            if (msg.getInt("v") != 1) throw new JSONException("Version");
            String op = msg.getString("op");
            if (op.equals("state")) { publish(); return; }
            if (!op.equals("batch")) throw new JSONException("Operation");
            long requested = msg.getLong("generation");
            if (!active || requested != generation || getCurrentInputConnection() == null) {
                reply(source, "stale", msg.optLong("id"));
                return;
            }
            String commit = msg.optString("commit", ""), preedit = msg.optString("preedit", "");
            int before = msg.optInt("deleteBefore", 0), after = msg.optInt("deleteAfter", 0);
            int begin = msg.optInt("cursorBegin", preedit.length());
            int end = msg.optInt("cursorEnd", begin);
            boolean hidden = begin == -1 && end == -1;
            if (!Wire.validText(commit) || !Wire.validText(preedit) ||
                before < 0 || before > 4096 || after < 0 || after > 4096 ||
                (!hidden && (!Wire.boundary(preedit, begin) || !Wire.boundary(preedit, end))))
                throw new JSONException("Invalid text range");
            InputConnection ic = getCurrentInputConnection();
            ic.beginBatchEdit();
            boolean ok = true;
            try {
                if (composing) ok = ic.setComposingText("", 1);
                composing = false;
                if (before != 0 || after != 0) {
                    CharSequence left = ic.getTextBeforeCursor(4096, 0);
                    CharSequence right = ic.getTextAfterCursor(4096, 0);
                    int b = left == null ? -1 : Wire.unitsForBytes(left.toString(), before, true);
                    int a = right == null ? -1 : Wire.unitsForBytes(right.toString(), after, false);
                    if (b < 0 || a < 0) ok = false;
                    else if (ok) ok = ic.deleteSurroundingText(b, a);
                }
                if (ok && !commit.isEmpty()) ok = ic.commitText(commit, 1);
                if (ok && !preedit.isEmpty()) {
                    ok = ic.setComposingText(preedit, 1);
                    composing = ok;
                    if (ok && !hidden && (begin != preedit.length() || end != begin)) {
                        ExtractedText text = ic.getExtractedText(new ExtractedTextRequest(), 0);
                        if (text != null) {
                            int start = text.startOffset + text.selectionEnd - preedit.length();
                            if (start >= 0) ok = ic.setSelection(start + begin, start + end);
                        }
                    }
                }
            } finally { ic.endBatchEdit(); }
            reply(source, ok ? "ok" : "unsupported", msg.optLong("id"));
        } catch (JSONException e) {
            reply(source, "invalid", msg.optLong("id"));
        }
    }
    private void reply(Peer source, String status, long id) {
        try {
            source.send(new JSONObject().put("v", 1).put("op", "result")
                .put("generation", generation).put("id", id).put("status", status));
        } catch (JSONException ignored) {}
    }
    private final class Peer {
        final LocalSocket socket;
        final ArrayBlockingQueue<JSONObject> output = new ArrayBlockingQueue<>(16);
        volatile boolean closed;
        Peer(LocalSocket socket) { this.socket = socket; }
        void send(JSONObject msg) { if (!closed && !output.offer(msg)) close(); }
        void close() {
            closed = true;
            try { socket.close(); } catch (IOException ignored) {}
        }
        void start() {
            new Thread(() -> {
                try {
                    DataOutputStream out = new DataOutputStream(socket.getOutputStream());
                    while (!closed) {
                        JSONObject msg = output.poll(1, java.util.concurrent.TimeUnit.SECONDS);
                        if (msg != null) Wire.write(out, msg);
                    }
                } catch (Exception ignored) { close(); }
            }, "anvildroid-ime-output").start();
            new Thread(() -> {
                try {
                    DataInputStream in = new DataInputStream(socket.getInputStream());
                    while (!closed) {
                        JSONObject msg = Wire.read(in);
                        // Bound work waiting on the Android main thread to one frame.
                        java.util.concurrent.CountDownLatch applied = new java.util.concurrent.CountDownLatch(1);
                        main.post(() -> { try { receive(this, msg); } finally { applied.countDown(); } });
                        if (!applied.await(5, java.util.concurrent.TimeUnit.SECONDS)) break;
                    }
                } catch (Exception ignored) {
                } finally {
                    close();
                    main.post(() -> {
                        if (peer == this) {
                            peer = null;
                            ++generation;
                            InputConnection ic = getCurrentInputConnection();
                            if (composing && ic != null) ic.finishComposingText();
                            composing = false;
                        }
                    });
                }
            }, "anvildroid-ime-input").start();
        }
    }
}
