package ir.medo.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * A tiny, read-only provider for the files this app hands to others.
 *
 * Android 7 blocks {@code file://} URIs, and the app has no AndroidX, so
 * {@code FileProvider} is not available. Only names produced by the app itself
 * are served, and only from its private cache/exports directories, so a
 * crafted URI cannot read anything else.
 */
public final class SharedFileProvider extends ContentProvider {

    public static final String AUTHORITY = "ir.medo.app.files";

    private static final String[] COLUMNS = {
            OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE
    };

    public static Uri uriFor(Context context, File file) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(file.getName())
                .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File file = resolve(uri);
        if (file == null) {
            return null;
        }
        String[] columns = projection == null ? COLUMNS : projection;
        MatrixCursor cursor = new MatrixCursor(columns, 1);
        Object[] row = new Object[columns.length];
        for (int i = 0; i < columns.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) {
                row[i] = file.getName();
            } else if (OpenableColumns.SIZE.equals(columns[i])) {
                row[i] = file.length();
            }
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override
    public String getType(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name == null) {
            return "application/octet-stream";
        }
        if (name.endsWith(".pdf")) {
            return "application/pdf";
        }
        if (name.endsWith(".apk")) {
            return "application/vnd.android.package-archive";
        }
        if (name.endsWith(".medo") || name.endsWith(".json")) {
            return "application/json";
        }
        return "application/octet-stream";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("read-only");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("read-only");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection,
                      String[] selectionArgs) {
        throw new UnsupportedOperationException("read-only");
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode)
            throws FileNotFoundException {
        if (mode != null && mode.contains("w")) {
            throw new FileNotFoundException("read-only provider");
        }
        File file = resolve(uri);
        if (file == null || !file.isFile()) {
            throw new FileNotFoundException(String.valueOf(uri));
        }
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    /** Only a bare, safe file name ever reaches the filesystem. */
    private File resolve(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name == null || name.isEmpty()) {
            return null;
        }
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            boolean safe = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                    || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
            if (!safe) {
                return null;
            }
        }
        Context context = getContext();
        if (context == null) {
            return null;
        }
        File[] roots = new File[]{
                new File(context.getCacheDir(), "update"),
                new File(context.getFilesDir(), "exports"),
                exportDir(context),
        };
        for (File root : roots) {
            if (root == null) {
                continue;
            }
            File candidate = new File(root, name);
            if (candidate.isFile()) {
                return candidate;
            }
        }
        return null;
    }

    /** Same location {@link PdfExporter} writes to. */
    static File exportDir(Context context) {
        File external = context.getExternalFilesDir(android.os.Environment.DIRECTORY_DOCUMENTS);
        return external != null ? external : new File(context.getFilesDir(), "exports");
    }
}
