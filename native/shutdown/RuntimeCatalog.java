package org.anvildroid.runtime;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.os.Looper;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.util.List;
import org.json.JSONObject;

/** Resolve installed resources through Android, including Play Store split APKs. */
public final class RuntimeCatalog {
    public static void main(String[] args) {
        try {
            Looper.prepareMainLooper();
            Class<?> thread = Class.forName("android.app.ActivityThread");
            Object main = thread.getMethod("systemMain").invoke(null);
            Context context = (Context) thread.getMethod("getSystemContext").invoke(main);
            PackageManager pm = context.getPackageManager();
            Intent intent = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
            List<ResolveInfo> activities = pm.queryIntentActivities(intent, 0);
            if (activities.size() > 10000) throw new IllegalStateException("Catalog too large");
            JSONObject catalog = new JSONObject();
            int remaining = 600000;
            for (ResolveInfo info : activities) {
                String pkg = info.activityInfo.packageName;
                if (catalog.has(pkg)) continue;
                JSONObject app = new JSONObject();
                CharSequence label = info.loadLabel(pm);
                if (label != null && label.length() > 0 && label.length() <= 512)
                    app.put("label", label.toString());
                try {
                    Drawable icon = info.loadIcon(pm);
                    Bitmap bitmap = Bitmap.createBitmap(96, 96, Bitmap.Config.ARGB_8888);
                    icon.setBounds(0, 0, 96, 96);
                    icon.draw(new Canvas(bitmap));
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, bytes);
                    bitmap.recycle();
                    String encoded = Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP);
                    if (encoded.length() <= 48000 && encoded.length() <= remaining) {
                        app.put("icon_data", encoded);
                        remaining -= encoded.length();
                    }
                } catch (RuntimeException unavailable) {
                    // One missing resource must not hide the rest of the catalog.
                }
                catalog.put(pkg, app);
            }
            System.out.println(catalog.toString());
        } catch (Exception error) {
            System.err.println("App catalog failed: " + error);
            System.exit(1);
        }
    }
}
