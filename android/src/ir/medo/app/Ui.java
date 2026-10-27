package ir.medo.app;

import android.content.Context;
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
    public static final int INK = 0xFF0B0F14;
    public static final int BG = INK;
    public static final int SURFACE = 0xFF141A21;
    public static final int SURFACE_2 = 0xFF1B232C;
    public static final int SURFACE_HI = SURFACE_2;       // older call sites
    public static final int SURFACE_3 = 0xFF232C36;
    public static final int LINE = 0xFF26313C;
    public static final int LINE_STRONG = 0xFF35414E;

    public static final int TEXT = 0xFFEDF2F7;
    public static final int TEXT_2 = 0xFFA7B4C2;
    public static final int DIM = TEXT_2;
    public static final int TEXT_3 = 0xFF7C8B9A;
    public static final int MUTED = TEXT_3;

    public static final int ACCENT = 0xFF2DD4A7;
    public static final int ACCENT_BG = 0xFF0C2A24;
    public static final int ACCENT_INK = 0xFF07130F;

    public static final int OK = 0xFF46C46A;
    public static final int OK_BG = 0xFF10281A;
    public static final int BAD = 0xFFFF6B62;
    public static final int BAD_BG = 0xFF2C1618;
    public static final int WARN = 0xFFE0AE3C;
    public static final int WARN_BG = 0xFF2A2113;

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
    public static final float T_XS = 11f;
    public static final float T_SM = 12f;
    public static final float T_BODY = 13f;
    public static final float T_UI = 15f;
    public static final float T_STEM = 17f;
    public static final float T_TITLE = 22f;
    public static final float T_DISPLAY = 28f;

    public static final int TOUCH = 48;

    private Ui() {
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
