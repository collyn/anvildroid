package org.anvildroid.runtime;

import org.json.JSONObject;
import android.system.Os;
import android.system.OsConstants;
import java.io.FileOutputStream;
import java.lang.reflect.Method;
import java.nio.channels.FileLock;
import java.util.List;

/** Runtime-local, serialized presentation transitions; never stops packages. */
public final class RuntimePresentation {
    private static final String SESSION = "waydroid.anvil_full_ui";
    private static final String SAVED = "anvildroid_presentation_restore";
    private static final Class<?> PROPS;
    static {
        try { PROPS = Class.forName("android.os.SystemProperties"); }
        catch (Exception e) { throw new ExceptionInInitializerError(e); }
    }
    private static String get(String key) throws Exception {
        return (String) PROPS.getMethod("get", String.class).invoke(null, key);
    }
    private static void put(String key, String value) throws Exception {
        PROPS.getMethod("set", String.class, String.class).invoke(null, key, value);
    }
    public static void main(String[] args) {
        try {
            if (args.length != 2 || !args[1].matches("[a-f0-9]{32}") ||
                    !(args[0].equals("enter") || args[0].equals("leave") || args[0].equals("recover") || args[0].equals("show")))
                throw new IllegalArgumentException("Invalid presentation request");
            // Both controller and companion use shell UID. Never follow a
            // guest-supplied symlink, and release the lock on process failure.
            try (FileOutputStream file = new FileOutputStream(Os.open(
                    "/data/waydroid_tmp/anvildroid/presentation-lock",
                    OsConstants.O_CREAT | OsConstants.O_RDWR | OsConstants.O_NOFOLLOW,
                    0660)); FileLock lock = file.getChannel().lock()) {
                transition(args[0], args[1]);
            }
            System.out.println("ANVILDROID_PRESENTATION_OK");
        } catch (Exception e) {
            System.err.println("Presentation transition failed: " + e);
            System.exit(1);
        }
    }
    private static void transition(String action, String token) throws Exception {
        if (action.equals("show")) { closeAppTasks(); return; }
        Object wm = Class.forName("android.view.WindowManagerGlobal")
                .getMethod("getWindowManagerService").invoke(null);
        Class<?> api = Class.forName("android.view.IWindowManager");
        Method read = api.getMethod("getWindowingMode", int.class);
        Method write = api.getMethod("setWindowingMode", int.class, int.class);
        String session = get(SESSION);
        String saved = setting(SAVED);
        if (action.equals("recover") || action.equals("leave")) {
            // A queued close from an old window must not undo a later open.
            if (saved == null) return;
            JSONObject previous = new JSONObject(saved);
            if (action.equals("leave") && !token.equals(session) &&
                    !(session.isEmpty() && token.equals(previous.optString("token")))) return;
            int original = previous.getInt("mode");
            if (original != 0 && original != 1 && original != 5) throw new IllegalStateException("Invalid original window mode");
            if (action.equals("leave")) closeAppTasks();
            write.invoke(wm, 0, original);
            policy(previous.isNull("policy") ? null : previous.getString("policy"));
            put("waydroid.active_apps", "none");
            put(SESSION, "");
            save(null);
            return;
        }
        if (!session.isEmpty()) {
            if (saved == null) throw new IllegalStateException("Missing presentation recovery state");
            save(new JSONObject(saved).put("token", token).toString());
            put(SESSION, token);
            return;
        }
        if (saved != null) { transition("recover", token); }
        int original = (Integer) read.invoke(wm, 0);
        if (original != 0 && original != 1 && original != 5)
            throw new IllegalStateException("Unsupported display windowing mode: " + original);
        String oldPolicy = setting("policy_control");
        save(new JSONObject().put("mode", original).put("token", token).put("policy", oldPolicy == null ? JSONObject.NULL : oldPolicy).toString());
        try {
            closeAppTasks();
            write.invoke(wm, 0, 1); // FULLSCREEN, inherited by launcher apps.
            if ((Integer) read.invoke(wm, 0) != 1)
                throw new IllegalStateException("Android did not switch to fullscreen");
            policy("null*");
            put(SESSION, token);
        } catch (Exception error) {
            write.invoke(wm, 0, original);
            policy(oldPolicy);
            save(null);
            throw error;
        }
    }
    // cmd uses the shell attribution source. ActivityThread's system context
    // reports package android, which SettingsProvider correctly rejects for UID 2000.
    private static String command(String... args) throws Exception {
        Process process = new ProcessBuilder(args).redirectErrorStream(true).start();
        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[1024];
        int count;
        while ((count = process.getInputStream().read(buffer)) != -1) {
            if (output.size() + count > 8192) { process.destroy(); throw new IllegalStateException("Settings output too large"); }
            output.write(buffer, 0, count);
        }
        String text = output.toString("UTF-8").trim();
        if (process.waitFor() != 0 || text.startsWith("Error") || text.contains("Exception"))
            throw new IllegalStateException("Settings command failed: " + text);
        return text;
    }
    private static String setting(String key) throws Exception {
        String value = command("/system/bin/cmd", "settings", "get", "secure", key);
        return value.equals("null") ? null : value;
    }
    private static void save(String value) throws Exception { setSetting(SAVED, value); }
    private static void policy(String value) throws Exception { setSetting("policy_control", value); }
    private static void setSetting(String key, String value) throws Exception {
        if (value == null) command("/system/bin/cmd", "settings", "delete", "secure", key);
        else command("/system/bin/cmd", "settings", "put", "secure", key, value);
        if (!java.util.Objects.equals(value, setting(key)))
            throw new IllegalStateException("Android did not save setting: " + key);
    }
    private static void closeAppTasks() throws Exception {
        Object service = Class.forName("android.app.ActivityTaskManager")
                .getDeclaredMethod("getService").invoke(null);
        Class<?> api = Class.forName("android.app.IActivityTaskManager");
        List<?> tasks = (List<?>) api.getMethod("getTasks", int.class, boolean.class,
                boolean.class, int.class).invoke(service, 256, false, true, -1);
        if (tasks.size() >= 256) throw new IllegalStateException("Too many tasks to verify");
        Method remove = api.getMethod("removeTask", int.class);
        for (Object task : tasks) {
            Class<?> type = task.getClass();
            if (type.getField("userId").getInt(task) != 0) continue;
            Object config = type.getField("configuration").get(task);
            Object window = config.getClass().getField("windowConfiguration").get(config);
            int activityType = (Integer) window.getClass().getMethod("getActivityType").invoke(window);
            if (activityType != 1) continue; // STANDARD only; preserve home/recents.
            Object component = type.getField("baseActivity").get(task);
            if (component == null) continue;
            String pkg = (String) component.getClass().getMethod("getPackageName").invoke(component);
            if (pkg.equals("android") || pkg.equals("com.android.systemui")) continue;
            int id = type.getField("taskId").getInt(task);
            if (id <= 0 || !((Boolean) remove.invoke(service, id)))
                throw new IllegalStateException("Android refused app task removal: " + id);
        }
    }
}
