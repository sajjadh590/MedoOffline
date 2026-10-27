package ir.medo.app;

import java.util.Locale;

/**
 * Decides what a scanned QR payload asks for.
 *
 * Both the bank and the app are served by the same little share server, so the
 * only difference is the path: {@code /medo.medo} is an import, {@code
 * /medo.apk} is an update. Anything else is treated as a share address.
 */
public final class ScanTarget {

    public static final int UNKNOWN = 0;
    public static final int BANK = 1;
    public static final int APK = 2;

    private ScanTarget() {
    }

    public static String normalize(String raw) {
        String value = raw == null ? "" : raw.trim();
        // QR payloads sometimes arrive with a trailing newline from the encoder.
        int cut = value.length();
        while (cut > 0 && Character.isWhitespace(value.charAt(cut - 1))) {
            cut--;
        }
        value = value.substring(0, cut);
        if (value.isEmpty()) {
            return "";
        }
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://" + value;
        }
        return value;
    }

    public static int kind(String raw) {
        String url = normalize(raw);
        if (url.isEmpty()) {
            return UNKNOWN;
        }
        String path = url.toLowerCase(Locale.ROOT);
        int query = path.indexOf('?');
        if (query >= 0) {
            path = path.substring(0, query);
        }
        if (path.endsWith(".apk") || path.endsWith("/medo.apk") || path.endsWith("/app.apk")) {
            return APK;
        }
        if (path.endsWith("/medo.medo") || path.endsWith("/bank.json")
                || path.endsWith("/medo.json") || path.endsWith(".medo")) {
            return BANK;
        }
        // A bare host:port is the share root — the bank downloader knows how
        // to turn it into /medo.medo.
        return BANK;
    }
}
