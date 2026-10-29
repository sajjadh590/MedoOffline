package ir.medo.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Design tokens + shared components (docs/design-contract.md).
 *
 * One 4dp spacing scale, one type scale, one semantic palette — everything the
 * two screens draw comes from here, so the app cannot drift into the generic
 * "cards on cards" look the reference UI agents warn about.
 */
public final class Ui {

    // ------------------------------------------------------------------ colour
    // These are the *active* tokens, replaced by applyTheme() before a screen
    // is built.  They are deliberately not final: the Settings screen changes
    // them and the activity is recreated, so every existing call site keeps
    // reading a central token instead of a hard-coded colour.
    public static int INK;
    public static int BG;
    public static int SURFACE;
    public static int SURFACE_2;
    public static int SURFACE_HI;       // older call sites
    public static int SURFACE_3;
    public static int LINE;
    public static int LINE_STRONG;

    public static int TEXT;
    public static int TEXT_2;
    public static int DIM;
    public static int TEXT_3;
    public static int MUTED;

    public static int ACCENT;
    public static int ACCENT_BG;
    public static int ACCENT_INK;

    public static int OK;
    public static int OK_BG;
    public static int BAD;
    public static int BAD_BG;
    public static int WARN;
    public static int WARN_BG;
    public static int PRESSED;
    public static int ACCENT_STROKE;

    // Camera screens stay dark whatever the reading theme: a black viewfinder
    // is functional, not a canvas for sepia.
    public static final int CAMERA_BG = 0xFF000000;
    public static final int CAMERA_OVERLAY = 0xCC000000;
    public static final int CAMERA_TEXT = 0xFFFFFFFF;
    public static final int CAMERA_HINT = 0xFFB9C4CF;
    public static final int CAMERA_ACCENT = 0xFF2DD4A7;

    public static final String PREFS = "medo";
    public static final String PREF_THEME = "theme";
    public static final String PREF_FONT_SCALE = "font_scale";
    public static final float[] FONT_SCALES = {0.9f, 1.0f, 1.15f, 1.3f};
    public static final String[] FONT_LABELS = {"کوچک", "متوسط", "بزرگ", "خیلی بزرگ"};

    private static String activeTheme = Theme.DARK;
    private static float activeScale = 1.0f;

    // ------------------------------------------------------------------ spacing
    public static final int S1 = 4;
    public static final int S2 = 8;
    public static final int S3 = 12;
    public static final int S4 = 16;
    public static final int S5 = 20;
    public static final int S6 = 24;
    public static final int S8 = 32;

    // -------------------------------------------------------------------- shape
    public static final float R_CONTROL = 10f;
    public static final float R_CARD = 14f;
    public static final float R_PANEL = 18f;

    // --------------------------------------------------------------------- type
    private static final float BASE_XS = 11f;
    private static final float BASE_SM = 12f;
    private static final float BASE_BODY = 13f;
    private static final float BASE_UI = 15f;
    private static final float BASE_STEM = 17f;
    private static final float BASE_TITLE = 22f;
    private static final float BASE_DISPLAY = 28f;

    public static float T_XS;
    public static float T_SM;
    public static float T_BODY;
    public static float T_UI;
    public static float T_STEM;
    public static float T_TITLE;
    public static float T_DISPLAY;

    public static final int TOUCH = 48;

    static {
        applyTheme(Theme.DARK, 1.0f);
    }

    private Ui() {
    }

    // ------------------------------------------------------------------- theme

    /** Apply the saved theme/font before building a screen.
     *
     * Returns true when the active values changed, so an activity can recreate
     * itself after the Settings screen instead of needing an app restart.
     */
    public static boolean apply(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS,
                Context.MODE_PRIVATE);
        String key = prefs.getString(PREF_THEME, Theme.DARK);
        float scale = prefs.getFloat(PREF_FONT_SCALE, 1.0f);
        if (key.equals(activeTheme) && Math.abs(scale - activeScale) < 0.001f) {
            return false;
        }
        applyTheme(key, scale);
        return true;
    }

    public static void applyTheme(String key, float scale) {
        Theme.Palette p = Theme.get(key);
        INK = p.INK;
        BG = p.INK;
        SURFACE = p.SURFACE;
        SURFACE_2 = p.SURFACE_2;
        SURFACE_HI = p.SURFACE_2;
        SURFACE_3 = p.SURFACE_3;
        LINE = p.LINE;
        LINE_STRONG = p.LINE_STRONG;
        TEXT = p.TEXT;
        TEXT_2 = p.TEXT_2;
        DIM = p.TEXT_2;
        TEXT_3 = p.TEXT_3;
        MUTED = p.TEXT_3;
        ACCENT = p.ACCENT;
        ACCENT_BG = p.ACCENT_BG;
        ACCENT_INK = p.ACCENT_INK;
        OK = p.OK;
        OK_BG = p.OK_BG;
        BAD = p.BAD;
        BAD_BG = p.BAD_BG;
        WARN = p.WARN;
        WARN_BG = p.WARN_BG;
        PRESSED = p.PRESSED;
        ACCENT_STROKE = p.ACCENT_STROKE;

        float s = Math.max(0.8f, Math.min(1.5f, scale));
        T_XS = BASE_XS * s;
        T_SM = BASE_SM * s;
        T_BODY = BASE_BODY * s;
        T_UI = BASE_UI * s;
        T_STEM = BASE_STEM * s;
        T_TITLE = BASE_TITLE * s;
        T_DISPLAY = BASE_DISPLAY * s;
        activeTheme = p.key;
        activeScale = s;
    }

    public static void setTheme(Context context, String key) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(PREF_THEME, Theme.get(key).key).apply();
    }

    public static void setFontScale(Context context, float scale) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putFloat(PREF_FONT_SCALE, scale).apply();
    }

    public static String themeKey() {
        return activeTheme;
    }

    public static float fontScale() {
        return activeScale;
    }

    public static boolean isDark() {
        return Theme.isDark(activeTheme);
    }

    // ------------------------------------------------------------------ metrics

    public static int dp(Context context, float value) {
        return (int) (value * context.getResources().getDisplayMetrics().density + 0.5f);
    }

    public static int sp(Context context, float value) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, value,
                context.getResources().getDisplayMetrics());
    }

    /** False when the user asked the system for less motion. */
    public static boolean motion(Context context) {
        try {
            float scale = Settings.Global.getFloat(context.getContentResolver(),
                    Settings.Global.ANIMATOR_DURATION_SCALE, 1f);
            return scale > 0.01f;
        } catch (RuntimeException e) {
            return true;
        }
    }

    public static long ms(Context context, long value) {
        return motion(context) ? value : 0L;
    }

    public static String fa(int number) {
        return fa(String.valueOf(number));
    }

    public static String fa(String asciiDigits) {
        if (asciiDigits == null) {
            return "";
        }
        StringBuilder b = new StringBuilder(asciiDigits.length());
        for (int i = 0; i < asciiDigits.length(); i++) {
            char c = asciiDigits.charAt(i);
            b.append(c >= '0' && c <= '9' ? (char) ('\u06F0' + (c - '0')) : c);
        }
        return b.toString();
    }

    // ---------------------------------------------------------------- drawables

    public static GradientDrawable rounded(Context c, int color, float radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setShape(GradientDrawable.RECTANGLE);
        d.setColor(color);
        d.setCornerRadius(dp(c, radiusDp));
        return d;
    }

    public static GradientDrawable outlined(Context c, int color, float radiusDp,
                                            int strokeColor, float strokeDp) {
        GradientDrawable d = rounded(c, color, radiusDp);
        d.setStroke(Math.max(1, dp(c, strokeDp)), strokeColor);
        return d;
    }

    /** Filled circle with a hairline, used by option markers and chevrons. */
    public static GradientDrawable circle(Context c, int color, int strokeColor, float strokeDp) {
        GradientDrawable d = new GradientDrawable();
        d.setShape(GradientDrawable.OVAL);
        d.setColor(color);
        if (strokeDp > 0) {
            d.setStroke(Math.max(1, dp(c, strokeDp)), strokeColor);
        }
        return d;
    }

    // ------------------------------------------------------------------ views

    public static TextView text(Context c, CharSequence value, float sizeSp, int color) {
        TextView t = new TextView(c);
        t.setText(value);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp);
        t.setTextColor(color);
        t.setLineSpacing(dp(c, 3), 1f);
        t.setTextDirection(View.TEXT_DIRECTION_RTL);
        t.setIncludeFontPadding(false);
        return t;
    }

    public static TextView bold(Context c, CharSequence value, float sizeSp, int color) {
        TextView t = text(c, value, sizeSp, color);
        t.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
        return t;
    }

    public static TextView medium(Context c, CharSequence value, float sizeSp, int color) {
        TextView t = text(c, value, sizeSp, color);
        t.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.NORMAL));
        t.setLetterSpacing(0.01f);
        return t;
    }

    /** Small all-caps-ish label above a group ("دسته‌بندی", "پاسخ تشریحی"). */
    public static TextView label(Context c, CharSequence value, int color) {
        TextView t = medium(c, value, T_XS, color);
        t.setLetterSpacing(0.06f);
        return t;
    }

    public static TextView pill(Context c, CharSequence value, int bg, int fg, float sizeSp) {
        TextView t = text(c, value, sizeSp, fg);
        t.setBackground(rounded(c, bg, 10));
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(c, 10), dp(c, 5), dp(c, 10), dp(c, 5));
        t.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
        return t;
    }

    public static TextView chip(Context c, CharSequence value, int bg, int fg) {
        TextView t = text(c, value, T_BODY, fg);
        t.setBackground(rounded(c, bg, R_CONTROL));
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(c, 14), dp(c, 10), dp(c, 14), dp(c, 10));
        t.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
        t.setMinHeight(dp(c, 44));
        return t;
    }

    /** Square icon/text button that always meets the 48dp touch target. */
    public static TextView iconButton(Context c, CharSequence glyph, float sizeSp, int color) {
        TextView t = text(c, glyph, sizeSp, color);
        t.setGravity(Gravity.CENTER);
        t.setLayoutParams(lp(dp(c, TOUCH), dp(c, TOUCH)));
        t.setBackground(rounded(c, 0x00000000, 12));
        t.setClickable(true);
        t.setFocusable(true);
        return t;
    }

    public static LinearLayout row(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        l.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        return l;
    }

    public static LinearLayout column(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        return l;
    }

    public static LinearLayout card(Context c) {
        LinearLayout l = column(c);
        l.setBackground(outlined(c, SURFACE, R_CARD, LINE, 1));
        l.setPadding(dp(c, S4), dp(c, S3), dp(c, S4), dp(c, S3));
        return l;
    }

    public static LinearLayout.LayoutParams lp(int width, int height) {
        return new LinearLayout.LayoutParams(width, height);
    }

    public static LinearLayout.LayoutParams margins(Context c, int width, int height,
                                                    int left, int top, int right, int bottom) {
        LinearLayout.LayoutParams p = lp(width, height);
        p.setMargins(dp(c, left), dp(c, top), dp(c, right), dp(c, bottom));
        return p;
    }

    public static LinearLayout.LayoutParams wrap() {
        return lp(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    public static LinearLayout.LayoutParams matchWrap() {
        return lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    public static LinearLayout.LayoutParams weight(float w) {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, w);
    }

    /** Fills the remaining space in a vertical LinearLayout (list, scroller). */
    public static LinearLayout.LayoutParams fillWeight() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
    }

    public static View divider(Context c) {
        View v = new View(c);
        v.setBackgroundColor(LINE);
        v.setLayoutParams(lp(ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(c, 1))));
        return v;
    }

    public static View spacer(Context c, float heightDp) {
        View v = new View(c);
        v.setLayoutParams(lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(c, heightDp)));
        return v;
    }

    public static View gap(Context c, float widthDp) {
        View v = new View(c);
        v.setLayoutParams(lp(dp(c, widthDp), 1));
        return v;
    }

    public static void describe(View view, String text) {
        view.setContentDescription(text);
    }
}
