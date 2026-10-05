package org.anvildroid.runtime;

import java.lang.reflect.Method;
import java.util.List;

/** Close one verified leaf task, including tasks sharing Android's desktop. */
public final class RuntimeTask {
    public static void main(String[] args) {
        try {
            close(args);
            System.out.println("ANVILDROID_TASK_CLOSED");
        } catch (Exception error) {
            System.err.println("Task close failed: " + error);
            System.exit(1);
        }
    }

    private static void close(String[] args) throws Exception {
        if (args.length != 2 || !args[0].matches("[1-9][0-9]*") ||
                !args[1].matches("[A-Za-z0-9_]+(?:\\.[A-Za-z0-9_]+)+") ||
                args[1].equals("com.android.systemui") || args[1].equals("com.android.launcher3")) {
            throw new IllegalArgumentException("Invalid task target");
        }
        int taskId = Integer.parseInt(args[0]);
        Object service = Class.forName("android.app.ActivityTaskManager")
                .getDeclaredMethod("getService").invoke(null);
        Class<?> api = Class.forName("android.app.IActivityTaskManager");
        Method getTasks = api.getMethod("getTasks", int.class, boolean.class, boolean.class, int.class);
        List<?> tasks = (List<?>) getTasks.invoke(service, 256, false, true, -1);
        int matches = 0;
        for (Object task : tasks) {
            Class<?> type = task.getClass();
            if (type.getField("taskId").getInt(task) != taskId ||
                    type.getField("userId").getInt(task) != 0) continue;
            Object component = type.getField("baseActivity").get(task);
            if (component == null) continue;
            String pkg = (String) component.getClass().getMethod("getPackageName").invoke(component);
            if (args[1].equals(pkg)) matches++;
        }
        if (matches != 1) throw new IllegalStateException("Task no longer belongs to requested app");
        if (!((Boolean) api.getMethod("removeTask", int.class).invoke(service, taskId))) {
            throw new IllegalStateException("Android refused task removal");
        }
    }
}
