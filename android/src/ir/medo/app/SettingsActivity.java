package ir.medo.app;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * Appearance settings: five reading themes and four text sizes.
 *
 * Every colour comes from Theme through Ui; this screen has no palette of its
 * own and no hard-coded colour.  A choice is saved first, then the activity is
 * recreated (the main screen also re-applies on resume), so the change is
 * visible immediately without restarting the app.
 */
public final class SettingsActivity extends Activity {

    private static final String PREFS = "medo";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Ui.apply(this);
        setContentView(build());
    }

    private View build() {
        LinearLayout root = Ui.column(this);
        root.setBackgroundColor(Ui.INK);
        root.setFitsSystemWindows(true);
        root.addView(buildTopBar());

        ScrollView scroller = new ScrollView(this);
        scroller.setFillViewport(true);
        LinearLayout body = Ui.column(this);
        int pad = Ui.dp(this, Ui.S5);
        body.setPadding(pad, Ui.dp(this, Ui.S3), pad, Ui.dp(this, Ui.S6));

        body.addView(sectionTitle("ظاهر برنامه"));
        body.addView(hint("رنگ‌ها از یک مجموعهٔ مرکزی می‌آیند؛ همهٔ صفحه‌ها، "
                + "گزینه‌ها، پاسخ درست/غلط و درس‌نامه با همین تم خوانده می‌شوند."));
        body.addView(Ui.spacer(this, Ui.S2));
        for (int i = 0; i < Theme.KEYS.length; i++) {
            body.addView(themeRow(Theme.KEYS[i], Theme.LABELS[i]));
        }

        body.addView(Ui.spacer(this, Ui.S6));
        body.addView(sectionTitle("اندازهٔ متن"));
        body.addView(hint("اندازهٔ متن سوال، گزینه‌ها و درس‌نامه. پیش‌فرض «متوسط» است."));
        body.addView(Ui.spacer(this, Ui.S2));
        body.addView(fontRow());

        TextView sample = Ui.text(this,
                "نمونهٔ متن: بیمار با تب و درد پهلو مراجعه کرده است. پاسخ تشریحی "
                        + "و نکات کلیدی با همین اندازه نمایش داده می‌شوند.",
                Ui.T_BODY, Ui.TEXT);
        sample.setBackground(Ui.outlined(this, Ui.SURFACE, Ui.R_CARD, Ui.LINE, 1));
        sample.setPadding(Ui.dp(this, Ui.S4), Ui.dp(this, Ui.S3),
                Ui.dp(this, Ui.S4), Ui.dp(this, Ui.S3));
        sample.setLineSpacing(Ui.dp(this, 4), 1f);
        body.addView(sample, Ui.margins(this, ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, 0, Ui.S3, 0, 0));

        body.addView(Ui.spacer(this, Ui.S6));
        TextView note = Ui.text(this,
                "بانک سوال و درس‌نامه‌ها آفلاین روی گوشی می‌مانند؛ تغییر تم "
                        + "هیچ دانلودی لازم ندارد.",
                Ui.T_SM, Ui.TEXT_3);
        body.addView(note);

        scroller.addView(body);
        root.addView(scroller, Ui.fillWeight());
        return root;
    }

    private View buildTopBar() {
        LinearLayout bar = Ui.row(this);
        int pad = Ui.dp(this, Ui.S4);
        bar.setPadding(pad, Ui.dp(this, Ui.S3), pad, Ui.dp(this, Ui.S2));
        TextView back = Ui.iconButton(this, "\u2039", 26, Ui.TEXT);
        Ui.describe(back, "بازگشت");
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
        bar.addView(back);
        LinearLayout titles = Ui.column(this);
        titles.setPadding(Ui.dp(this, Ui.S2), 0, 0, 0);
        titles.addView(Ui.bold(this, "تنظیمات", Ui.T_TITLE, Ui.TEXT));
        titles.addView(Ui.text(this, "ظاهر و خوانایی", Ui.T_SM, Ui.TEXT_3));
        bar.addView(titles, Ui.weight(1f));
        return bar;
    }

    private TextView sectionTitle(String value) {
        TextView title = Ui.bold(this, value, Ui.T_UI, Ui.TEXT);
        title.setPadding(0, 0, 0, Ui.dp(this, Ui.S1));
        return title;
    }

    private TextView hint(String value) {
        TextView view = Ui.text(this, value, Ui.T_SM, Ui.TEXT_3);
        view.setLineSpacing(Ui.dp(this, 3), 1f);
        return view;
    }

    private View themeRow(final String key, String label) {
        Theme.Palette palette = Theme.get(key);
        boolean active = key.equals(Ui.themeKey());
        LinearLayout row = Ui.row(this);
        row.setBackground(active
                ? Ui.outlined(this, Ui.ACCENT_BG, Ui.R_CARD, Ui.ACCENT, 1.5f)
                : Ui.outlined(this, Ui.SURFACE, Ui.R_CARD, Ui.LINE, 1));
        row.setPadding(Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S3),
                Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S3));
        row.setClickable(true);
        row.setFocusable(true);
        row.setMinimumHeight(Ui.dp(this, 56));
        Ui.describe(row, label + (active ? "، انتخاب‌شده" : ""));

        LinearLayout swatch = Ui.row(this);
        swatch.setBackground(Ui.rounded(this, palette.INK, 9));
        swatch.setPadding(Ui.dp(this, 4), Ui.dp(this, 4),
                Ui.dp(this, 4), Ui.dp(this, 4));
        for (int color : new int[]{palette.ACCENT, palette.TEXT_2, palette.SURFACE_2}) {
            View chip = new View(this);
            chip.setBackground(Ui.rounded(this, color, 4));
            LinearLayout.LayoutParams cp = Ui.lp(Ui.dp(this, 9), Ui.dp(this, 9));
            cp.setMargins(0, 0, Ui.dp(this, 3), 0);
            swatch.addView(chip, cp);
        }
        row.addView(swatch);

        TextView name = Ui.bold(this, label, Ui.T_BODY,
                active ? Ui.ACCENT : Ui.TEXT);
        name.setPadding(Ui.dp(this, Ui.S3), 0, 0, 0);
        row.addView(name, Ui.weight(1f));
        if (active) {
            TextView check = Ui.bold(this, "\u2713", Ui.T_UI, Ui.ACCENT);
            check.setGravity(Gravity.CENTER);
            row.addView(check);
        } else {
            row.addView(new View(this), Ui.lp(Ui.dp(this, 16), Ui.dp(this, 16)));
        }

        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Ui.setTheme(SettingsActivity.this, key);
                Ui.apply(SettingsActivity.this);
                recreate();
            }
        });
        LinearLayout.LayoutParams params = Ui.matchWrap();
        params.setMargins(0, 0, 0, Ui.dp(this, Ui.S2));
        row.setLayoutParams(params);
        return row;
    }

    private View fontRow() {
        LinearLayout row = Ui.row(this);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        float current = prefs.getFloat(Ui.PREF_FONT_SCALE, 1.0f);
        for (int i = 0; i < Ui.FONT_SCALES.length; i++) {
            final float scale = Ui.FONT_SCALES[i];
            boolean active = Math.abs(scale - current) < 0.001f;
            TextView chip = Ui.chip(this, Ui.FONT_LABELS[i],
                    active ? Ui.ACCENT : Ui.SURFACE_2,
                    active ? Ui.ACCENT_INK : Ui.TEXT_2);
            Ui.describe(chip, Ui.FONT_LABELS[i] + (active ? "، انتخاب‌شده" : ""));
            chip.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    Ui.setFontScale(SettingsActivity.this, scale);
                    Ui.apply(SettingsActivity.this);
                    recreate();
                }
            });
            LinearLayout.LayoutParams params = Ui.weight(1f);
            params.setMargins(0, 0, i < Ui.FONT_SCALES.length - 1 ? Ui.dp(this, Ui.S1) : 0, 0);
            row.addView(chip, params);
        }
        return row;
    }
}
