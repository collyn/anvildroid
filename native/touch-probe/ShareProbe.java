package org.anvildroid.touchprobe;

import android.content.Intent;
import android.os.Process;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.Charset;

/** Explicit diagnostic app-UID FUSE probe, limited to acceptance marker files. */
final class ShareProbe {
    interface Reporter { void log(String message); }
    static void report(Intent intent, Reporter output) {
        if (!"org.anvildroid.touchprobe.SHARE_PROBE".equals(intent.getAction())) return;
        String name = intent.getStringExtra("share");
        String token = intent.getStringExtra("token");
        if (name == null || !name.matches("[A-Za-z0-9][A-Za-z0-9_-]{0,47}") ||
            token == null || !token.matches("[A-Za-z0-9_-]{1,80}")) {
            output.log("SHARE invalid arguments"); return;
        }
        File directory = new File("/sdcard/AnvilDroid", name);
        output.log("SHARE uid=" + Process.myUid() + " name=" + name + " token=" + token);
        try {
            byte[] data = new byte[256];
            int count;
            try (FileInputStream input = new FileInputStream(new File(directory, "acceptance-read"))) {
                count = input.read(data);
            }
            String value = count < 0 ? "" : new String(data, 0, count, Charset.forName("UTF-8"));
            output.log("SHARE read=" + value.equals(token));
        } catch (IOException | SecurityException error) {
            output.log("SHARE read=false error=" + error);
        }
        try {
            try (FileOutputStream stream = new FileOutputStream(new File(directory, "acceptance-write-" + token + "-app"))) {
                stream.write(token.getBytes(Charset.forName("UTF-8")));
            }
            output.log("SHARE write=true");
        } catch (IOException | SecurityException error) {
            output.log("SHARE write=false error=" + error);
        }
    }
}
