package org.anvildroid.runtime;

import java.net.InetAddress;
import java.net.URL;
import javax.net.ssl.HttpsURLConnection;

/** Disposable acceptance probe only; never invoked during normal runtime boot. */
public final class RuntimeNetworkProbe {
    public static void main(String[] args) throws Exception {
        if (args.length != 0) throw new IllegalArgumentException("No arguments accepted");
        if (InetAddress.getAllByName("www.google.com").length == 0)
            throw new IllegalStateException("DNS returned no addresses");
        HttpsURLConnection request = (HttpsURLConnection)
            new URL("https://www.google.com/generate_204").openConnection();
        request.setConnectTimeout(10000);
        request.setReadTimeout(10000);
        request.setInstanceFollowRedirects(false);
        try {
            if (request.getResponseCode() != 204)
                throw new IllegalStateException("Unexpected HTTPS response");
            System.out.println("ANVILDROID_DNS_HTTPS_READY");
        } finally { request.disconnect(); }
    }
}
