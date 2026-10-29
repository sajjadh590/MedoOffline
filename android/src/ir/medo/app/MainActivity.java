package ir.medo.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.StateListDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.Editable;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.DecelerateInterpolator;
import android.widget.AdapterView;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Home: the category tree, offline, with search and "continue where you left off". */
public final class MainActivity extends Activity {

    private static final int PICK_FILE = 41;
    private static final int SCAN_CODE = 42;
    private static final int RANDOM_COUNT = 20;
    private static final String PREFS = "medo";
    private static final String PREF_LAST_NODE = "last_node";
    private static final String PREF_TREE_MODE = "tree_mode";
    private static final String MODE_MINE = "mine";
    private static final String MODE_ALL = "all";

    private Bank bank;
    private StateStore state;

    private ListView list;
    private TextView counter;
    private TextView subtitle;
    private TextView statQuestions;
    private TextView statAnswered;
    private TextView statAccuracy;
    private LinearLayout emptyBox;
    private LinearLayout resumeCard;
    private TextView resumeTitle;
    private EditText search;
    private TextView mineTab;
    private TextView allTab;
    private String treeMode = MODE_MINE;

    private final List<Node> rows = new ArrayList<>();
    private RowAdapter adapter;
    private String query = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Ui.apply(this);
        treeMode = getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(PREF_TREE_MODE, MODE_MINE);
        if (!MODE_ALL.equals(treeMode)) {
            treeMode = MODE_MINE;
        }
        state = new StateStore(new File(getFilesDir(), "state.json"));
        setContentView(build());
        load();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (Ui.apply(this)) {
            recreate();
            return;
        }
        // QuizActivity writes through its own StateStore, so re-read the file.
        state.load();
        if (adapter != null) {
            adapter.notifyDataSetChanged();
        }
        refreshHeader();
        refreshResume();
    }

    // -------------------------------------------------------------------- UI

    private View build() {
        LinearLayout root = Ui.column(this);
        root.setBackgroundColor(Ui.INK);
        root.setFitsSystemWindows(true);

        root.addView(buildHeader());
        root.addView(buildEmptyState());
        root.addView(buildList(), Ui.fillWeight());
        root.addView(buildFooter());
        return root;
    }

    private View buildHeader() {
        LinearLayout header = Ui.column(this);
        header.setPadding(Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S5),
                Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S2));

        LinearLayout titleRow = Ui.row(this);
        LinearLayout titles = Ui.column(this);
        TextView logo = Ui.bold(this, "مدو", Ui.T_DISPLAY, Ui.TEXT);
        titles.addView(logo);
        subtitle = Ui.text(this, "آزمون آفلاین سوالات پزشکی", Ui.T_SM, Ui.TEXT_3);
        subtitle.setPadding(0, Ui.dp(this, 2), 0, 0);
        titles.addView(subtitle);
        titleRow.addView(titles, Ui.weight(1f));

        counter = Ui.pill(this, "—", Ui.ACCENT_BG, Ui.ACCENT, Ui.T_SM);
        titleRow.addView(counter);

        TextView settingsButton = Ui.iconButton(this, "\u2699", 19, Ui.TEXT_2);
        Ui.describe(settingsButton, "تنظیمات و ظاهر برنامه");
        settingsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, SettingsActivity.class));
            }
        });
        titleRow.addView(settingsButton);

        // Import lives here instead of eating one of the two main actions.
        TextView importButton = Ui.iconButton(this, "\u21E9", 20, Ui.TEXT_2);
        Ui.describe(importButton, "دریافت بانک");
        importButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showImportMenu();
            }
        });
        titleRow.addView(importButton);
        header.addView(titleRow);

        header.addView(Ui.spacer(this, Ui.S4));

        // Stat strip: a *row*. It used to be a vertical card whose weighted
        // children were measured against the full screen height — that single
        // mistake produced the giant empty panel on the home screen.
        LinearLayout stats = Ui.row(this);
        stats.setBackground(Ui.outlined(this, Ui.SURFACE, Ui.R_CARD, Ui.LINE, 1));
        stats.setPadding(Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S3),
                Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S3));
        statQuestions = addStat(stats, "—", "سوال");
        addStatDivider(stats);
        statAnswered = addStat(stats, "—", "پاسخ");
        addStatDivider(stats);
        statAccuracy = addStat(stats, "—", "دقت");
        header.addView(stats, Ui.matchWrap());
        header.addView(buildTreeTabs(), Ui.margins(this,
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, 0, Ui.S2, 0, 0));

        // Resume card appears only when a section was opened before.
        resumeCard = Ui.row(this);
        resumeCard.setBackground(Ui.outlined(this, Ui.ACCENT_BG, Ui.R_CONTROL, Ui.ACCENT_STROKE, 1));
        resumeCard.setPadding(Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S3), Ui.dp(this, Ui.S2));
        resumeCard.setVisibility(View.GONE);
        resumeCard.setClickable(true);
        TextView resumeGlyph = Ui.text(this, "\u21BA", 18, Ui.ACCENT);
        resumeCard.addView(resumeGlyph);
        LinearLayout resumeTexts = Ui.column(this);
        resumeTitle = Ui.bold(this, "", Ui.T_BODY, Ui.TEXT);
        resumeTexts.addView(resumeTitle);
        resumeTexts.addView(Ui.text(this, "ادامهٔ آخرین بخش", Ui.T_XS, Ui.TEXT_3));
        resumeCard.addView(resumeTexts, Ui.weight(1f));
        LinearLayout.LayoutParams rp = Ui.matchWrap();
        rp.setMargins(0, Ui.dp(this, Ui.S2), 0, 0);
        header.addView(resumeCard, rp);

        header.addView(Ui.spacer(this, Ui.S3));
        header.addView(buildSearch());
        header.addView(Ui.spacer(this, Ui.S2));

        LinearLayout actions = Ui.row(this);
        TextView browse = Ui.chip(this, "همهٔ سوال‌ها", Ui.SURFACE_2, Ui.TEXT);
        browse.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openBrowse(null);
            }
        });
        TextView random = Ui.chip(this, "آزمون تصادفی", Ui.ACCENT, Ui.ACCENT_INK);
        random.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startRandom();
            }
        });
        actions.addView(browse, Ui.weight(1f));
        actions.addView(Ui.gap(this, Ui.S2), Ui.lp(Ui.dp(this, Ui.S2), 1));
        actions.addView(random, Ui.weight(1f));
        header.addView(actions);

        // Scanning the desktop's QR was only reachable through the import
        // menu, where nobody found it. Same action, its own row.
        TextView scanRow = Ui.chip(this, "اسکن QR — دریافت بانک یا نسخهٔ جدید برنامه",
                Ui.SURFACE_2, Ui.ACCENT);
        scanRow.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                scan();
            }
        });
        Ui.describe(scanRow, "اسکن کد QR کامپیوتر برای دریافت بانک یا نسخهٔ جدید برنامه");
        header.addView(scanRow, Ui.margins(this, ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, 0, Ui.S2, 0, 0));

        return header;
    }

    /** «بانک من» vs «همهٔ دسته‌ها», mirroring the desktop tree tabs. */
    private View buildTreeTabs() {
        LinearLayout tabs = Ui.row(this);
        tabs.setBackground(Ui.rounded(this, Ui.SURFACE, Ui.R_CONTROL));
        tabs.setPadding(Ui.dp(this, 3), Ui.dp(this, 3),
                Ui.dp(this, 3), Ui.dp(this, 3));
        mineTab = Ui.chip(this, "بانک من", Ui.ACCENT, Ui.ACCENT_INK);
        allTab = Ui.chip(this, "همهٔ دسته‌ها", Ui.SURFACE, Ui.TEXT_2);
        mineTab.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                setTreeMode(MODE_MINE);
            }
        });
        allTab.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                setTreeMode(MODE_ALL);
            }
        });
        Ui.describe(mineTab, "فقط دسته‌هایی که سوال دارند");
        Ui.describe(allTab, "همهٔ دسته‌های سایت");
        tabs.addView(mineTab, Ui.weight(1f));
        tabs.addView(Ui.gap(this, Ui.S1));
        tabs.addView(allTab, Ui.weight(1f));
        boolean mine = MODE_MINE.equals(treeMode);
        if (!mine) {
            mineTab.setBackground(Ui.rounded(this, Ui.SURFACE, Ui.R_CONTROL));
            mineTab.setTextColor(Ui.TEXT_2);
            allTab.setBackground(Ui.rounded(this, Ui.ACCENT, Ui.R_CONTROL));
            allTab.setTextColor(Ui.ACCENT_INK);
        }
        return tabs;
    }

    private void setTreeMode(String mode) {
        treeMode = MODE_ALL.equals(mode) ? MODE_ALL : MODE_MINE;
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putString(PREF_TREE_MODE, treeMode).apply();
        if (mineTab != null && allTab != null) {
            boolean mine = MODE_MINE.equals(treeMode);
            mineTab.setBackground(Ui.rounded(this,
                    mine ? Ui.ACCENT : Ui.SURFACE, Ui.R_CONTROL));
            mineTab.setTextColor(mine ? Ui.ACCENT_INK : Ui.TEXT_2);
            allTab.setBackground(Ui.rounded(this,
                    mine ? Ui.SURFACE : Ui.ACCENT, Ui.R_CONTROL));
            allTab.setTextColor(mine ? Ui.TEXT_2 : Ui.ACCENT_INK);
        }
        rebuildRows();
    }

    private TextView addStat(LinearLayout parent, String value, String caption) {
        LinearLayout box = Ui.column(this);
        box.setGravity(Gravity.CENTER);
        TextView number = Ui.bold(this, value, 19, Ui.TEXT);
        number.setGravity(Gravity.CENTER);
        TextView label = Ui.text(this, caption, Ui.T_XS, Ui.TEXT_3);
        label.setGravity(Gravity.CENTER);
        label.setPadding(0, Ui.dp(this, 2), 0, 0);
        box.addView(number);
        box.addView(label);
        parent.addView(box, Ui.weight(1f));
        return number;
    }

    private void addStatDivider(LinearLayout parent) {
        View v = new View(this);
        v.setBackgroundColor(Ui.LINE);
        parent.addView(v, Ui.lp(Math.max(1, Ui.dp(this, 1)), Ui.dp(this, 30)));
    }

    private View buildSearch() {
        LinearLayout wrap = Ui.row(this);
        wrap.setBackground(Ui.rounded(this, Ui.SURFACE, Ui.R_CONTROL));
        wrap.setPadding(Ui.dp(this, Ui.S3), 0, Ui.dp(this, Ui.S3), 0);
        LinearLayout.LayoutParams p = Ui.matchWrap();
        p.setMargins(0, Ui.dp(this, Ui.S2), 0, 0);
        wrap.setLayoutParams(p);
        wrap.setMinimumHeight(Ui.dp(this, 46));

        TextView glyph = Ui.text(this, "\u2315", 17, Ui.TEXT_3);
        glyph.setPadding(0, 0, Ui.dp(this, Ui.S2), 0);
        wrap.addView(glyph);

        search = new EditText(this);
        search.setHint("جستجوی مبحث یا سوال…");
        search.setHintTextColor(Ui.TEXT_3);
        search.setTextColor(Ui.TEXT);
        search.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, Ui.T_BODY);
        search.setSingleLine(true);
        search.setBackgroundColor(Color.TRANSPARENT);
        search.setPadding(0, 0, 0, 0);
        search.setInputType(InputType.TYPE_CLASS_TEXT);
        search.setTextDirection(View.TEXT_DIRECTION_RTL);
        Ui.describe(search, "جستجوی مبحث");
        search.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {
                query = s == null ? "" : s.toString().trim();
                rebuildRows();
            }

            @Override
            public void afterTextChanged(Editable s) {
            }
        });
        wrap.addView(search, Ui.weight(1f));
        return wrap;
    }

    private View buildEmptyState() {
        emptyBox = Ui.column(this);
        emptyBox.setBackground(Ui.outlined(this, Ui.SURFACE, Ui.R_PANEL, Ui.LINE, 1));
        emptyBox.setPadding(Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S5),
                Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S5));
        LinearLayout.LayoutParams p = Ui.matchWrap();
        p.setMargins(Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S4), Ui.dp(this, Ui.S5), Ui.dp(this, Ui.S3));
        emptyBox.setLayoutParams(p);

        emptyBox.addView(Ui.bold(this, "بانک هنوز روی گوشی نیست", 17, Ui.TEXT));
        emptyBox.addView(Ui.spacer(this, Ui.S2));
        emptyBox.addView(Ui.text(this,
                "روی کامپیوتر دستور زیر را اجرا کن و آدرسی که چاپ می‌شود را در برنامه وارد کن:",
                Ui.T_BODY, Ui.TEXT_2));
        emptyBox.addView(Ui.spacer(this, Ui.S3));

        TextView cmd = Ui.text(this, "medo share", Ui.T_BODY, Ui.ACCENT);
        cmd.setTypeface(Typeface.MONOSPACE);
        cmd.setTextDirection(View.TEXT_DIRECTION_LTR);
        cmd.setBackground(Ui.rounded(this, Ui.INK, 8));
        cmd.setPadding(Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S2),
                Ui.dp(this, Ui.S2), Ui.dp(this, Ui.S2));
        emptyBox.addView(cmd);

        emptyBox.addView(Ui.spacer(this, Ui.S3));
        emptyBox.addView(step("۱", "روی کامپیوتر medo share را اجرا کن و QR را با دوربین گوشی اسکن کن"));
        emptyBox.addView(step("۲", "یا فایل .medo را با کابل کپی کن و «از فایل» را بزن"));
        emptyBox.addView(step("۳", "بعد از دریافت، مبحث را انتخاب کن و سوال بزن"));

        emptyBox.addView(Ui.spacer(this, Ui.S4));
        TextView cta = Ui.chip(this, "دریافت بانک", Ui.ACCENT, Ui.ACCENT_INK);
        cta.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showImportMenu();
            }
        });
        LinearLayout.LayoutParams ctaParams = Ui.matchWrap();
        ctaParams.setMargins(0, 0, 0, 0);
        emptyBox.addView(cta, ctaParams);
        return emptyBox;
    }

    private View step(String number, String text) {
        LinearLayout row = Ui.row(this);
        row.setPadding(0, Ui.dp(this, Ui.S1), 0, 0);
        TextView n = Ui.text(this, number, Ui.T_XS, Ui.ACCENT);
        n.setGravity(Gravity.CENTER);
        n.setBackground(Ui.circle(this, Ui.ACCENT_BG, Ui.ACCENT_STROKE, 0));
        n.setLayoutParams(Ui.lp(Ui.dp(this, 20), Ui.dp(this, 20)));
        row.addView(n);
        TextView body = Ui.text(this, text, Ui.T_SM, Ui.TEXT_2);
        body.setPadding(0, 0, Ui.dp(this, Ui.S2), 0);
        row.addView(body, Ui.weight(1f));
        return row;
    }

    private View buildList() {
        list = new ListView(this);
        list.setDivider(null);
        list.setDividerHeight(0);
        list.setCacheColorHint(Color.TRANSPARENT);
        list.setSelector(pressedSelector());
        list.setBackgroundColor(Color.TRANSPARENT);
        list.setVerticalScrollBarEnabled(false);
        adapter = new RowAdapter();
        list.setAdapter(adapter);
        list.setOnItemClickListener(new AdapterView.OnItemClickListener() {
            @Override
            public void onItemClick(AdapterView<?> parent, View view, int position, long id) {
                onRow(rows.get(position), view);
            }
        });
        list.setOnItemLongClickListener(new AdapterView.OnItemLongClickListener() {
            @Override
            public boolean onItemLongClick(AdapterView<?> parent, View view, int position, long id) {
                showNodeMenu(rows.get(position));
                return true;
            }
        });
        return list;
    }

    private View buildFooter() {
        TextView hint = Ui.text(this,
                "لمس: دیدن سوال‌ها  ·  فلش: باز/بستن  ·  ⋯ یا نگه‌داشتن: آزمون و PDF",
                Ui.T_XS, Ui.TEXT_3);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, Ui.dp(this, Ui.S2), 0, Ui.dp(this, Ui.S3));
        return hint;
    }

    private StateListDrawable pressedSelector() {
        StateListDrawable selector = new StateListDrawable();
        selector.addState(new int[]{android.R.attr.state_pressed},
                new ColorDrawable(Ui.PRESSED));
        selector.addState(new int[]{}, new ColorDrawable(Color.TRANSPARENT));
        return selector;
    }

    // ------------------------------------------------------------------- data

    private void load() {
        BankStore.loadAsync(this, new BankStore.Loaded() {
            @Override
            public void onLoaded(Bank loaded) {
                bank = loaded;
                applyBank();
            }

            @Override
            public void onError(String message) {
                bank = null;
                applyBank();
                toast("بانک خوانده نشد: " + message);
            }
        });
    }

    private void applyBank() {
        rebuildRows();
        boolean has = bank != null && bank.size() > 0;
        emptyBox.setVisibility(has ? View.GONE : View.VISIBLE);
        list.setVisibility(has ? View.VISIBLE : View.GONE);
        refreshHeader();
        refreshResume();
    }

    private void rebuildRows() {
        rows.clear();
        if (bank != null) {
            if (query.isEmpty()) {
                if (MODE_MINE.equals(treeMode)) {
                    // Only branches that actually hold downloaded questions:
                    // the same meaning as «بانک من» in the desktop tree.
                    bank.flattenStored(bank.roots, rows);
                } else {
                    Bank.flatten(bank.roots, rows);
                }
            } else {
                List<Node> all = new ArrayList<>();
                Bank.flattenAll(bank.roots, all);
                String needle = Text.normalizeSearch(query);
                for (Node n : all) {
                    if (Text.normalizeSearch(n.label()).contains(needle)) {
                        rows.add(n);
                    }
                }
            }
        }
        if (adapter != null) {
            adapter.notifyDataSetChanged();
        }
    }

    private int[] progress() {
        int answered = 0;
        int correct = 0;
        if (bank != null) {
            for (Question q : bank.questions) {
                int picked = state.answer(q.id);
                if (picked > 0) {
                    answered++;
                    if (q.hasAnswer() && picked == q.correct) {
                        correct++;
                    }
                }
            }
        }
        return new int[]{answered, correct};
    }

    private void refreshHeader() {
        int[] p = progress();
        if (bank == null) {
            counter.setText("—");
            statQuestions.setText("—");
            statAnswered.setText("—");
            statAccuracy.setText("—");
            subtitle.setText("برای شروع، بانک را دریافت کن");
            return;
        }
        counter.setText(Ui.fa(bank.size()) + " سوال");
        statQuestions.setText(Ui.fa(bank.size()));
        statAnswered.setText(Ui.fa(p[0]));
        statAccuracy.setText(p[0] == 0 ? "—" : Ui.fa(Math.round(100f * p[1] / p[0])) + "٪");
        subtitle.setText(BankStore.readableSize(this) + " · ذخیره‌شده روی گوشی");
    }

    private void refreshResume() {
        if (resumeCard == null) {
            return;
        }
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String url = prefs.getString(PREF_LAST_NODE, null);
        Node node = bank == null || url == null ? null : bank.node(url);
        if (node == null || bank.forNode(node).isEmpty()) {
            resumeCard.setVisibility(View.GONE);
            return;
        }
        resumeCard.setVisibility(View.VISIBLE);
        resumeTitle.setText(node.label());
        resumeCard.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openQuiz(node);
            }
        });
        Ui.describe(resumeCard, "ادامهٔ " + node.label());
    }

    // ---------------------------------------------------------------- actions

    /** A tap on any node that has questions opens the question list — the
     *  tree chevron (a separate target) still expands and collapses it. */
    private void onRow(Node node, View rowView) {
        int count = bank == null ? 0 : bank.countFor(node);
        if (count > 0) {
            openBrowse(node);
            return;
        }
        if (node.hasChildren()) {
            toggleNode(node, rowView.findViewWithTag("chevron"));
            return;
        }
        // An empty subtopic is still part of its topic's pool: the archive was
        // downloaded from the unfiltered topic page, so the slice is empty but
        // the questions exist one level up. Never leave the tap as a dead end.
        Node up = bank == null ? null : bank.nearestWithQuestions(node.parent);
        if (up != null) {
            showEmptyNode(node, up);
            return;
        }
        toast("این بخش هنوز سوالی ندارد");
    }

    private void showEmptyNode(final Node node, final Node up) {
        final int count = bank.countFor(up);
        new AlertDialog.Builder(this)
                .setTitle(node.label())
                .setMessage("این زیرمبحث جداگانه دانلود نشده است، اما سوال‌هایش داخل «"
                        + up.label() + "» هست.")
                .setPositiveButton("دیدن سوال‌ها (" + Ui.fa(count) + ")",
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                openBrowse(up);
                            }
                        })
                .setNeutralButton("آزمون", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        openQuiz(up);
                    }
                })
                .setNegativeButton("انصراف", null)
                .show();
    }

    private void toggleNode(Node node, View chevron) {
        node.expanded = !node.expanded;
        if (chevron != null) {
            chevron.animate()
                    .rotation(node.expanded ? 90f : 0f)
                    .setDuration(Ui.ms(this, 150))
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
        }
        rebuildRows();
    }

    private void openBrowse(Node node) {
        Intent intent = new Intent(this, BrowseActivity.class);
        if (node != null) {
            intent.putExtra(BrowseActivity.EXTRA_NODE, node.sourceUrl);
            intent.putExtra(BrowseActivity.EXTRA_TITLE, node.label());
        }
        try {
            startActivity(intent);
        } catch (RuntimeException e) {
            toast("فهرست سوال‌ها باز نشد: " + e.getMessage());
        }
    }

    /** The row menu: long-press anywhere on a row, or the ⋯ button. */
    private void showNodeMenu(final Node node) {
        final List<Question> questions = bank == null
                ? new ArrayList<Question>() : bank.forNode(node);
        final Node up = questions.isEmpty() && bank != null
                ? bank.nearestWithQuestions(node.parent) : null;
        if (questions.isEmpty() && up == null) {
            toast("این بخش هنوز سوالی ندارد");
            return;
        }

        final List<String> labels = new ArrayList<>();
        final List<Integer> actions = new ArrayList<>();
        if (!questions.isEmpty()) {
            labels.add("دیدن سوال‌ها (" + Ui.fa(questions.size()) + ")");
            actions.add(0);
            labels.add("آزمون این بخش (" + Ui.fa(questions.size()) + " سوال)");
            actions.add(1);
            labels.add("خروجی PDF این بخش");
            actions.add(2);
        } else {
            int count = bank.countFor(up);
            labels.add("دیدن سوال‌های «" + up.label() + "» (" + Ui.fa(count) + ")");
            actions.add(0);
            labels.add("آزمون «" + up.label() + "»");
            actions.add(1);
        }
        if (node.hasChildren()) {
            labels.add(node.expanded ? "بستن زیرشاخه‌ها" : "باز کردن زیرشاخه‌ها");
            actions.add(3);
        }

        new AlertDialog.Builder(this)
                .setTitle(node.label())
                .setItems(labels.toArray(new String[0]), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        Node target = up == null ? node : up;
                        int action = actions.get(which);
                        if (action == 0) {
                            openBrowse(target);
                        } else if (action == 1) {
                            openQuiz(target);
                        } else if (action == 2) {
                            PdfExporter.export(MainActivity.this, target.label(),
                                    questions, state, bank);
                        } else {
                            toggleNode(node, null);
                        }
                    }
                })
                .show();
    }

    private void openQuiz(Node node) {
        List<Question> questions = bank.forNode(node);
        if (questions.isEmpty()) {
            toast("این بخش هنوز سوالی ندارد");
            return;
        }
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putString(PREF_LAST_NODE, node.sourceUrl).apply();
        Intent intent = new Intent(this, QuizActivity.class);
        intent.putExtra(QuizActivity.EXTRA_NODE, node.sourceUrl);
        intent.putExtra(QuizActivity.EXTRA_TITLE, node.label());
        startActivity(intent);
    }

    private void startRandom() {
        if (bank == null || bank.size() == 0) {
            toast("اول بانک را دریافت کن");
            showImportMenu();
            return;
        }
        List<Question> picked = bank.random(RANDOM_COUNT);
        if (picked.isEmpty()) {
            toast("سوالی با گزینه پیدا نشد");
            return;
        }
        int[] ids = new int[picked.size()];
        for (int i = 0; i < picked.size(); i++) {
            ids[i] = picked.get(i).id;
        }
        Intent intent = new Intent(this, QuizActivity.class);
        intent.putExtra(QuizActivity.EXTRA_IDS, ids);
        intent.putExtra(QuizActivity.EXTRA_TITLE, "آزمون تصادفی");
        startActivity(intent);
    }

    // --------------------------------------------------------------- import

    private void showImportMenu() {
        new AlertDialog.Builder(this)
                .setTitle("دریافت بانک یا به‌روزرسانی")
                .setItems(new String[]{
                        "اسکن QR با دوربین",
                        "از فایل (.medo)",
                        "از وای‌فای / USB"}, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        if (which == 0) {
                            scan();
                        } else if (which == 1) {
                            pickFile();
                        } else {
                            showAddressDialog();
                        }
                    }
                })
                .show();
    }

    private void scan() {
        try {
            startActivityForResult(new Intent(this, ScanActivity.class), SCAN_CODE);
        } catch (RuntimeException e) {
            toast("دوربین باز نشد: " + e.getMessage());
        }
    }

    private void pickFile() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        try {
            startActivityForResult(intent, PICK_FILE);
        } catch (Exception e) {
            toast("فایل‌منیجر سیستم پیدا نشد");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == SCAN_CODE) {
            String text = resultCode == RESULT_OK && data != null
                    ? data.getStringExtra(ScanActivity.EXTRA_RESULT) : null;
            if (text != null && !text.trim().isEmpty()) {
                handleScan(text.trim());
            }
            return;
        }
        if (requestCode != PICK_FILE || resultCode != RESULT_OK
                || data == null || data.getData() == null) {
            return;
        }
        final Uri uri = data.getData();
        final ProgressDialog progress = progress("در حال خواندن فایل…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    InputStream in = getContentResolver().openInputStream(uri);
                    byte[] blob = Io.readAll(in);
                    final Bank installed = BankStore.install(MainActivity.this, blob);
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            progress.dismiss();
                            bank = installed;
                            applyBank();
                            toast("بانک نصب شد: " + Ui.fa(installed.size()) + " سوال");
                        }
                    });
                } catch (final Exception e) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            progress.dismiss();
                            toast("فایل خوانده نشد: " + e.getMessage());
                        }
                    });
                }
            }
        }, "bank-file").start();
    }

    private void showAddressDialog() {
        final EditText input = new EditText(this);
        input.setText(BankStore.savedAddress(this));
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setTextDirection(View.TEXT_DIRECTION_LTR);
        input.setTextAlignment(View.TEXT_ALIGNMENT_CENTER);
        input.setSingleLine(true);
        input.setTextColor(Ui.TEXT);
        input.setHintTextColor(Ui.TEXT_3);

        LinearLayout box = Ui.column(this);
        int pad = Ui.dp(this, Ui.S5);
        box.setPadding(pad, pad, pad, 0);
        TextView hint = Ui.text(this,
                "روی کامپیوتر: medo share — آدرسی که چاپ می‌شود را همین‌جا بگذار.\n"
                        + "برای کابل USB: adb reverse tcp:8765 tcp:8765 و آدرس 127.0.0.1:8765",
                Ui.T_SM, Ui.TEXT_2);
        box.addView(hint);
        box.addView(input, Ui.matchWrap());

        new AlertDialog.Builder(this)
                .setTitle("دریافت از وای‌فای")
                .setView(box)
                .setPositiveButton("دریافت", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        String address = input.getText().toString();
                        BankStore.saveAddress(MainActivity.this, address);
                        download(address);
                    }
                })
                .setNegativeButton("انصراف", null)
                .show();
    }

    /** One QR can carry either an update (…/medo.apk) or a fresh bank. */
    private void handleScan(String text) {
        String url = text;
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        String lower = url.toLowerCase(Locale.ROOT);
        if (lower.endsWith(".apk") || lower.contains("/medo.apk") || lower.contains("/app.apk")) {
            downloadUpdate(url);
            return;
        }
        BankStore.saveAddress(this, url);
        download(url);
    }

    private void downloadUpdate(final String url) {
        final File dir = new File(getCacheDir(), "update");
        if (!dir.isDirectory() && !dir.mkdirs()) {
            toast("پوشهٔ موقت ساخته نشد");
            return;
        }
        final File target = new File(dir, "medo.apk");
        final ProgressDialog progress = progress("در حال دریافت نسخهٔ جدید…");
        BankStore.download(url, target, new BankStore.Download() {
            @Override
            public void onDone(File file) {
                progress.dismiss();
                installUpdate(file);
            }

            @Override
            public void onError(String message) {
                progress.dismiss();
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle("به‌روزرسانی دریافت نشد")
                        .setMessage(message)
                        .setPositiveButton("باشه", null)
                        .show();
            }
        });
    }

    private void installUpdate(File file) {
        if (android.os.Build.VERSION.SDK_INT >= 26
                && !getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(this)
                    .setTitle("اجازهٔ نصب لازم است")
                    .setMessage("برای نصب نسخهٔ جدید، اجازهٔ «نصب برنامه‌های ناشناس» را برای مدو روشن کن.")
                    .setPositiveButton("تنظیمات", new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface dialog, int which) {
                            try {
                                startActivity(new Intent(
                                        android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                        Uri.parse("package:" + getPackageName())));
                            } catch (RuntimeException e) {
                                toast("تنظیمات این گوشی باز نشد");
                            }
                        }
                    })
                    .setNegativeButton("بعداً", null)
                    .show();
            return;
        }
        Uri apk = SharedFileProvider.uriFor(this, file);
        Intent intent = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(apk, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(intent);
        } catch (RuntimeException e) {
            new AlertDialog.Builder(this)
                    .setTitle("نصب‌کننده باز نشد")
                    .setMessage("فایل در حافظهٔ برنامه ذخیره شد: medo.apk\nمی‌توانی با فایل‌منیجر نصبش کنی.")
                    .setPositiveButton("باشه", null)
                    .show();
        }
    }

    private void download(String address) {
        final ProgressDialog progress = progress("در حال دریافت از " + BankStore.normalize(address));
        BankStore.fetch(address, new BankStore.Fetch() {
            @Override
            public void onDone(byte[] data) {
                try {
                    Bank installed = BankStore.install(MainActivity.this, data);
                    progress.dismiss();
                    bank = installed;
                    applyBank();
                    toast("بانک نصب شد: " + Ui.fa(installed.size()) + " سوال");
                } catch (Exception e) {
                    progress.dismiss();
                    toast("فایل معتبر نبود: " + e.getMessage());
                }
            }

            @Override
            public void onError(String message) {
                progress.dismiss();
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle("دریافت نشد")
                        .setMessage(message + "\n\nوای‌فای: هر دو دستگاه روی یک شبکه باشند و "
                                + "فایروال ویندوز پورت ۸۷۶۵ را نبندد. USB: adb reverse را چک کن.")
                        .setPositiveButton("باشه", null)
                        .show();
            }
        });
    }

    private ProgressDialog progress(String message) {
        ProgressDialog dialog = new ProgressDialog(this);
        dialog.setMessage(message);
        dialog.setCancelable(false);
        dialog.show();
        return dialog;
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
            Node node = rows.get(position);
            boolean isRoot = node.depth == 0 && query.isEmpty();
            int indent = query.isEmpty() ? 20 + node.depth * 12 : 20;

            LinearLayout wrapper = Ui.column(MainActivity.this);

            LinearLayout row = Ui.row(MainActivity.this);
            row.setMinimumHeight(Ui.dp(MainActivity.this, 52));
            row.setPadding(Ui.dp(MainActivity.this, indent), Ui.dp(MainActivity.this, Ui.S2),
                    Ui.dp(MainActivity.this, 20), Ui.dp(MainActivity.this, Ui.S2));

            TextView chevron = Ui.text(MainActivity.this,
                    node.hasChildren() ? "\u25B8" : "\u00B7", Ui.T_BODY,
                    node.hasChildren() ? Ui.TEXT_2 : Ui.LINE_STRONG);
            chevron.setGravity(Gravity.CENTER);
            chevron.setRotation(node.expanded ? 90f : 0f);
            chevron.setTag("chevron");
            chevron.setPadding(0, 0, 0, 0);
            chevron.setLayoutParams(Ui.lp(Ui.dp(MainActivity.this, 36),
                    Ui.dp(MainActivity.this, Ui.TOUCH)));
            if (node.hasChildren()) {
                chevron.setClickable(true);
                chevron.setFocusable(true);
                chevron.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        toggleNode(node, v);
                    }
                });
                Ui.describe(chevron, "باز یا بستن " + node.label());
            }
            row.addView(chevron);

            LinearLayout labelBox = Ui.column(MainActivity.this);
            labelBox.setPadding(Ui.dp(MainActivity.this, Ui.S1), 0, 0, 0);
            TextView title = Ui.text(MainActivity.this, node.label(),
                    isRoot ? 16 : Ui.T_UI, isRoot ? Ui.TEXT : Ui.TEXT);
            if (isRoot) {
                title.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
            }
            labelBox.addView(title);

            if (node.partial) {
                TextView partial = Ui.text(MainActivity.this,
                        "ناقص — هنوز کامل دانلود نشده", Ui.T_XS, Ui.WARN);
                labelBox.addView(partial);
            } else if (isRoot && node.hasChildren()) {
                TextView meta = Ui.text(MainActivity.this,
                        Ui.fa(node.children.size()) + " زیرشاخه", Ui.T_XS, Ui.TEXT_3);
                labelBox.addView(meta);
            }
            row.addView(labelBox, Ui.weight(1f));

            int count = bank == null ? 0 : bank.countFor(node);
            TextView badge = Ui.pill(MainActivity.this,
                    count > 0 ? Ui.fa(count) : "—",
                    count > 0 ? Ui.ACCENT_BG : Ui.SURFACE_2,
                    count > 0 ? Ui.ACCENT : Ui.TEXT_3,
                    Ui.T_XS);
            row.addView(badge);

            // A visible menu target: long-press is invisible, and the previous
            // build proved easy to miss on a real phone.
            TextView more = Ui.iconButton(MainActivity.this, "\u22EF", 19, Ui.TEXT_3);
            Ui.describe(more, "گزینه‌های " + node.label());
            more.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    showNodeMenu(node);
                }
            });
            row.addView(more);

            row.setLongClickable(true);
            row.setOnLongClickListener(new View.OnLongClickListener() {
                @Override
                public boolean onLongClick(View v) {
                    showNodeMenu(node);
                    return true;
                }
            });

            wrapper.addView(row);
            if (isRoot) {
                View line = new View(MainActivity.this);
                line.setBackgroundColor(Ui.LINE);
                LinearLayout.LayoutParams lp = Ui.matchWrap();
                lp.height = Math.max(1, Ui.dp(MainActivity.this, 1));
                lp.setMargins(Ui.dp(MainActivity.this, 20), 0, Ui.dp(MainActivity.this, 20), 0);
                wrapper.addView(line, lp);
            }
            Ui.describe(row, node.label() + "، " + Ui.fa(count) + " سوال");
            return wrapper;
        }
    }
}
