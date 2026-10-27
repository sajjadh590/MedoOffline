package ir.medo.app;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;

/** Small IO helpers; no dependencies, works the same on the JVM tests. */
public final class Io {

    private Io() {
    }

    public static String readText(File file) throws IOException {
        return new String(readAll(new FileInputStream(file)), "UTF-8");
    }

    public static byte[] readAll(InputStream in) throws IOException {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[16 * 1024];
            int n;
            while ((n = in.read(buffer)) > 0) {
                out.write(buffer, 0, n);
            }
            return out.toByteArray();
        } finally {
            in.close();
        }
    }
}
