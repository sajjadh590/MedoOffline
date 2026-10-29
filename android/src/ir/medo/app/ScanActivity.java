package ir.medo.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.hardware.Camera;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * «اسکن QR» — the phone's counterpart to the QR the desktop shows.
 *
 * The camera preview is decoded with zxing-core, bundled offline (no Play
 * Services, no network). The payload is either the bank URL or the APK URL;
 * MainActivity decides which. Older ROMs without a usable camera still get the
 * manual address dialog, so scanning is a shortcut, never the only way in.
 */
public final class ScanActivity extends Activity
        implements SurfaceHolder.Callback, Camera.PreviewCallback {

    public static final String EXTRA_RESULT = "text";

    private static final int PERMISSION = 71;
    private static final long FRAME_INTERVAL_MS = 180L;

    private SurfaceView surface;
    private Camera camera;
    private Camera.Size size;
    private boolean surfaceReady;
    private boolean decoding;
    private boolean finished;
    private volatile long lastFrameAt;
    private volatile int misses;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private ExecutorService worker;
    private TextView hint;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        worker = Executors.newSingleThreadExecutor();
        setContentView(build());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!hasPermission()) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, PERMISSION);
        } else if (surfaceReady) {
            openCamera();
        }
    }

    @Override
    protected void onPause() {
        releaseCamera();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        releaseCamera();
        if (worker != null) {
            worker.shutdownNow();
        }
        super.onDestroy();
    }

    // -------------------------------------------------------------------- UI

    private View build() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Ui.CAMERA_BG);

        surface = new SurfaceView(this);
        root.addView(surface, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        surface.getHolder().addCallback(this);

        LinearLayout top = Ui.row(this);
        top.setBackgroundColor(Ui.CAMERA_OVERLAY);
        top.setPadding(Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S2));
        TextView close = Ui.iconButton(this, "\u2039", 30, Ui.CAMERA_TEXT);
        Ui.describe(close, "بستن اسکنر");
        close.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
        top.addView(close);

        LinearLayout texts = Ui.column(this);
        texts.setPadding(Ui.dp(this, Ui.S2), 0, Ui.dp(this, Ui.S1), 0);
        TextView title = Ui.bold(this, "اسکن QR", 19, Ui.CAMERA_TEXT);
        texts.addView(title);
        hint = Ui.text(this, "کد QR کامپیوتر را داخل کادر بگیر", Ui.T_SM, Ui.CAMERA_HINT);
        hint.setPadding(0, Ui.dp(this, 2), 0, 0);
        texts.addView(hint);
        top.addView(texts, Ui.weight(1f));
        root.addView(top, frameParams(Gravity.TOP, -1, -2));

        // Framing square: a plain border, no image assets.
        View frame = new View(this);
        GradientDrawable border = new GradientDrawable();
        border.setColor(Color.TRANSPARENT);
        border.setCornerRadius(Ui.dp(this, 16));
        border.setStroke(Ui.dp(this, 2), Ui.CAMERA_ACCENT);
        frame.setBackground(border);
        int side = Ui.dp(this, 240);
        FrameLayout.LayoutParams fp = new FrameLayout.LayoutParams(side, side);
        fp.gravity = Gravity.CENTER;
        root.addView(frame, fp);

        TextView bottom = Ui.text(this,
                "دریافت بانک یا به‌روزرسانی برنامه", Ui.T_SM, Ui.CAMERA_HINT);
        bottom.setGravity(Gravity.CENTER);
        bottom.setPadding(0, Ui.dp(this, Ui.S4), 0, Ui.dp(this, Ui.S4));
        bottom.setBackgroundColor(Ui.CAMERA_OVERLAY);
        root.addView(bottom, frameParams(Gravity.BOTTOM, -1, -2));
        return root;
    }

    private FrameLayout.LayoutParams frameParams(int gravity, int width, int height) {
        FrameLayout.LayoutParams p = new FrameLayout.LayoutParams(width, height);
        p.gravity = gravity;
        return p;
    }

    // --------------------------------------------------------------- camera

    private boolean hasPermission() {
        return Build.VERSION.SDK_INT < 23
                || checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != PERMISSION) {
            return;
        }
        boolean granted = grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (granted) {
            if (surfaceReady) {
                openCamera();
            }
        } else {
            new AlertDialog.Builder(this)
                    .setTitle("دسترسی دوربین لازم است")
                    .setMessage("برای اسکن QR، اجازهٔ دوربین را بده. از آدرس دستی هم می‌توانی بانک را بگیری.")
                    .setPositiveButton("باشه", new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface dialog, int which) {
                            finish();
                        }
                    })
                    .show();
        }
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        surfaceReady = true;
        if (hasPermission()) {
            openCamera();
        }
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        // The preview resizes itself; nothing to renegotiate on API 21+.
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        surfaceReady = false;
        releaseCamera();
    }

    @SuppressWarnings("deprecation")
    private void openCamera() {
        if (camera != null || finished) {
            return;
        }
        try {
            camera = Camera.open();
            if (camera == null) {
                showCameraProblem("دوربینی روی گوشی پیدا نشد");
                return;
            }
            Camera.Parameters params = camera.getParameters();
            Camera.Size best = pickSize(params.getSupportedPreviewSizes());
            if (best != null) {
                params.setPreviewSize(best.width, best.height);
            }
            try {
                params.setPreviewFormat(android.graphics.ImageFormat.NV21);
            } catch (RuntimeException ignored) {
                // Some ROMs only expose the default format; NV21 is the default.
            }
            List<String> focus = params.getSupportedFocusModes();
            if (focus != null && focus.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE)) {
                params.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            }
            try {
                camera.setParameters(params);
            } catch (RuntimeException rejected) {
                // Never leave the user without a preview because a parameter
                // is unsupported; retry with the camera's own defaults.
                camera.setParameters(camera.getParameters());
            }
            // Read back: the camera may have rounded the requested size.
            size = camera.getParameters().getPreviewSize();
            camera.setDisplayOrientation(displayDegrees());
            camera.setPreviewDisplay(surface.getHolder());
            camera.setPreviewCallback(this);
            camera.startPreview();
        } catch (Throwable e) {
            releaseCamera();
            showCameraProblem(e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
        }
    }

    private Camera.Size pickSize(List<Camera.Size> sizes) {
        if (sizes == null || sizes.isEmpty()) {
            return null;
        }
        Camera.Size best = null;
        long bestScore = Long.MAX_VALUE;
        for (Camera.Size s : sizes) {
            if (s.width < 640 || s.height < 480) {
                continue;
            }
            // Prefer something near 1280×720: enough detail for a QR, cheap
            // enough that decoding keeps up on an old phone.
            long pixels = (long) s.width * s.height;
            long score = Math.abs(pixels - 1280L * 720L);
            if (score < bestScore) {
                bestScore = score;
                best = s;
            }
        }
        return best != null ? best : sizes.get(0);
    }

    @SuppressWarnings("deprecation")
    private int displayDegrees() {
        try {
            Camera.CameraInfo info = new Camera.CameraInfo();
            Camera.getCameraInfo(0, info);
            int rotation = getWindowManager().getDefaultDisplay().getRotation();
            int degrees;
            switch (rotation) {
                case 1:
                    degrees = 90;
                    break;
                case 2:
                    degrees = 180;
                    break;
                case 3:
                    degrees = 270;
                    break;
                default:
                    degrees = 0;
            }
            return (info.orientation - degrees + 360) % 360;
        } catch (RuntimeException e) {
            return 90;
        }
    }

    @SuppressWarnings("deprecation")
    private void releaseCamera() {
        Camera current = camera;
        camera = null;
        if (current == null) {
            return;
        }
        try {
            current.setPreviewCallback(null);
            current.stopPreview();
        } catch (RuntimeException ignored) {
            // Camera already gone (user left the app, ROM reclaimed it).
        }
        try {
            current.release();
        } catch (RuntimeException ignored) {
            // Nothing left to release.
        }
    }

    // --------------------------------------------------------------- decode

    @Override
    public void onPreviewFrame(final byte[] data, Camera source) {
        if (finished || decoding || data == null || size == null) {
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastFrameAt < FRAME_INTERVAL_MS) {
            return;
        }
        lastFrameAt = now;
        decoding = true;
        // The camera hands the SAME array to every callback and keeps writing
        // the next frame into it. Decoding that array asynchronously read a
        // buffer being overwritten, so a code right in front of the lens never
        // decoded — copy the frame and decode the copy.
        final byte[] frame = data.clone();
        final int width = size.width;
        final int height = size.height;
        if (worker == null || worker.isShutdown()) {
            decoding = false;
            return;
        }
        worker.execute(new Runnable() {
            @Override
            public void run() {
                String text = null;
                try {
                    text = QrDecoder.decode(frame, width, height);
                } catch (Throwable ignored) {
                    // A bad frame is normal while the camera focuses.
                } finally {
                    decoding = false;
                }
                if (text != null && !text.trim().isEmpty()) {
                    finishWith(text.trim());
                    return;
                }
                hintAfterMiss();
            }
        });
    }

    /**
     * One nudge after a few seconds of misses: nothing, then useful advice.
     *
     * The frame size is printed with it: «nothing happens» on a real phone is
     * either no frames at all or frames too small to hold the modules, and the
     * screen is the only place that can be seen without a debugger.
     */
    private void hintAfterMiss() {
        int seen = ++misses;
        if (seen != 45 && seen != 220) {
            return;
        }
        final String text = seen == 45
                ? "کد پیدا نشد — کد را کامل داخل کادر بگیر و کمی نزدیک‌تر شو"
                : "هنوز کد خوانده نشد (فریم " + size.width + "×" + size.height
                  + ") — نور صفحه را زیاد کن یا آدرس را دستی وارد کن";
        ui.post(new Runnable() {
            @Override
            public void run() {
                if (!finished && hint != null) {
                    hint.setText(text);
                }
            }
        });
    }

    private void finishWith(final String text) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                if (finished) {
                    return;
                }
                finished = true;
                releaseCamera();
                setResult(RESULT_OK, new Intent().putExtra(EXTRA_RESULT, text));
                finish();
            }
        });
    }

    private void showCameraProblem(String detail) {
        if (finished) {
            return;
        }
        finished = true;
        releaseCamera();
        new AlertDialog.Builder(this)
                .setTitle("دوربین باز نشد")
                .setMessage(detail + "\n\nاز «از وای‌فای / USB» آدرس را دستی وارد کن.")
                .setPositiveButton("باشه", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        finish();
                    }
                })
                .show();
        Toast.makeText(this, "اسکنر در دسترس نیست", Toast.LENGTH_SHORT).show();
        if (hint != null) {
            hint.setText("دوربین در دسترس نیست");
        }
    }
}
