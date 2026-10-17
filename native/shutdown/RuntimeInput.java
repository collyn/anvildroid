package org.anvildroid.runtime;

import android.app.UiAutomation;
import android.content.res.Resources;
import android.os.HandlerThread;
import android.os.Looper;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.List;

/** Acknowledge only Android's known, otherwise invisible build warning. */
public final class RuntimeInput {
    public static void main(String[] args) {
        HandlerThread thread = new HandlerThread("anvildroid-boot-input");
        UiAutomation automation = null;
        try {
            if (args.length != 1 || !args[0].equals("acknowledge-build-warning"))
                throw new IllegalArgumentException("Invalid input recovery request");
            Looper.prepareMainLooper();
            thread.start();
            Class<?> connection = Class.forName("android.app.IUiAutomationConnection");
            Object binder = Class.forName("android.app.UiAutomationConnection").getConstructor().newInstance();
            automation = (UiAutomation) UiAutomation.class.getConstructor(Looper.class, connection)
                .newInstance(thread.getLooper(), binder);
            UiAutomation.class.getMethod("connect").invoke(automation);
            Resources resources = Resources.getSystem();
            int messageId = resources.getIdentifier("system_error_manufacturer", "string", "android");
            if (messageId == 0) throw new IllegalStateException("Unknown Android build warning resource");
            String message = resources.getString(messageId);
            AccessibilityNodeInfo root = automation.getRootInActiveWindow();
            if (root == null || !"android".contentEquals(root.getPackageName())) {
                System.out.println("ANVILDROID_INPUT_NO_BUILD_WARNING");
                return;
            }
            List<AccessibilityNodeInfo> messages = root.findAccessibilityNodeInfosByViewId("android:id/message");
            List<AccessibilityNodeInfo> buttons = root.findAccessibilityNodeInfosByViewId("android:id/button1");
            if (messages.size() != 1 || buttons.size() != 1 ||
                    !message.contentEquals(messages.get(0).getText()) ||
                    !resources.getText(android.R.string.ok).toString().contentEquals(buttons.get(0).getText()) ||
                    !buttons.get(0).isEnabled() || !buttons.get(0).isClickable()) {
                System.out.println("ANVILDROID_INPUT_NO_BUILD_WARNING");
                return;
            }
            // Never inject a global Enter/tap: an unrelated dialog can take focus.
            if (!buttons.get(0).performAction(AccessibilityNodeInfo.ACTION_CLICK))
                throw new IllegalStateException("Android build warning acknowledgement failed");
            System.out.println("ANVILDROID_BUILD_WARNING_ACKNOWLEDGED");
        } catch (Exception error) {
            System.err.println("Desktop input recovery failed: " + error);
            System.exit(1);
        } finally {
            if (automation != null) {
                try { UiAutomation.class.getMethod("disconnect").invoke(automation); }
                catch (Exception ignored) {}
            }
            thread.quitSafely();
        }
    }
}
