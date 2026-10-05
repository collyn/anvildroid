package org.anvildroid.runtime;

import java.lang.reflect.Method;

/** Drain Android activity/system state without requesting a device power-off. */
public final class RuntimeShutdown {
    public static void main(String[] args) throws Exception {
        if (args.length != 0) throw new IllegalArgumentException("No arguments accepted");
        Class<?> manager = Class.forName("android.app.ActivityManager");
        Method getService = manager.getDeclaredMethod("getService");
        getService.setAccessible(true);
        Object service = getService.invoke(null);
        Method shutdown = Class.forName("android.app.IActivityManager").getMethod("shutdown", int.class);
        boolean timedOut = (Boolean) shutdown.invoke(service, 5000);
        if (timedOut) throw new IllegalStateException("Android activities did not finish shutdown");
        // SettingsProvider persists asynchronously (bounded batching). Let its
        // pending handler writes finish after activities have been quiesced.
        Thread.sleep(2000);
        System.out.println("ANVILDROID_SHUTDOWN_READY");
    }
}
