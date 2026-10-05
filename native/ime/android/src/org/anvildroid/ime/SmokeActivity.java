package org.anvildroid.ime;

import android.app.Activity;
import android.os.*;
import android.net.*;
import android.widget.*;
import android.view.inputmethod.InputMethodManager;
import java.io.*;
import java.util.concurrent.FutureTask;
import org.json.*;

/** Explicit, disposable editor. Test text never comes from another app. */
public final class SmokeActivity extends Activity {
    private EditText editor;
    private TextView status;
    private boolean autoStarted;
    private String stage = "connect";
    private String run;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        run = getIntent().getStringExtra("run");
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        status = new TextView(this);
        status.setText("AnvilDroid IME receiver test");
        editor = new EditText(this);
        editor.setSingleLine(false);
        editor.setHint("Ô nhập thử riêng — không dùng dữ liệu cá nhân");
        Button button = new Button(this);
        button.setText("Test receiver");
        button.setOnClickListener(v -> startTest());
        layout.addView(status);
        layout.addView(editor);
        layout.addView(button);
        setContentView(layout);
        editor.requestFocus();
    }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (focused && !autoStarted && getIntent().getBooleanExtra("smoke", false)) {
            autoStarted = true;
            new Handler().postDelayed(() -> startTest(), 1000);
        }
    }
    private void startTest() {
        editor.setText("");
        editor.requestFocus();
        ((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).restartInput(editor);
        status.setText("Testing receiver…");
        new Handler().postDelayed(() -> new Thread(() -> test(), "ime-smoke").start(), 800);
    }
    private void expect(String expected) throws Exception {
        for (int attempt = 0; attempt < 40; attempt++) {
            FutureTask<Boolean> check = new FutureTask<>(() -> editor.getText().toString().equals(expected));
            runOnUiThread(check);
            if (check.get(3, java.util.concurrent.TimeUnit.SECONDS)) return;
            Thread.sleep(50);
        }
        stage += "-editor";
        throw new IOException("Editor assertion failed");
    }
    private JSONObject readResult(DataInputStream in) throws Exception {
        for (int i = 0; i < 8; i++) {
            JSONObject msg = Wire.read(in);
            if (msg.optString("op").equals("result")) return msg;
        }
        throw new IOException("No result");
    }
    private void test() {
        try (LocalSocket socket = new LocalSocket()) {
            socket.connect(new LocalSocketAddress(BridgeIme.SOCKET));
            socket.setSoTimeout(4000);
            DataInputStream in = new DataInputStream(socket.getInputStream());
            DataOutputStream out = new DataOutputStream(socket.getOutputStream());
            JSONObject state = Wire.read(in);
            stage = "active-editor";
            if (!state.getBoolean("active") || !state.getString("package").equals(getPackageName()))
                throw new IOException("Test editor not active");
            long gen = state.getLong("generation");
            String[] commits = {"Tiếng Việt 😀", "", "", "", "Tôi"};
            String[] preedits = {"", "", "Toi", "Tôi", ""};
            String[] expected = {"Tiếng Việt 😀", "Tiếng Việt ", "Tiếng Việt Toi",
                                  "Tiếng Việt Tôi", "Tiếng Việt Tôi"};
            for (int i = 0; i < commits.length; i++) {
                stage = "batch-" + i;
                Wire.write(out, new JSONObject().put("v", 1).put("op", "batch")
                    .put("generation", gen).put("id", i)
                    .put("commit", commits[i]).put("preedit", preedits[i])
                    .put("deleteBefore", i == 1 ? 4 : 0));
                String status = readResult(in).getString("status");
                if (!status.equals("ok")) {
                    stage += "-" + status;
                    throw new IOException("Batch rejected");
                }
                expect(expected[i]);
            }
            Wire.write(out, new JSONObject().put("v", 1).put("op", "batch")
                .put("generation", gen - 1).put("commit", "must not insert"));
            if (!readResult(in).getString("status").equals("stale")) throw new IOException("Stale accepted");
            expect("Tiếng Việt Tôi");
            android.util.Log.i("AnvilDroidImeTest", "run=" + run + " PASS commit/preedit/emoji-delete/stale-generation");
            runOnUiThread(() -> status.setText("PASS — receiver, composing, emoji và phiên cũ"));
        } catch (Exception e) {
            android.util.Log.e("AnvilDroidImeTest", "run=" + run + " FAIL " + stage + " " + e.getClass().getSimpleName());
            runOnUiThread(() -> status.setText("FAIL — kiểm tra IME được chọn và log test"));
        }
    }
}
