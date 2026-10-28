package ir.medo.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Where the bank lives on the phone, and how it gets there. */
public final class BankStore {

    public static final String FILE_NAME = "bank.medo";
    public static final String DEFAULT_ADDRESS = "http://127.0.0.1:8765";
    private static final long MAX_BYTES = 96L * 1024 * 1024;
    private static final String PREFS = "medo";

    private BankStore() {
    }

    public static File file(Context context) {
        return new File(context.getFilesDir(), FILE_NAME);
    }

    public static boolean hasBank(Context context) {
        return file(context).isFile();
    }

    public static String savedAddress(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return prefs.getString("address", DEFAULT_ADDRESS);
    }

    public static void saveAddress(Context context, String address) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString("address", address).apply();
    }

    public static String readableSize(Context context) {
        File f = file(context);
        if (!f.isFile()) {
            return "بدون بانک";
        }
        long bytes = f.length();
        if (bytes < 1024) {
            return bytes + " بایت";
        }
        if (bytes < 1024 * 1024) {
            return String.format(java.util.Locale.US, "%.0f کیلوبایت", bytes / 1024.0);
        }
        return String.format(java.util.Locale.US, "%.1f مگابایت", bytes / (1024.0 * 1024.0));
    }

    // ------------------------------------------------------------------ load

    public interface Loaded {
        void onLoaded(Bank bank);

        void onError(String message);
    }

    /** Parses off the UI thread: a full archive can be tens of megabytes. */
    public static void loadAsync(final Context context, final Loaded callback) {
        final Handler ui = new Handler(Looper.getMainLooper());
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    final Bank bank = read(context);
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            callback.onLoaded(bank);
                        }
                    });
                } catch (final Exception e) {
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            callback.onError(describe(e));
                        }
                    });
                }
            }
        }, "bank-load").start();
    }

    public static Bank read(Context context) throws Exception {
        Bank cached = Bank.cached();
        if (cached != null) {
            return cached;
        }
        File f = file(context);
        if (!f.isFile()) {
            return null;
        }
        // Bank.load sniffs the format: v2 is a zip read chunk by chunk, v1 is
        // the old single JSON document.
        Bank bank = Bank.load(f);
        Bank.remember(bank);
        return bank;
    }

    /** Validates before touching the saved file; a bad download cannot eat it. */
    public static Bank install(Context context, byte[] blob) throws Exception {
        File target = file(context);
        File tmp = new File(target.getAbsolutePath() + ".tmp");
        FileOutputStream out = new FileOutputStream(tmp);
        try {
            out.write(blob);
            out.flush();
            out.getFD().sync();
        } finally {
            out.close();
        }
        // Parse the bytes actually on disk: a truncated download fails here,
        // before the old bank is replaced.
        Bank bank = Bank.load(tmp);
        if (!tmp.renameTo(target)) {
            target.delete();
            if (!tmp.renameTo(target)) {
                throw new java.io.IOException("could not replace " + target);
            }
        }
        Bank.remember(bank);
        return bank;
    }

    public static void forget(Context context) {
        file(context).delete();
        Bank.remember(null);
    }

    // --------------------------------------------------------------- Wi-Fi/USB

    public interface Fetch {
        void onDone(byte[] data);

        void onError(String message);
    }

    /** "192.168.1.5:8765" -> "http://192.168.1.5:8765/medo.medo" */
    public static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty()) {
            return DEFAULT_ADDRESS + "/medo.medo";
        }
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            s = "http://" + s;
        }
        int schemeEnd = s.indexOf("://") + 3;
        int slash = s.indexOf('/', schemeEnd);
        if (slash < 0) {
            s = s + "/medo.medo";
        } else if (slash == s.length() - 1) {
            s = s + "medo.medo";
        }
        return s;
    }

    public static void fetch(final String address, final Fetch callback) {
        final Handler ui = new Handler(Looper.getMainLooper());
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(normalize(address));
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(8000);
                    conn.setReadTimeout(30000);
                    conn.setInstanceFollowRedirects(true);
                    conn.setRequestProperty("Accept", "application/zip,application/json,*/*");
                    int code = conn.getResponseCode();
                    if (code != 200) {
                        throw new java.io.IOException("سرور " + code + " پاسخ داد");
                    }
                    long length = conn.getContentLength();
                    if (length > MAX_BYTES) {
                        throw new java.io.IOException("فایل بیش از حد بزرگ است");
                    }
                    InputStream in = conn.getInputStream();
                    final byte[] data = Io.readAll(in);
                    if (data.length > MAX_BYTES) {
                        throw new java.io.IOException("فایل بیش از حد بزرگ است");
                    }
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            callback.onDone(data);
                        }
                    });
                } catch (final Exception e) {
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            callback.onError(describe(e));
                        }
                    });
                } finally {
                    if (conn != null) {
                        conn.disconnect();
                    }
                }
            }
        }, "bank-fetch").start();
    }

    // ------------------------------------------------------------- app update

    public interface Download {
        void onDone(File file);

        void onError(String message);
    }

    /** Streams an absolute URL to {@code target} (used for the APK update). */
    public static void download(final String absoluteUrl, final File target,
                                final Download callback) {
        final Handler ui = new Handler(Looper.getMainLooper());
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                File tmp = new File(target.getAbsolutePath() + ".part");
                try {
                    URL url = new URL(absoluteUrl);
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(120000);
                    conn.setInstanceFollowRedirects(true);
                    conn.setRequestProperty("Accept", "application/vnd.android.package-archive,*/*");
                    int code = conn.getResponseCode();
                    if (code != 200) {
                        throw new java.io.IOException("سرور " + code + " پاسخ داد");
                    }
                    long length = conn.getContentLength();
                    if (length > MAX_BYTES) {
                        throw new java.io.IOException("فایل بیش از حد بزرگ است");
                    }
                    InputStream in = conn.getInputStream();
                    FileOutputStream out = new FileOutputStream(tmp);
                    long total = 0;
                    try {
                        byte[] buffer = new byte[32 * 1024];
                        int read;
                        while ((read = in.read(buffer)) > 0) {
                            total += read;
                            if (total > MAX_BYTES) {
                                throw new java.io.IOException("فایل بیش از حد بزرگ است");
                            }
                            out.write(buffer, 0, read);
                        }
                        out.getFD().sync();
                    } finally {
                        out.close();
                    }
                    if (total < 1024 || !looksLikeApk(tmp)) {
                        throw new java.io.IOException("فایل دریافتی APK معتبر نیست");
                    }
                    if (!tmp.renameTo(target)) {
                        target.delete();
                        if (!tmp.renameTo(target)) {
                            throw new java.io.IOException("ذخیرهٔ فایل ممکن نشد");
                        }
                    }
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            callback.onDone(target);
                        }
                    });
                } catch (final Exception e) {
                    tmp.delete();
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            callback.onError(describe(e));
                        }
                    });
                } finally {
                    if (conn != null) {
                        conn.disconnect();
                    }
                }
            }
        }, "apk-download").start();
    }

    private static boolean looksLikeApk(File file) {
        java.io.FileInputStream in = null;
        try {
            in = new java.io.FileInputStream(file);
            return in.read() == 'P' && in.read() == 'K';
        } catch (Exception e) {
            return false;
        } finally {
            if (in != null) {
                try {
                    in.close();
                } catch (Exception ignored) {
                    // nothing to do
                }
            }
        }
    }

    private static String describe(Exception e) {
        String message = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        if (e instanceof java.net.UnknownHostException) {
            return "آدرس پیدا نشد — IP درست است؟";
        }
        if (e instanceof java.net.SocketTimeoutException) {
            return "پاسخی نیامد (timeout) — گوشی و کامپیوتر روی یک وای‌فای هستند؟";
        }
        if (e instanceof java.net.ConnectException) {
            return "اتصال رد شد — دستور medo share روی کامپیوتر اجرا است؟";
        }
        if (e instanceof IllegalArgumentException) {
            return message;      // parser messages are already in Persian
        }
        return message;
    }
}
