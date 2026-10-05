package org.anvildroid.ime;

import java.io.*;
import java.nio.*;
import java.nio.charset.*;
import org.json.*;

/** Length-prefixed UTF-8 JSON. Never use Java's modified UTF-8 on this wire. */
final class Wire {
    static final int LIMIT = 32768;
    static JSONObject read(DataInputStream in) throws IOException, JSONException {
        int size = in.readInt();
        if (size < 2 || size > LIMIT) throw new IOException("Invalid frame size");
        byte[] bytes = new byte[size];
        in.readFully(bytes);
        String text = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString();
        return new JSONObject(text);
    }
    static void write(DataOutputStream out, JSONObject value) throws IOException {
        byte[] bytes = value.toString().getBytes(StandardCharsets.UTF_8);
        if (bytes.length > LIMIT) throw new IOException("Frame too large");
        out.writeInt(bytes.length);
        out.write(bytes);
        out.flush();
    }
    static int unitsForBytes(String text, int bytes, boolean fromEnd) {
        if (bytes < 0 || bytes > 4096) return -1;
        int offset = fromEnd ? text.length() : 0, used = 0, units = 0;
        while (used < bytes) {
            if (fromEnd ? offset == 0 : offset == text.length()) return -1;
            int cp = fromEnd ? text.codePointBefore(offset) : text.codePointAt(offset);
            if (cp >= 0xd800 && cp <= 0xdfff) return -1;
            int width = cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
            used += width;
            int n = Character.charCount(cp);
            units += n;
            offset += fromEnd ? -n : n;
        }
        return used == bytes ? units : -1;
    }
    static boolean boundary(String text, int index) {
        return index >= 0 && index <= text.length() &&
            !(index > 0 && index < text.length() &&
              Character.isHighSurrogate(text.charAt(index - 1)) &&
              Character.isLowSurrogate(text.charAt(index)));
    }
    static boolean validText(String text) {
        if (text.length() > 4096) return false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i == text.length() || !Character.isLowSurrogate(text.charAt(i))) return false;
            } else if (Character.isLowSurrogate(c)) return false;
        }
        return text.getBytes(StandardCharsets.UTF_8).length <= 4096;
    }
}
