package ir.medo.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.Editable;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * The question list for one topic (or the whole archive).
 *
 * The home tree answers "where am I?"; this screen answers "what is inside?".
 * Rows carry the status the desktop app shows — answered/correct/wrong,
 * bookmarked — and a tap opens the quiz at exactly that question, so browsing
 * is not a dead end the way "tap a topic → start a 45-question exam" was.
 */
public final class BrowseActivity extends Activity {

    public static final String EXTRA_NODE = "node";
    public static final String EXTRA_TITLE = "title";

    private static final int FILTER_ALL = 0;
    private static final int FILTER_UNANSWERED = 1;
    private static final int FILTER_MARKED = 2;
    private static final int FILTER_WRONG = 3;

    private Bank bank;
    private StateStore state;
    private Node node;
    private String title = "همهٔ سوال‌ها";

    private final List<Question> base = new ArrayList<>();
    private final List<Question> rows = new ArrayList<>();

    private ListView list;
    private LinearLayout emptyBox;
    private TextView countLabel;
    private TextView quizButton;
    private EditText search;
    private final TextView[] chips = new TextView[4];

    private String query = "";
    private int filter = FILTER_ALL;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        state = new StateStore(new File(getFilesDir(), "state.json"));
        bank = Bank.cached();
        if (bank == null) {
            try {
                bank = BankStore.read(this);
            } catch (Exception ignored) {
                bank = null;
            }
        }
        if (bank == null) {
            Toast.makeText(this, "بانک خوانده نشد؛ دوباره از خانه دریافت کن", Toast.LENGTH_LONG).show();
            finish();
            return;
        }
        Bank.remember(bank);

        String url = getIntent().getStringExtra(EXTRA_NODE);
        node = url == null ? null : bank.node(url);
        String requested = getIntent().getStringExtra(EXTRA_TITLE);
        if (!TextUtils.isEmpty(requested)) {
            title = requested;
        } else if (node != null) {
            title = node.label();
        }

        base.addAll(node == null ? bank.answerable() : bank.forNode(node));
        setContentView(build());
        rebuild();
    }

    @Override
    protected void onResume() {
        super.onResume();
        state.load();
        rebuild();
    }

    // ------------------------------------------------------------------- UI

    private View build() {
        LinearLayout root = Ui.column(this);
        root.setBackgroundColor(Ui.INK);
        root.setFitsSystemWindows(true);
        root.addView(buildTopBar());
        root.addView(buildFilters());
        root.addView(buildList(), Ui.fillWeight());
        root.addView(buildFooter());
        return root;
    }

    private View buildTopBar() {
        LinearLayout top = Ui.row(this);
        top.setPadding(Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S1));

        TextView back = Ui.iconButton(this, "\u2039", 30, Ui.TEXT);
        Ui.describe(back, "بازگشت");
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
        top.addView(back);

        LinearLayout titles = Ui.column(this);
        titles.setPadding(Ui.dp(this, Ui.S1), 0, Ui.dp(this, Ui.S1), 0);
        TextView heading = Ui.bold(this, title, Ui.T_UI, Ui.TEXT);
        heading.setSingleLine(true);
        heading.setEllipsize(TextUtils.TruncateAt.END);
        titles.addView(heading);
        countLabel = Ui.text(this, "", Ui.T_XS, Ui.TEXT_3);
        countLabel.setPadding(0, Ui.dp(this, 1), 0, 0);
        titles.addView(countLabel);
        top.addView(titles, Ui.weight(1f));

        TextView pdf = Ui.chip(this, "PDF", Ui.SURFACE_2, Ui.ACCENT);
        pdf.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, Ui.T_SM);
        pdf.setPadding(Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S2));
        pdf.setMinHeight(Ui.dp(this, 40));
        Ui.describe(pdf, "خروجی PDF از فهرست فعلی");
        pdf.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (rows.isEmpty()) {
                    toast("سوالی در این فهرست نیست");
                    return;
                }
                PdfExporter.export(BrowseActivity.this, title, new ArrayList<>(rows), state);
            }
        });
        top.addView(pdf);
        return top;
    }

    private View buildFilters() {
        LinearLayout box = Ui.column(this);
        box.setPadding(Ui.dp(this, Ui.S4), 0, Ui.dp(this, Ui.S4), Ui.dp(this, Ui.S1));

        LinearLayout searchWrap = Ui.row(this);
        searchWrap.setBackground(Ui.rounded(this, Ui.SURFACE, Ui.R_CONTROL));
        searchWrap.setPadding(Ui.dp(this, Ui.S3), 0, Ui.dp(this, Ui.S3), 0);
        searchWrap.setMinimumHeight(Ui.dp(this, 44));

        TextView glyph = Ui.text(this, "\u2315", 16, Ui.TEXT_3);
        glyph.setPadding(0, 0, Ui.dp(this, Ui.S2), 0);
        searchWrap.addView(glyph);

        search = new EditText(this);
        search.setHint("جستجو در متن سوال‌ها…");
        search.setHintTextColor(Ui.TEXT_3);
        search.setTextColor(Ui.TEXT);
        search.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, Ui.T_BODY);
        search.setSingleLine(true);
        search.setBackgroundColor(Color.TRANSPARENT);
        search.setPadding(0, 0, 0, 0);
        search.setInputType(InputType.TYPE_CLASS_TEXT);
        search.setTextDirection(View.TEXT_DIRECTION_RTL);
        Ui.describe(search, "جستجو در سوال‌های این بخش");
        search.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {
                query = s == null ? "" : s.toString().trim();
                rebuild();
            }

            @Override
            public void afterTextChanged(Editable s) {
            }
        });
        searchWrap.addView(search, Ui.weight(1f));
        box.addView(searchWrap);

        HorizontalScrollView scroller = new HorizontalScrollView(this);
        scroller.setHorizontalScrollBarEnabled(false);
        LinearLayout chipsRow = Ui.row(this);
        chipsRow.setPadding(0, Ui.dp(this, Ui.S2), 0, 0);
        String[] labels = {"همه", "بی‌پاسخ", "ستاره‌دار", "غلط"};
        for (int i = 0; i < labels.length; i++) {
            final int index = i;
            TextView chip = Ui.chip(this, labels[i], Ui.SURFACE, Ui.TEXT_2);
            chip.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, Ui.T_SM);
            chip.setMinHeight(Ui.dp(this, 38));
            chip.setPadding(Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S1),
                    Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S1));
            chip.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    filter = index;
                    paintChips();
                    rebuild();
                }
            });
            chips[i] = chip;
            LinearLayout.LayoutParams p = Ui.wrap();
            p.setMargins(0, 0, Ui.dp(this, Ui.S2), 0);
            chipsRow.addView(chip, p);
        }
        scroller.addView(chipsRow);
        box.addView(scroller);
        paintChips();
        return box;
    }

    private void paintChips() {
        for (int i = 0; i < chips.length; i++) {
            boolean on = i == filter;
            chips[i].setBackground(Ui.rounded(this, on ? Ui.ACCENT : Ui.SURFACE,
                    Ui.R_CONTROL));
            chips[i].setTextColor(on ? Ui.ACCENT_INK : Ui.TEXT_2);
            chips[i].setTypeface(Typeface.create(Typeface.SANS_SERIF,
                    on ? Typeface.BOLD : Typeface.NORMAL));
        }
    }

    private View buildList() {
        list = new ListView(this);
        list.setDivider(null);
        list.setDividerHeight(0);
        list.setCacheColorHint(Color.TRANSPARENT);
        list.setBackgroundColor(Color.TRANSPARENT);
        list.setVerticalScrollBarEnabled(false);
        list.setAdapter(new RowAdapter());
        list.setOnItemClickListener(new android.widget.AdapterView.OnItemClickListener() {
            @Override
            public void onItemClick(android.widget.AdapterView<?> parent, View view,
                                    int position, long id) {
                startQuiz(position);
            }
        });

        emptyBox = Ui.column(this);
        emptyBox.setGravity(Gravity.CENTER);
        emptyBox.setPadding(Ui.dp(this, Ui.S6), Ui.dp(this, Ui.S8),
                Ui.dp(this, Ui.S6), Ui.dp(this, Ui.S8));
        emptyBox.addView(Ui.bold(this, "سوالی با این فیلتر نیست", Ui.T_UI, Ui.TEXT));
        emptyBox.addView(Ui.spacer(this, Ui.S2));
        TextView reset = Ui.chip(this, "نمایش همه", Ui.SURFACE_2, Ui.TEXT);
        reset.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                query = "";
                search.setText("");
                filter = FILTER_ALL;
                paintChips();
                rebuild();
            }
        });
        emptyBox.addView(reset);
        emptyBox.setVisibility(View.GONE);

        LinearLayout wrap = Ui.column(this);
        wrap.addView(emptyBox, Ui.matchWrap());
        wrap.addView(list, Ui.fillWeight());
        return wrap;
    }

    private View buildFooter() {
        LinearLayout footer = Ui.column(this);
        footer.setPadding(Ui.dp(this, Ui.S4), Ui.dp(this, Ui.S1),
                Ui.dp(this, Ui.S4), Ui.dp(this, Ui.S4));

        quizButton = Ui.chip(this, "شروع آزمون از این فهرست", Ui.ACCENT, Ui.ACCENT_INK);
        quizButton.setGravity(Gravity.CENTER);
        quizButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startQuiz(0);
            }
        });
        footer.addView(quizButton, Ui.matchWrap());
        return footer;
    }

    // ----------------------------------------------------------------- data

    private void rebuild() {
        rows.clear();
        // One word-index lookup per query, instead of re-folding every stem and
        // option on every filter change.
        Set<Integer> hits = (query.isEmpty() || bank == null)
                ? null : bank.searchIds(query);
        for (Question q : base) {
            if (matches(q, hits)) {
                rows.add(q);
            }
        }
        int answered = 0;
        for (Question q : base) {
            if (state.answer(q.id) > 0) {
                answered++;
            }
        }
        countLabel.setText(Ui.fa(rows.size()) + " سوال"
                + (answered > 0 ? "  ·  " + Ui.fa(answered) + " پاسخ‌داده" : ""));
        boolean none = rows.isEmpty();
        emptyBox.setVisibility(none ? View.VISIBLE : View.GONE);
        list.setVisibility(none ? View.GONE : View.VISIBLE);
        quizButton.setEnabled(!none);
        quizButton.setAlpha(none ? 0.45f : 1f);
        quizButton.setText(none ? "سوالی برای آزمون نیست"
                : "شروع آزمون از این فهرست (" + Ui.fa(rows.size()) + ")");
        if (list.getAdapter() != null) {
            ((BaseAdapter) list.getAdapter()).notifyDataSetChanged();
        }
    }

    private boolean matches(Question q, Set<Integer> hits) {
        int picked = state.answer(q.id);
        if (filter == FILTER_UNANSWERED && picked > 0) {
            return false;
        }
        if (filter == FILTER_MARKED && !state.marked(q.id)) {
            return false;
        }
        if (filter == FILTER_WRONG
                && (picked == 0 || !q.hasAnswer() || picked == q.correct)) {
            return false;
        }
        return hits == null || hits.contains(q.id);
    }

    private void startQuiz(int startIndex) {
        if (rows.isEmpty()) {
            toast("این فهرست خالی است");
            return;
        }
        int[] ids = new int[rows.size()];
        for (int i = 0; i < rows.size(); i++) {
            ids[i] = rows.get(i).id;
        }
        Intent intent = new Intent(this, QuizActivity.class);
        intent.putExtra(QuizActivity.EXTRA_IDS, ids);
        intent.putExtra(QuizActivity.EXTRA_TITLE, title);
        intent.putExtra(QuizActivity.EXTRA_START_INDEX,
                Math.max(0, Math.min(startIndex, rows.size() - 1)));
        startActivity(intent);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    // --------------------------------------------------------------- adapter

    private final class RowAdapter extends BaseAdapter {

        @Override
        public int getCount() {
            return rows.size();
        }

        @Override
        public Object getItem(int position) {
            return rows.get(position);
        }

        @Override
        public long getItemId(int position) {
            return rows.get(position).id;
        }

        @Override
        public View getView(int position, View recycled, ViewGroup parent) {
            Question q = rows.get(position);
            int picked = state.answer(q.id);
            boolean marked = state.marked(q.id);

            LinearLayout wrap = Ui.column(BrowseActivity.this);
            wrap.setPadding(Ui.dp(BrowseActivity.this, Ui.S4), Ui.dp(BrowseActivity.this, Ui.S2),
                    Ui.dp(BrowseActivity.this, Ui.S4), Ui.dp(BrowseActivity.this, Ui.S2));

            LinearLayout top = Ui.row(BrowseActivity.this);

            TextView index = Ui.pill(BrowseActivity.this, Ui.fa(position + 1),
                    Ui.SURFACE_2, Ui.TEXT_3, Ui.T_XS);
            top.addView(index);

            if (marked) {
                TextView star = Ui.text(BrowseActivity.this, "\u2605", Ui.T_SM, Ui.WARN);
                star.setPadding(Ui.dp(BrowseActivity.this, Ui.S2), 0, 0, 0);
                top.addView(star);
            }

            if (picked > 0 && q.hasAnswer()) {
                boolean right = picked == q.correct;
                TextView statePill = Ui.pill(BrowseActivity.this,
                        right ? "\u2713 درست" : "\u2717 نادرست",
                        right ? Ui.OK_BG : Ui.BAD_BG,
                        right ? Ui.OK : Ui.BAD, Ui.T_XS);
                LinearLayout.LayoutParams p = Ui.wrap();
                p.setMargins(Ui.dp(BrowseActivity.this, Ui.S2), 0, 0, 0);
                top.addView(statePill, p);
            } else if (picked > 0) {
                TextView statePill = Ui.pill(BrowseActivity.this, "بدون پاسخ در بانک",
                        Ui.SURFACE_2, Ui.WARN, Ui.T_XS);
                LinearLayout.LayoutParams p = Ui.wrap();
                p.setMargins(Ui.dp(BrowseActivity.this, Ui.S2), 0, 0, 0);
                top.addView(statePill, p);
            }

            View gap = new View(BrowseActivity.this);
            top.addView(gap, Ui.weight(1f));

            if (!TextUtils.isEmpty(q.path)) {
                TextView path = Ui.text(BrowseActivity.this, q.path, Ui.T_XS, Ui.TEXT_3);
                path.setSingleLine(true);
                path.setEllipsize(TextUtils.TruncateAt.END);
                top.addView(path);
            }
            wrap.addView(top);

            TextView stem = Ui.text(BrowseActivity.this, q.stemText, Ui.T_UI, Ui.TEXT);
            stem.setMaxLines(3);
            stem.setEllipsize(TextUtils.TruncateAt.END);
            stem.setLineSpacing(Ui.dp(BrowseActivity.this, 4), 1f);
            LinearLayout.LayoutParams sp = Ui.matchWrap();
            sp.setMargins(0, Ui.dp(BrowseActivity.this, Ui.S1), 0, Ui.dp(BrowseActivity.this, Ui.S1));
            stem.setLayoutParams(sp);
            wrap.addView(stem);

            TextView meta = Ui.text(BrowseActivity.this,
                    Ui.fa(q.options.size()) + " گزینه"
                            + (q.hasExplanation() ? "  ·  پاسخ تشریحی" : ""),
                    Ui.T_XS, Ui.TEXT_3);
            wrap.addView(meta);

            View line = new View(BrowseActivity.this);
            line.setBackgroundColor(Ui.LINE);
            LinearLayout.LayoutParams lp = Ui.matchWrap();
            lp.height = Math.max(1, Ui.dp(BrowseActivity.this, 1));
            wrap.addView(line, lp);

            Ui.describe(wrap, "سوال " + Ui.fa(position + 1) + ": " + q.stemText);
            return wrap;
        }
    }
}
