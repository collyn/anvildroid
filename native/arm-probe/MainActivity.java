package org.anvildroid.armprobe;

import android.app.Activity;
import android.content.Context;
import android.os.Bundle;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** Disposable acceptance probe: its only persisted output is its own result. */
public final class MainActivity extends Activity {
    private static native int run(int seed);

    static String execute(Context context) {
        String result;
        try {
            System.loadLibrary("armprobe");
            // The seed crosses JNI and volatile arithmetic executes in ARM code.
            if (run(7) != 7007) throw new IllegalStateException("Native arithmetic mismatch");
            result = "ARM_NATIVE_EXECUTION_PASS";
        } catch (Throwable error) {
            result = "FAIL: " + error.toString();
        }
        try (FileOutputStream output = new FileOutputStream(new File(context.getFilesDir(), "result"))) {
            output.write(result.getBytes(StandardCharsets.UTF_8));
        } catch (Exception error) {
            throw new IllegalStateException("Cannot save probe result", error);
        }
        return result;
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView view = new TextView(this);
        view.setText(execute(this));
        setContentView(view);
    }
}
