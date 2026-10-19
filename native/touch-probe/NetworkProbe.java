package org.anvildroid.touchprobe;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkInfo;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.SystemClock;

/** API23-compatible, read-only app observations, without network identifiers. */
final class NetworkProbe {
    interface Reporter { void log(String message); }

    @SuppressWarnings("deprecation")
    static void report(Context context, Reporter output) {
        output.log("NETWORK snapshot uptimeMs=" + SystemClock.uptimeMillis()
            + " sdk=" + Build.VERSION.SDK_INT);
        try {
            ConnectivityManager manager = (ConnectivityManager)
                context.getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkInfo info = manager.getActiveNetworkInfo();
            output.log("NETWORK activeInfo=" + (info == null ? "none" :
                "type=" + info.getTypeName() + "(" + info.getType() + ")"
                + " connected=" + info.isConnected() + " available=" + info.isAvailable()
                + " state=" + info.getDetailedState()));
            Network active = manager.getActiveNetwork();
            Network[] networks = manager.getAllNetworks();
            output.log("NETWORK count=" + networks.length + " activePresent=" + (active != null));
            for (int i = 0; i < networks.length; ++i) {
                NetworkCapabilities caps = manager.getNetworkCapabilities(networks[i]);
                output.log("NETWORK index=" + i + " active=" + networks[i].equals(active)
                    + " capabilities=" + (caps == null ? "none" :
                    "wifi=" + caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    + " ethernet=" + caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
                    + " cellular=" + caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
                    + " internet=" + caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    + " validated=" + caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)));
            }
        } catch (RuntimeException error) {
            output.log("NETWORK unavailable=" + error.getClass().getSimpleName());
        }
        try {
            WifiManager manager = (WifiManager) context.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
            WifiInfo info = manager.getConnectionInfo();
            output.log("WIFI enabled=" + manager.isWifiEnabled() + " state=" + manager.getWifiState()
                + " info=" + (info == null ? "none" :
                "supplicant=" + info.getSupplicantState() + " networkIdPresent=" + (info.getNetworkId() >= 0)
                + " linkMbps=" + info.getLinkSpeed() + " ipPresent=" + (info.getIpAddress() != 0)));
        } catch (RuntimeException error) {
            output.log("WIFI unavailable=" + error.getClass().getSimpleName());
        }
    }
}
