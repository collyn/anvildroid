package org.anvildroid.ime;

import android.content.*;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.nio.*;
import java.nio.charset.*;
import java.util.Arrays;
import org.json.*;

/** Main-thread clipboard endpoint on the authenticated IME channel. */
final class ClipboardSync implements ClipboardManager.OnPrimaryClipChangedListener {
    interface Sender { void send(JSONObject value); }
    private static final int LIMIT = 1024 * 1024, CHUNK = 18000;
    private final ClipboardManager manager;
    private final Sender sender;
    private byte[] last;
    private boolean lastClear;
    private long revision, incomingId;
    private int expected;
    private boolean clear;
    private ByteArrayOutputStream incoming;
    ClipboardSync(Context context, Sender sender) {
        manager = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
        this.sender = sender;
        manager.addPrimaryClipChangedListener(this);
    }
    void close() { manager.removePrimaryClipChangedListener(this); incoming = null; }
    @Override public void onPrimaryClipChanged() { publish(false); }
    void publish(boolean force) {
        try {
            ClipData clip = manager.getPrimaryClip();
            boolean empty = clip == null || clip.getItemCount() == 0;
            // Do not coerce content URIs: only transfer clipboard text.
            CharSequence text = empty ? "" : clip.getItemAt(0).getText();
            if (text == null) return;
            byte[] bytes = text.toString().getBytes(StandardCharsets.UTF_8);
            if (bytes.length > LIMIT || (!force && lastClear == empty && Arrays.equals(last, bytes))) return;
            last = bytes; lastClear = empty;
            long id = ++revision;
            sender.send(new JSONObject().put("v", 1).put("op", "clipboard_begin")
                .put("id", id).put("bytes", bytes.length).put("clear", empty).put("initial", force));
            for (int offset = 0; offset < bytes.length; offset += CHUNK) {
                int size = Math.min(CHUNK, bytes.length - offset);
                sender.send(new JSONObject().put("v", 1).put("op", "clipboard_chunk")
                    .put("id", id).put("offset", offset)
                    // Android JSONStringer escapes '/', while the native
                    // bounded parser reads this generated string directly.
                    .put("data", Base64.encodeToString(bytes, offset, size, Base64.NO_WRAP | Base64.URL_SAFE)));
            }
            sender.send(new JSONObject().put("v", 1).put("op", "clipboard_end").put("id", id));
        } catch (RuntimeException | JSONException ignored) {
            // Clipboard access can be revoked on focus changes. Never log data.
        }
    }
    boolean receive(JSONObject msg) throws JSONException {
        String op = msg.getString("op");
        if (!op.startsWith("clipboard_")) return false;
        if (op.equals("clipboard_get")) { publish(true); return true; }
        long id = msg.getLong("id");
        if (op.equals("clipboard_begin")) {
            int size = msg.getInt("bytes");
            if (size < 0 || size > LIMIT) throw new JSONException("Clipboard size");
            incomingId = id; expected = size; clear = msg.optBoolean("clear");
            if (clear && size != 0) throw new JSONException("Clipboard clear");
            incoming = new ByteArrayOutputStream(size);
        } else if (op.equals("clipboard_chunk")) {
            if (incoming == null || incomingId != id || msg.getInt("offset") != incoming.size())
                throw new JSONException("Clipboard sequence");
            byte[] bytes;
            try { bytes = Base64.decode(msg.getString("data"), Base64.NO_WRAP); }
            catch (IllegalArgumentException e) { throw new JSONException("Clipboard encoding"); }
            if (bytes.length > CHUNK || bytes.length + incoming.size() > expected)
                throw new JSONException("Clipboard overflow");
            incoming.write(bytes, 0, bytes.length);
        } else if (op.equals("clipboard_end")) {
            if (incoming == null || incomingId != id || incoming.size() != expected)
                throw new JSONException("Clipboard incomplete");
            byte[] bytes = incoming.toByteArray(); incoming = null;
            try {
                String value = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes)).toString();
                if (lastClear != clear || !Arrays.equals(last, bytes)) {
                    last = bytes; lastClear = clear;
                    if (clear && android.os.Build.VERSION.SDK_INT >= 28) {
                        try { ClipboardManager.class.getMethod("clearPrimaryClip").invoke(manager); }
                        catch (ReflectiveOperationException e) { throw new JSONException("Clipboard clear"); }
                    } else manager.setPrimaryClip(ClipData.newPlainText("", clear ? "" : value));
                }
            } catch (CharacterCodingException | RuntimeException e) { throw new JSONException("Clipboard unavailable"); }
        } else throw new JSONException("Clipboard operation");
        return true;
    }
}
