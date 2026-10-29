package ir.medo.app;

import java.util.ArrayList;
import java.util.List;

/**
 * The five reading themes and every semantic colour token they must define.
 *
 * This class deliberately has no android.* import: the JVM test suite checks
 * that every theme defines every token and that text keeps a WCAG-AA contrast
 * ratio (>= 4.5) on every surface it is used on.  Screens never hard-code a
 * colour; they read the tokens from Ui, which applies one of these palettes.
 */
public final class Theme {

    public static final String DARK = "dark";
    public static final String AMOLED = "amoled";
    public static final String LIGHT = "light";
    public static final String SEPIA = "sepia";
    public static final String BLUEGREY = "bluegrey";

    public static final String[] KEYS = {DARK, AMOLED, LIGHT, SEPIA, BLUEGREY};
    public static final String[] LABELS = {
            "تیره (پیش‌فرض)", "مشکی AMOLED", "روشن", "کاغذی (سپیا)", "آبی-خاکستری"
    };

    /** Every palette must set all of these; the test compares the field list. */
    public static final String[] TOKENS = {
            "INK", "SURFACE", "SURFACE_2", "SURFACE_3", "LINE", "LINE_STRONG",
            "TEXT", "TEXT_2", "TEXT_3", "ACCENT", "ACCENT_BG", "ACCENT_INK",
            "OK", "OK_BG", "BAD", "BAD_BG", "WARN", "WARN_BG",
            "PRESSED", "ACCENT_STROKE"
    };

    public static final class Palette {
        public final String key;
        public final int INK;
        public final int SURFACE;
        public final int SURFACE_2;
        public final int SURFACE_3;
        public final int LINE;
        public final int LINE_STRONG;
        public final int TEXT;
        public final int TEXT_2;
        public final int TEXT_3;
        public final int ACCENT;
        public final int ACCENT_BG;
        public final int ACCENT_INK;
        public final int OK;
        public final int OK_BG;
        public final int BAD;
        public final int BAD_BG;
        public final int WARN;
        public final int WARN_BG;
        public final int PRESSED;
        public final int ACCENT_STROKE;

        Palette(String key, int ink, int surface, int surface2, int surface3,
                int line, int lineStrong, int text, int text2, int text3,
                int accent, int accentBg, int accentInk, int ok, int okBg,
                int bad, int badBg, int warn, int warnBg,
                int pressed, int accentStroke) {
            this.key = key;
            this.INK = ink;
            this.SURFACE = surface;
            this.SURFACE_2 = surface2;
            this.SURFACE_3 = surface3;
            this.LINE = line;
            this.LINE_STRONG = lineStrong;
            this.TEXT = text;
            this.TEXT_2 = text2;
            this.TEXT_3 = text3;
            this.ACCENT = accent;
            this.ACCENT_BG = accentBg;
            this.ACCENT_INK = accentInk;
            this.OK = ok;
            this.OK_BG = okBg;
            this.BAD = bad;
            this.BAD_BG = badBg;
            this.WARN = warn;
            this.WARN_BG = warnBg;
            this.PRESSED = pressed;
            this.ACCENT_STROKE = accentStroke;
        }
    }

    private static final Palette DARK_PALETTE = new Palette(
            DARK, 0xFF0B0F14, 0xFF141A21, 0xFF1B232C, 0xFF232C36,
            0xFF26313C, 0xFF35414E, 0xFFEDF2F7, 0xFFA7B4C2, 0xFF7C8B9A,
            0xFF2DD4A7, 0xFF0C2A24, 0xFF07130F, 0xFF46C46A, 0xFF10281A,
            0xFFFF6B62, 0xFF2C1618, 0xFFE0AE3C, 0xFF2A2113,
            0xFF1F2933, 0x332DD4A7);

    private static final Palette AMOLED_PALETTE = new Palette(
            AMOLED, 0xFF000000, 0xFF080808, 0xFF101010, 0xFF181818,
            0xFF242424, 0xFF333333, 0xFFF5F5F5, 0xFFC0C0C0, 0xFF949494,
            0xFF2DD4A7, 0xFF062018, 0xFF000000, 0xFF5BD08A, 0xFF0A2314,
            0xFFFF7B72, 0xFF2A1012, 0xFFE8B84A, 0xFF241C0B,
            0xFF151515, 0x552DD4A7);

    private static final Palette LIGHT_PALETTE = new Palette(
            LIGHT, 0xFFF5F7FA, 0xFFFFFFFF, 0xFFEEF2F7, 0xFFE2E8F0,
            0xFFD7DEE7, 0xFFB9C4D0, 0xFF16202B, 0xFF46586A, 0xFF5D6F80,
            0xFF0F7A5A, 0xFFDDF4EC, 0xFFFFFFFF, 0xFF1E7A3C, 0xFFDFF5E5,
            0xFFB3261E, 0xFFFBE4E2, 0xFF8A5A00, 0xFFFFF1D6,
            0xFFE4EAF2, 0x330F7A5A);

    private static final Palette SEPIA_PALETTE = new Palette(
            SEPIA, 0xFFF4ECD8, 0xFFFBF4E4, 0xFFEFE3C8, 0xFFE5D5B5,
            0xFFD8C6A5, 0xFFBFA97F, 0xFF3B2F1E, 0xFF5C4A32, 0xFF6E5A40,
            0xFF1F6B4F, 0xFFE3E9D2, 0xFFFFF8E7, 0xFF2F6B2F, 0xFFDDEBD0,
            0xFF9B2C24, 0xFFF6DCD4, 0xFF7A4E00, 0xFFF7E5BF,
            0xFFEADDCA, 0x331F6B4F);

    private static final Palette BLUEGREY_PALETTE = new Palette(
            BLUEGREY, 0xFF1B2430, 0xFF232E3C, 0xFF2B3848, 0xFF354456,
            0xFF415166, 0xFF566A83, 0xFFE8EEF5, 0xFFB4C2D2, 0xFF97A9BD,
            0xFF58B6F0, 0xFF163247, 0xFF07131C, 0xFF5BD08A, 0xFF143326,
            0xFFFF8A80, 0xFF3A1B1E, 0xFFE8B84A, 0xFF33280F,
            0xFF2F3E50, 0x5558B6F0);

    private Theme() {
    }

    public static Palette get(String key) {
        if (AMOLED.equals(key)) {
            return AMOLED_PALETTE;
        }
        if (LIGHT.equals(key)) {
            return LIGHT_PALETTE;
        }
        if (SEPIA.equals(key)) {
            return SEPIA_PALETTE;
        }
        if (BLUEGREY.equals(key)) {
            return BLUEGREY_PALETTE;
        }
        return DARK_PALETTE;
    }

    public static boolean isDark(String key) {
        return !LIGHT.equals(key) && !SEPIA.equals(key);
    }

    public static int[] values(Palette p) {
        return new int[]{p.INK, p.SURFACE, p.SURFACE_2, p.SURFACE_3, p.LINE,
                p.LINE_STRONG, p.TEXT, p.TEXT_2, p.TEXT_3, p.ACCENT,
                p.ACCENT_BG, p.ACCENT_INK, p.OK, p.OK_BG, p.BAD, p.BAD_BG,
                p.WARN, p.WARN_BG, p.PRESSED, p.ACCENT_STROKE};
    }

    /** WCAG relative luminance of an ARGB colour (alpha is ignored). */
    public static double luminance(int color) {
        double r = channel((color >> 16) & 0xFF);
        double g = channel((color >> 8) & 0xFF);
        double b = channel(color & 0xFF);
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    private static double channel(int value) {
        double c = value / 255.0;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }

    public static double contrast(int foreground, int background) {
        double a = luminance(foreground);
        double b = luminance(background);
        double light = Math.max(a, b);
        double dark = Math.min(a, b);
        return (light + 0.05) / (dark + 0.05);
    }

    /** Pairs the app actually draws text on; every one must be >= 4.5. */
    public static List<String> contrastFailures(Palette p) {
        List<String> failures = new ArrayList<>();
        check(failures, p, "TEXT", p.TEXT, "INK", p.INK);
        check(failures, p, "TEXT", p.TEXT, "SURFACE", p.SURFACE);
        check(failures, p, "TEXT", p.TEXT, "SURFACE_2", p.SURFACE_2);
        check(failures, p, "TEXT_2", p.TEXT_2, "INK", p.INK);
        check(failures, p, "TEXT_2", p.TEXT_2, "SURFACE", p.SURFACE);
        check(failures, p, "TEXT_2", p.TEXT_2, "SURFACE_2", p.SURFACE_2);
        check(failures, p, "TEXT_3", p.TEXT_3, "INK", p.INK);
        check(failures, p, "TEXT_3", p.TEXT_3, "SURFACE", p.SURFACE);
        check(failures, p, "TEXT_3", p.TEXT_3, "SURFACE_2", p.SURFACE_2);
        check(failures, p, "ACCENT", p.ACCENT, "INK", p.INK);
        check(failures, p, "ACCENT", p.ACCENT, "SURFACE", p.SURFACE);
        check(failures, p, "ACCENT_INK", p.ACCENT_INK, "ACCENT", p.ACCENT);
        check(failures, p, "OK", p.OK, "OK_BG", p.OK_BG);
        check(failures, p, "BAD", p.BAD, "BAD_BG", p.BAD_BG);
        check(failures, p, "WARN", p.WARN, "WARN_BG", p.WARN_BG);
        return failures;
    }

    private static void check(List<String> failures, Palette p, String fgName,
                              int fg, String bgName, int bg) {
        double ratio = contrast(fg, bg);
        if (ratio < 4.5) {
            failures.add(p.key + ": " + fgName + " on " + bgName + " = "
                    + String.format(java.util.Locale.US, "%.2f", ratio));
        }
    }
}
