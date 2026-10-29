package ir.medo.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.Html;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

/** One quiz session: instant feedback, the explanation, and the PDF button. */
public final class QuizActivity extends Activity {

    public static final String EXTRA_NODE = "node";
    public static final String EXTRA_IDS = "ids";
    public static final String EXTRA_TITLE = "title";
    public static final String EXTRA_START_INDEX = "start_index";

    private StateStore state;
    private Bank bank;
    private Quiz quiz;
    private String title = "مدو";

    private TextView meta;
    private TextView stem;
    private LinearLayout optionsBox;
    private TextView feedback;
    private LinearLayout explanationBox;
    private TextView explanationBody;
    private TextView star;
    private TextView nextButton;
    private TextView prevButton;
    private TextView clearButton;
    private TextView progressText;
    private ProgressBar bar;
    private ScrollView scroller;

    private int lastQuestionId = -1;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Ui.apply(this);
        state = new StateStore(new File(getFilesDir(), "state.json"));
        bank = Bank.cached();
        if (bank == null) {
            try {
                bank = BankStore.read(this);
            } catch (Exception ignored) {
                bank = null;
            }
        }

        List<Question> items = resolveQuestions();
        if (items.isEmpty()) {
            Toast.makeText(this, "سوالی برای نمایش نیست", Toast.LENGTH_LONG).show();
            finish();
            return;
        }
        String requested = getIntent().getStringExtra(EXTRA_TITLE);
        if (!TextUtils.isEmpty(requested)) {
            title = requested;
        }
        quiz = new Quiz(items, state, bank);
        // A tap in the question list must land on that row, not restart the
        // section at question 1.
        quiz.goTo(getIntent().getIntExtra(EXTRA_START_INDEX, 0));
        setContentView(build());
        render();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Theme/size changed in Settings: rebuild the views in place so the
        // reader stays on the same question instead of returning to the start.
        if (quiz != null && Ui.apply(this)) {
            setContentView(build());
            render();
        }
    }

    private List<Question> resolveQuestions() {
        List<Question> items = new ArrayList<>();
        if (bank == null) {
            return items;
        }
        int[] ids = getIntent().getIntArrayExtra(EXTRA_IDS);
        if (ids != null) {
            for (int id : ids) {
                // Light entries only: Quiz.current() hydrates the one on screen.
                Question q = bank.light(id);
                if (q != null) {
                    items.add(q);
                }
            }
            return items;
        }
        Node node = bank.node(getIntent().getStringExtra(EXTRA_NODE));
        return node == null ? bank.answerable() : bank.forNode(node);
    }

    // ------------------------------------------------------------------- UI

    private View build() {
        LinearLayout root = Ui.column(this);
        root.setBackgroundColor(Ui.INK);
        root.setFitsSystemWindows(true);
        root.addView(buildTopBar());
        root.addView(buildProgress());

        scroller = new ScrollView(this);
        scroller.setFillViewport(true);
        scroller.setClipToPadding(false);
        scroller.addView(buildContent());
        root.addView(scroller, Ui.fillWeight());

        root.addView(buildFooter());
        return root;
    }

    private View buildTopBar() {
        LinearLayout top = Ui.row(this);
        top.setPadding(Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S3), 0);

        TextView back = Ui.iconButton(this, "\u2039", 30, Ui.TEXT);
        Ui.describe(back, "بازگشت");
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
        top.addView(back);

        TextView heading = Ui.bold(this, title, Ui.T_UI, Ui.TEXT);
        heading.setSingleLine(true);
        heading.setEllipsize(TextUtils.TruncateAt.END);
        heading.setPadding(Ui.dp(this, Ui.S1), 0, Ui.dp(this, Ui.S1), 0);
        top.addView(heading, Ui.weight(1f));

        star = Ui.iconButton(this, "\u2606", 20, Ui.TEXT_3);
        star.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                quiz.toggleMark();
                renderStar();
                v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);
            }
        });
        top.addView(star);

        TextView pdf = Ui.chip(this, "PDF", Ui.SURFACE_2, Ui.ACCENT);
        pdf.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, Ui.T_SM);
        pdf.setPadding(Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S2));
        pdf.setMinHeight(Ui.dp(this, 40));
        Ui.describe(pdf, "خروجی PDF این بخش");
        pdf.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                PdfExporter.export(QuizActivity.this, title,
                        new java.util.ArrayList<Question>(quiz.items()), state, bank);
            }
        });
        top.addView(pdf);
        return top;
    }

    private View buildProgress() {
        LinearLayout box = Ui.column(this);
        box.setPadding(Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S5), 0);

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(1000);
        bar.setProgressDrawable(Ui.rounded(this, Ui.ACCENT, 2));
        bar.setBackground(Ui.rounded(this, Ui.SURFACE_2, 2));
        box.addView(bar, Ui.lp(ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 3)));

        progressText = Ui.text(this, "", Ui.T_XS, Ui.TEXT_3);
        progressText.setPadding(0, Ui.dp(this, Ui.S1), 0, 0);
        box.addView(progressText);
        return box;
    }

    private View buildContent() {
        LinearLayout content = Ui.column(this);
        content.setPadding(Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S3),
                Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S5));

        meta = Ui.text(this, "", Ui.T_XS, Ui.TEXT_3);
        content.addView(meta);

        // The stem gets its own surface: it is the one thing read carefully.
        LinearLayout stemCard = Ui.card(this);
        stemCard.setBackground(Ui.outlined(this, Ui.SURFACE, Ui.R_PANEL, Ui.LINE, 1));
        LinearLayout.LayoutParams stemParams = Ui.matchWrap();
        stemParams.setMargins(0, Ui.dp(this, Ui.S2), 0, Ui.dp(this, Ui.S4));
        stemCard.setLayoutParams(stemParams);

        stem = Ui.text(this, "", Ui.T_STEM, Ui.TEXT);
        stem.setLineSpacing(Ui.dp(this, 6), 1f);
        stemCard.addView(stem);
        content.addView(stemCard);

        content.addView(Ui.label(this,
                "\u06AF\u0632\u06CC\u0646\u0647\u200C\u0647\u0627", Ui.TEXT_3));
        content.addView(Ui.spacer(this, Ui.S2));

        optionsBox = Ui.column(this);
        content.addView(optionsBox);

        feedback = Ui.text(this, "", Ui.T_BODY, Ui.TEXT);
        feedback.setPadding(Ui.dp(this, Ui.S1), Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S1), 0);
        feedback.setVisibility(View.GONE);
        content.addView(feedback);

        clearButton = Ui.text(this, "\u067E\u0627\u0633\u062E \u0631\u0627 \u067E\u0627\u06A9 \u06A9\u0646",
                Ui.T_SM, Ui.TEXT_3);
        clearButton.setPadding(Ui.dp(this, Ui.S1), Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S1), 0);
        clearButton.setVisibility(View.GONE);
        clearButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                quiz.clear();
                render();
            }
        });
        content.addView(clearButton);

        explanationBox = Ui.row(this);
        explanationBox.setBackground(Ui.outlined(this, Ui.SURFACE, Ui.R_CARD, Ui.LINE, 1));
        explanationBox.setPadding(0, Ui.dp(this, Ui.S3), 0, Ui.dp(this, Ui.S3));
        LinearLayout.LayoutParams explParams = Ui.matchWrap();
        explParams.setMargins(0, Ui.dp(this, Ui.S4), 0, 0);
        explanationBox.setLayoutParams(explParams);

        View accentBar = new View(this);
        accentBar.setBackgroundColor(Ui.ACCENT);
        explanationBox.addView(accentBar, Ui.lp(Ui.dp(this, 3), ViewGroup.LayoutParams.MATCH_PARENT));

        LinearLayout explTexts = Ui.column(this);
        explTexts.setPadding(Ui.dp(this, Ui.S4), 0, Ui.dp(this, Ui.S4), 0);
        explTexts.addView(Ui.label(this, "\u067E\u0627\u0633\u062E \u062A\u0634\u0631\u06CC\u062D\u06CC", Ui.ACCENT));
        explanationBody = Ui.text(this, "", Ui.T_BODY, Ui.TEXT);
        explanationBody.setPadding(0, Ui.dp(this, Ui.S2), 0, 0);
        explanationBody.setLineSpacing(Ui.dp(this, 5), 1f);
        explTexts.addView(explanationBody);
        explanationBox.addView(explTexts, Ui.weight(1f));

        explanationBox.setVisibility(View.GONE);
        content.addView(explanationBox);
        return content;
    }

    private View buildFooter() {
        LinearLayout footer = Ui.row(this);
        footer.setBackgroundColor(Ui.INK);
        footer.setPadding(Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S4));

        prevButton = Ui.chip(this, "قبلی", Ui.SURFACE_2, Ui.TEXT);
        prevButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (quiz.prev()) {
                    render();
                }
            }
        });
        nextButton = Ui.chip(this, "بعدی", Ui.ACCENT, Ui.ACCENT_INK);
        nextButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                next();
            }
        });
        footer.addView(prevButton, Ui.weight(1f));
        footer.addView(Ui.gap(this, Ui.S2), Ui.lp(Ui.dp(this, Ui.S2), 1));
        footer.addView(nextButton, Ui.weight(1.3f));
        return footer;
    }

    // ---------------------------------------------------------------- render

    private void render() {
        Question q = quiz.current();
        boolean changed = q.id != lastQuestionId;
        lastQuestionId = q.id;

        meta.setText(Ui.fa(q.meta(quiz.position(), quiz.size()))
                + (TextUtils.isEmpty(q.path) ? "" : "  ·  " + q.path));
        stem.setText(html(q.stemHtml, q.stemText));

        renderOptions(q);
        renderFeedback(q);
        renderStar();

        int answered = quiz.answeredCount();
        bar.setProgress((int) (1000.0 * answered / quiz.size()));
        progressText.setText("پاسخ‌داده: " + Ui.fa(answered) + " از " + Ui.fa(quiz.size())
                + "  ·  درست: " + Ui.fa(quiz.correctCount()));
        prevButton.setAlpha(quiz.position() == 1 ? 0.4f : 1f);
        prevButton.setEnabled(quiz.position() != 1);
        nextButton.setText(quiz.hasNext() ? "بعدی" : "پایان و نتیجه");

        if (changed) {
            scroller.post(new Runnable() {
                @Override
                public void run() {
                    scroller.scrollTo(0, 0);
                }
            });
        }
    }

    private void renderOptions(Question q) {
        optionsBox.removeAllViews();
        final int picked = quiz.selected();
        boolean answered = picked > 0;
        int delay = 0;
        for (final Question.Option option : q.options) {
            boolean isPicked = picked == option.index;
            boolean isCorrect = q.hasAnswer() && q.correct == option.index;

            LinearLayout row = Ui.row(this);
            int bg = Ui.SURFACE;
            int border = Ui.LINE;
            // A question the archive cannot grade must not paint the pick red:
            // «wrong» would be a lie when no answer was ever stored.
            if (answered && isCorrect) {
                bg = Ui.OK_BG;
                border = Ui.OK;
            } else if (answered && isPicked && q.hasAnswer()) {
                bg = Ui.BAD_BG;
                border = Ui.BAD;
            } else if (answered && isPicked) {
                bg = Ui.ACCENT_BG;
                border = Ui.ACCENT;
            }
            row.setBackground(Ui.outlined(this, bg, Ui.R_CARD, border,
                    answered && (isPicked || isCorrect) ? 1.5f : 1f));
            row.setMinimumHeight(Ui.dp(this, 52));
            row.setPadding(Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S3),
                    Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S3));
            row.setClickable(true);
            row.setFocusable(true);
            row.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    if (quiz.answered()) {
                        return;
                    }
                    v.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                    quiz.select(option.index);
                    render();
                }
            });
            Ui.describe(row, "گزینهٔ " + Ui.fa(option.index) + ": " + option.text);

            LinearLayout.LayoutParams rowParams = Ui.matchWrap();
            rowParams.setMargins(0, 0, 0, Ui.dp(this, Ui.S2));
            row.setLayoutParams(rowParams);

            TextView marker = Ui.text(this, Ui.fa(option.index), Ui.T_SM,
                    answered && (isCorrect || isPicked) ? Ui.INK : Ui.TEXT_2);
            marker.setGravity(Gravity.CENTER);
            marker.setTypeface(Typeface.DEFAULT_BOLD);
            marker.setBackground(Ui.circle(this,
                    answered && isCorrect ? Ui.OK
                            : (answered && isPicked && q.hasAnswer() ? Ui.BAD
                            : (answered && isPicked ? Ui.ACCENT : Ui.SURFACE_3)),
                    0, 0));
            marker.setLayoutParams(Ui.lp(Ui.dp(this, 26), Ui.dp(this, 26)));
            row.addView(marker);

            TextView label = Ui.text(this, option.text, Ui.T_UI, Ui.TEXT);
            label.setPadding(Ui.dp(this, Ui.S3), 0, 0, 0);
            label.setLineSpacing(Ui.dp(this, 3), 1f);
            row.addView(label, Ui.weight(1f));

            // Site poll share, like the desktop option row. Absent for old
            // questions, so the column simply disappears.
            Integer share = q.votes.get(option.index);
            if (share != null) {
                TextView vote = Ui.text(this, Ui.fa(share) + "٪", Ui.T_XS, Ui.TEXT_3);
                vote.setPadding(Ui.dp(this, Ui.S2), 0, 0, 0);
                vote.setGravity(Gravity.END);
                row.addView(vote);
            }

            optionsBox.addView(row);

            if (Ui.motion(this)) {
                row.setAlpha(answered ? 1f : 0f);
                row.setTranslationY(Ui.dp(this, 6));
                row.animate()
                        .alpha(1f)
                        .translationY(0f)
                        .setStartDelay(delay)
                        .setDuration(180)
                        .start();
                delay += 25;
            }
        }
    }

    private void renderFeedback(Question q) {
        int picked = quiz.selected();
        if (picked == 0) {
            feedback.setVisibility(View.GONE);
            clearButton.setVisibility(View.GONE);
            explanationBox.setVisibility(View.GONE);
            return;
        }
        clearButton.setVisibility(View.VISIBLE);
        feedback.setVisibility(View.VISIBLE);
        if (!q.hasAnswer()) {
            String hint = "";
            Integer best = q.bestVoteOption();
            if (best != null) {
                hint = "\nبیشترین رأی کاربران سایت: گزینهٔ " + Ui.fa(best)
                        + " (" + Ui.fa(q.votes.get(best)) + "٪)";
            }
            feedback.setText("پاسخ صحیح این سوال در بانک آفلاین ثبت نشده است.\n"
                    + "انتخاب تو غلط شمرده نمی‌شود و در دقت حساب نمی‌آید." + hint);
            feedback.setTextColor(Ui.WARN);
        } else if (picked == q.correct) {
            feedback.setText("\u2713  درست");
            feedback.setTextColor(Ui.OK);
        } else {
            feedback.setText("\u2717  نادرست — گزینهٔ صحیح: " + Ui.fa(q.correct));
            feedback.setTextColor(Ui.BAD);
        }

        boolean show = q.hasExplanation();
        explanationBox.setVisibility(show ? View.VISIBLE : View.GONE);
        if (show) {
            explanationBody.setText(html(q.explanationHtml, q.explanationText));
            if (Ui.motion(this)) {
                explanationBox.setAlpha(0f);
                explanationBox.animate().alpha(1f).setDuration(220).start();
            }
        }
    }

    private void renderStar() {
        boolean marked = quiz.marked();
        star.setText(marked ? "\u2605" : "\u2606");
        star.setTextColor(marked ? Ui.WARN : Ui.TEXT_3);
        Ui.describe(star, marked ? "حذف از ستاره‌دارها" : "ستاره‌دار کردن سوال");
    }

    private void showSummary() {
        int total = quiz.size();
        int answered = quiz.answeredCount();
        int right = quiz.correctCount();
        int gradable = quiz.answeredGradable();
        String message = "درست: " + Ui.fa(right) + " از " + Ui.fa(total)
                + "\nپاسخ‌داده: " + Ui.fa(answered) + " از " + Ui.fa(total)
                + (gradable == 0 ? ""
                : "\nدقت: " + Ui.fa(Math.round(100f * right / gradable)) + "٪ از "
                + Ui.fa(gradable) + " سوال قابل تصحیح")
                + (answered > gradable
                ? "\n" + Ui.fa(answered - gradable) + " سوال بدون پاسخ در بانک بود."
                : "");
        new AlertDialog.Builder(this)
                .setTitle("نتیجهٔ این بخش")
                .setMessage(message)
                .setPositiveButton("مرور از اول", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        quiz.goFirst();
                        render();
                    }
                })
                .setNegativeButton("بستن", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        finish();
                    }
                })
                .show();
    }

    private void next() {
        if (quiz.hasNext()) {
            quiz.next();
            render();
            return;
        }
        showSummary();
    }

    private static CharSequence html(String html, String plain) {
        if (html == null || html.trim().isEmpty()) {
            return plain == null ? "" : plain;
        }
        try {
            @SuppressWarnings("deprecation")
            // LessonText keeps table cells apart and gives long tokens a
            // break opportunity, so a 30k-character lesson renders all of
            // itself inside the scroll view instead of one clipped block.
            CharSequence parsed = Html.fromHtml(LessonText.prepareHtml(html));
            return parsed == null || parsed.length() == 0 ? (plain == null ? "" : plain) : parsed;
        } catch (RuntimeException e) {
            return plain == null ? "" : plain;
        }
    }
}
