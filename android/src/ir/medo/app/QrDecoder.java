package ir.medo.app;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.Binarizer;
import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.MultiFormatReader;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.Result;
import com.google.zxing.common.GlobalHistogramBinarizer;
import com.google.zxing.common.HybridBinarizer;

import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;

/**
 * Decodes one camera frame. Pure zxing — no android.* — so the desktop JVM
 * tests (android/test.sh) can prove a generated QR is read back.
 *
 * The frame arrives as the camera's NV21 preview buffer: the first
 * {@code width * height} bytes are the Y (luminance) plane, which is all a QR
 * reader needs. The sensor is landscape while the phone is held portrait, so
 * the four orientations are tried instead of guessing the device rotation.
 */
public final class QrDecoder {

    private QrDecoder() {
    }

    /** @return the decoded text, or null when this frame holds no readable QR. */
    public static String decode(byte[] yuv, int width, int height) {
        if (yuv == null || width <= 0 || height <= 0
                || yuv.length < (long) width * height) {
            return null;
        }
        Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
        hints.put(DecodeHintType.POSSIBLE_FORMATS,
                Collections.singletonList(BarcodeFormat.QR_CODE));
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        // A code shown on a dark background (dark theme, phone-to-phone) is
        // inverted; without this hint zxing sees a solid block.
        hints.put(DecodeHintType.ALSO_INVERTED, Boolean.TRUE);

        MultiFormatReader reader = new MultiFormatReader();
        reader.setHints(hints);
        // Some ROMs hand out a padded buffer (each row rounded up), so the
        // luminance plane is wider than the frame: decoding it as `width`
        // pixels per row shears the code and nothing ever reads.
        int stride = strideOf(yuv.length, width, height);
        PlanarYUVLuminanceSource source =
                new PlanarYUVLuminanceSource(yuv, stride, height, 0, 0, width, height, false);
        try {
            for (int i = 0; i < 4; i++) {
                String text = read(reader, source);
                if (text != null) {
                    return text;
                }
                if (!source.isRotateSupported()) {
                    break;
                }
                source = (PlanarYUVLuminanceSource) source.rotateCounterClockwise();
            }
        } finally {
            reader.reset();
        }
        return null;
    }

    /** Bytes per row implied by the buffer length (NV21 = stride × height × 1.5). */
    static int strideOf(int length, int width, int height) {
        long tight = (long) width * height * 3 / 2;
        if (height <= 0 || length <= tight) {
            return width;
        }
        long guess = Math.round(length * 2.0 / 3.0 / height);
        return guess > width && guess * height <= length ? (int) guess : width;
    }

    /** Hybrid binarization first, then the global histogram for flat lighting. */
    private static String read(MultiFormatReader reader, PlanarYUVLuminanceSource source) {
        String text = read(reader, new HybridBinarizer(source));
        if (text == null) {
            text = read(reader, new GlobalHistogramBinarizer(source));
        }
        return text;
    }

    private static String read(MultiFormatReader reader, Binarizer binarizer) {
        try {
            Result result = reader.decodeWithState(new BinaryBitmap(binarizer));
            if (result != null && result.getText() != null && !result.getText().isEmpty()) {
                return result.getText();
            }
        } catch (Exception ignored) {
            // NotFound / Checksum / Format: this frame simply has no QR.
        }
        return null;
    }
}
