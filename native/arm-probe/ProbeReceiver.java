package org.anvildroid.armprobe;

/** Explicit component broadcast permits acceptance without display interaction. */
public final class ProbeReceiver extends android.content.BroadcastReceiver {
    @Override public void onReceive(android.content.Context context, android.content.Intent intent) {
        setResultData(MainActivity.execute(context));
    }
}
