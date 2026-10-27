package ir.medo.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.pdf.PdfDocument;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.text.Html;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextDirectionHeuristics;
import android.text.TextPaint;
import android.text.TextUtils;

import java.io.File;
import java.io.FileOutputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * «خروجی PDF» — a real file, not a system print dialog.
 *
 * The previous version called {@code PrintManager.print()}, which needs the
 * device print spooler; on ROMs that strip it (and on many phones with no
 * printer configured) the tap did visibly nothing. This writes an A4 PDF into
 * the app's Documents folder, then offers share/open — no external library,
 * no network, and it works with the same Persian text shaping as the UI.
 */
public final class PdfExporter {

    private static final int PAGE_W = 595;      // A4 at 72 dpi
    private static final int PAGE_H = 842;
    private static final int MARGIN = 40;
    private static final int HEADER = 54;
    private static final int FOOTER = 26;
    private static final int CONTENT_W = PAGE_W - 2 * MARGIN;

    private static final int INK = 0xFF111820;
    private static final int MUTED = 0xFF5C6B7A;
    private static final int LINE = 0xFFD8E0E8;
    private static final int ACCENT = 0xFF14805E;
    private static final int WARN = 0xFF9A6A00;

    private PdfExporter() {
    }

    /** Result handed back to the dialog so it can say what was written. */
    static final class Result {
        final File file;
        final int pages;
        final int questions;

        Result(File file, int pages, int questions) {
            this.file = file;
            this.pages = pages;
            this.questions = questions;
        }
    }

    public static void export(final Activity activity, final String title,
                              final List<Question> questions, final StateStore state) {
        if (questions == null || questions.isEmpty()) {
            toast(activity, "سوالی برای خروجی نیست");
            return;
        }
        final ProgressDialog progress = new ProgressDialog(activity);
        progress.setMessage("در حال ساخت PDF…");
        progress.setCancelable(false);
        progress.show();

        final Handler ui = new Handler(Looper.getMainLooper());
        final List<Question> items = questions;
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    final Result result = write(activity, title, items, state);
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            progress.dismiss();
                            offer(activity, result);
                        }
                    });
                } catch (final Throwable e) {
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            progress.dismiss();
                            new AlertDialog.Builder(activity)
                                    .setTitle("PDF ساخته نشد")
                                    .setMessage(e.getMessage() == null
                                            ? e.getClass().getSimpleName() : e.getMessage())
                                    .setPositiveButton("باشه", null)
                                    .show();
                        }
                    });
                }
            }
        }, "pdf-export").start();
    }

    private static void offer(final Activity activity, final Result result) {
        int kb = (int) Math.max(1, result.file.length() / 1024);
        new AlertDialog.Builder(activity)
                .setTitle("PDF آماده شد")
                .setMessage(Ui.fa(result.questions) + " سوال  ·  "                        + Ui.fa(result.pages)
                        + " صفحه  ·  " + Ui.fa(kb) + " کیلوبایت\n"
                        + "پاسخنامهٔ هر بخش در پایان همان بخش آمده است.\n\n"
                        + "پوشهٔ Documents برنامهٔ مدو")
                .setPositiveButton("اشتراک‌گذاری", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        share(activity, result.file);
                    }
                })
                .setNeutralButton("باز کردن", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        open(activity, result.file);
                    }
                })
                .setNegativeButton("باشه", null)
                .show();
    }

    // ------------------------------------------------------------------ write

    private static Result write(Activity activity, String title, List<Question> questions,
                                StateStore state) throws Exception {
        PdfDocument document = new PdfDocument();
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
        String name = "medo-" + stamp + ".pdf";

        File dir = SharedFileProvider.exportDir(activity);
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new java.io.IOException("پوشهٔ خروجی ساخته نشد");
        }
        File file = new File(dir, name);

        Pager pager = new Pager(document, title);
        if (state != null) {
            state.load();
        }
        int at = 0;
        while (at < questions.size()) {
            // Questions arrive grouped (one tray = one section), so the key of
            // each section is emitted right after that section's questions.
            String section = sectionOf(questions.get(at));
            int end = at;
            while (end < questions.size()
                    && sectionOf(questions.get(end)).equals(section)) {
                end++;
            }
            for (int i = at; i < end; i++) {
                Question q = questions.get(i);
                pager.questionHeading(i + 1, questions.size(), q);
                pager.stem(plain(q.stemHtml, q.stemText));
                pager.options(q, state);
                pager.explanation(q);
            }
            pager.answerKey(questions.subList(at, end), at + 1, section);
            at = end;
        }
        int pages = pager.finish();

        FileOutputStream out = new FileOutputStream(file);
        try {
            document.writeTo(out);
            out.flush();
        } finally {
            out.close();
            document.close();
        }
        return new Result(file, pages, questions.size());
    }

    // ------------------------------------------------------------------ pager

    private static final class Pager {

        private final PdfDocument document;
        private final String title;

        private final TextPaint headingPaint;
        private final TextPaint metaPaint;
        private final TextPaint stemPaint;
        private final TextPaint optionPaint;
        private final TextPaint optionCorrectPaint;
        private final TextPaint notePaint;
        private final TextPaint footerPaint;
        private final TextPaint sectionPaint;

        private PdfDocument.Page page;
        private Canvas canvas;
        private int y;
        private int pages;

        Pager(PdfDocument document, String title) {
            this.document = document;
            this.title = title == null ? "" : title;

            headingPaint = paint(11f, INK, Typeface.BOLD);
            metaPaint = paint(8.5f, MUTED, Typeface.NORMAL);
            stemPaint = paint(11.5f, INK, Typeface.NORMAL);
            optionPaint = paint(10.8f, INK, Typeface.NORMAL);
            optionCorrectPaint = paint(10.8f, ACCENT, Typeface.BOLD);
            notePaint = paint(9f, WARN, Typeface.NORMAL);
            footerPaint = paint(8f, MUTED, Typeface.NORMAL);
            sectionPaint = paint(9.5f, ACCENT, Typeface.BOLD);
            newPage();
        }

        private static TextPaint paint(float size, int color, int style) {
            TextPaint p = new TextPaint(Paint.ANTI_ALIAS_FLAG);
            p.setTextSize(size);
            p.setColor(color);
            p.setTypeface(Typeface.create("sans-serif", style));
            return p;
        }

        private void newPage() {
            if (page != null) {
                closePage();
            }
            page = document.startPage(new PdfDocument.PageInfo.Builder(PAGE_W, PAGE_H, ++pages).create());
            canvas = page.getCanvas();
            canvas.drawColor(Color.WHITE);

            Paint accent = new Paint(Paint.ANTI_ALIAS_FLAG);
            accent.setColor(ACCENT);
            canvas.drawRect(0, 0, PAGE_W, 3, accent);

            Paint right = paint(11f, INK, Typeface.BOLD);
            right.setTextAlign(Paint.Align.RIGHT);
            String label = title.length() > 60 ? title.substring(0, 60) + "…" : title;
            canvas.drawText(label, PAGE_W - MARGIN, 30, right);

            Paint left = paint(8f, MUTED, Typeface.NORMAL);
            left.setTextAlign(Paint.Align.LEFT);
            canvas.drawText("Medo · offline", MARGIN, 30, left);

            y = MARGIN + HEADER;
        }

        private void closePage() {
            Paint line = new Paint();
            line.setColor(LINE);
            canvas.drawRect(MARGIN, PAGE_H - MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN + 1, line);
            Paint p = paint(8f, MUTED, Typeface.NORMAL);
            p.setTextAlign(Paint.Align.RIGHT);
            canvas.drawText("صفحهٔ " + Ui.fa(pages), PAGE_W - MARGIN, PAGE_H - MARGIN + 14, p);
            p.setTextAlign(Paint.Align.LEFT);
            canvas.drawText("مدو — آفلاین", MARGIN, PAGE_H - MARGIN + 14, p);
            document.finishPage(page);
            page = null;
        }

        private int bottom() {
            return PAGE_H - MARGIN - FOOTER;
        }

        private void space(int height) {
            if (y + height > bottom()) {
                newPage();
            }
        }

        private void gap(int height) {
            y += height;
        }

        void questionHeading(int position, int total, Question q) {
            space(64);
            String meta = "سوال " + Ui.fa(position) + " از " + Ui.fa(total);
            if (q.page > 0) {
                meta += "  ·  صفحهٔ " + Ui.fa(q.page);
            }
            if (!TextUtils.isEmpty(q.path)) {
                meta += "  ·  " + q.path;
            }
            drawLine(meta, metaPaint, 0);
            gap(3);
        }

        void stem(CharSequence text) {
            flow(text, stemPaint, 6);
        }

        void options(Question q, StateStore state) {
            int picked = state == null ? 0 : state.answer(q.id);
            for (Question.Option option : q.options) {
                boolean correct = q.hasAnswer() && option.index == q.correct;
                TextPaint p = correct ? optionCorrectPaint : optionPaint;
                int h = layout(option.text == null ? "" : option.text, p, CONTENT_W - 24).getHeight();
                space(Math.max(24, h + 7));
                int lineTop = y;
                int box = 15;
                int cx = PAGE_W - MARGIN - 8;
                int cy = lineTop + 8;
                Paint circle = new Paint(Paint.ANTI_ALIAS_FLAG);
                circle.setStyle(correct ? Paint.Style.FILL : Paint.Style.STROKE);
                circle.setStrokeWidth(1.1f);
                circle.setColor(correct ? ACCENT : LINE);
                canvas.drawCircle(cx, cy, box / 2f, circle);
                Paint number = paint(7.6f, correct ? Color.WHITE : MUTED, Typeface.BOLD);
                number.setTextAlign(Paint.Align.CENTER);
                canvas.drawText(Ui.fa(option.index), cx, cy + 2.7f, number);

                flow(option.text == null ? "" : option.text, p, 3, CONTENT_W - 24);
            }
            if (!q.hasAnswer()) {
                gap(2);
                drawLine("پاسخ این سوال در بانک آفلاین ثبت نشده است", notePaint, 4);
                gap(2);
            } else if (picked > 0) {
                gap(1);
                String mark = picked == q.correct ? "پاسخ تو: درست" : "پاسخ تو: گزینهٔ " + Ui.fa(picked);
                drawLine(mark, metaPaint, 4);
                gap(2);
            }
            gap(8);
        }

        void explanation(Question q) {
            CharSequence text = plain(q.explanationHtml, q.explanationText);
            if (TextUtils.isEmpty(text)) {
                return;
            }
            space(34);
            drawLine("پاسخ تشریحی", sectionPaint, 0);
            gap(2);
            flow(text, paint(10.2f, INK, Typeface.NORMAL), 2, CONTENT_W - 10);
            gap(10);
        }

        /**
         * «پاسخنامه» — the key of one section, right after that section.
         *
         * The correct option is already marked green next to each question;
         * this is the separate sheet the teacher-side of the app expects, and
         * it is the only place a question whose key was never harvested is
         * called out explicitly.  One sheet per section instead of a single
         * one at the very end: with 400 questions the old placement was
         * dozens of pages away from the question it explained.
         */
        void answerKey(List<Question> group, int firstNumber, String label) {
            if (group == null || group.isEmpty()) {
                return;
            }
            newPage();
            String head = TextUtils.isEmpty(label) ? "پاسخنامه" : "پاسخنامهٔ «" + label + "»";
            drawLine(head, sectionPaint, 0);
            gap(3);
            drawLine("شمارهٔ سوال) گزینهٔ درست — «—» یعنی پاسخش در بانک ثبت نشده است.",
                     metaPaint, 0);
            gap(12);
            final int columns = 4;
            final float cellW = CONTENT_W / (float) columns;
            final TextPaint cell = paint(10.5f, INK, Typeface.BOLD);
            cell.setTextAlign(Paint.Align.RIGHT);
            for (int i = 0; i < group.size(); i += columns) {
                space(18);
                for (int c = 0; c < columns && i + c < group.size(); c++) {
                    Question q = group.get(i + c);
                    String text = Ui.fa(firstNumber + i + c) + ") "
                            + (q.hasAnswer() ? Ui.fa(q.correct) : "—");
                    // The page is RTL: the first column sits on the right.
                    canvas.drawText(text, PAGE_W - MARGIN - c * cellW, y + 11f, cell);
                }
                gap(16);
            }
            gap(8);
        }

        int finish() {
            if (page != null) {
                closePage();
            }
            return Math.max(1, pages);
        }

        // -------------------------------------------------------- text flow

        private StaticLayout layout(CharSequence text, TextPaint paint, int width) {
            CharSequence source = text == null ? "" : text;
            int w = Math.max(40, width);
            if (Build.VERSION.SDK_INT >= 23) {
                return StaticLayout.Builder.obtain(source, 0, source.length(), paint, w)
                        .setAlignment(Layout.Alignment.ALIGN_NORMAL)
                        .setTextDirection(TextDirectionHeuristics.RTL)
                        .setLineSpacing(3f, 1f)
                        .setIncludePad(false)
                        .build();
            }
            return new StaticLayout(source, paint, w, Layout.Alignment.ALIGN_NORMAL,
                    1f, 3f, false);
        }

        private void drawLine(String text, TextPaint paint, int indent) {
            flow(text, paint, 0, CONTENT_W - indent);
        }

        /** Draws a layout, continuing on a fresh page instead of clipping. */
        private void flow(CharSequence text, TextPaint paint, int spaceAfter) {
            flow(text, paint, spaceAfter, CONTENT_W);
        }

        private void flow(CharSequence text, TextPaint paint, int spaceAfter, int width) {
            if (TextUtils.isEmpty(text)) {
                return;
            }
            StaticLayout block = layout(text, paint, width);
            int line = 0;
            int lines = block.getLineCount();
            while (line < lines) {
                space(18);
                int available = bottom() - y;
                int top = block.getLineTop(line);
                int end = line;
                while (end < lines && block.getLineBottom(end) - top <= available) {
                    end++;
                }
                if (end == line) {
                    newPage();
                    continue;
                }
                canvas.save();
                canvas.clipRect(MARGIN, y, PAGE_W - MARGIN, y + available);
                canvas.translate(MARGIN, y - top);
                block.draw(canvas);
                canvas.restore();
                y += block.getLineBottom(end - 1) - top;
                line = end;
                if (line < lines) {
                    newPage();
                }
            }
            y += spaceAfter;
        }
    }

    // ------------------------------------------------------------- helpers

    /** Section a question belongs to: its category path, else its topic/tag. */
    private static String sectionOf(Question q) {
        if (!TextUtils.isEmpty(q.path)) {
            return q.path;
        }
        return "مبحث " + q.topicId + (q.tagId > 0 ? " · زیرمبحث " + q.tagId : "");
    }

    static CharSequence plain(String html, String fallback) {
        String source = html == null || html.trim().isEmpty() ? fallback : html;
        if (source == null) {
            return "";
        }
        try {
            @SuppressWarnings("deprecation")
            CharSequence parsed = Html.fromHtml(source);
            String text = parsed == null ? "" : parsed.toString().trim();
            return text.isEmpty() ? (fallback == null ? "" : fallback) : text;
        } catch (RuntimeException e) {
            return source;
        }
    }

    private static void share(Activity activity, File file) {
        Uri uri = SharedFileProvider.uriFor(activity, file);
        Intent send = new Intent(Intent.ACTION_SEND)
                .setType("application/pdf")
                .putExtra(Intent.EXTRA_STREAM, uri)
                .putExtra(Intent.EXTRA_SUBJECT, "مدو — خروجی سوال‌ها")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            activity.startActivity(Intent.createChooser(send, "اشتراک‌گذاری PDF"));
        } catch (RuntimeException e) {
            toast(activity, "برنامه‌ای برای اشتراک‌گذاری پیدا نشد");
        }
    }

    private static void open(Activity activity, File file) {
        Uri uri = SharedFileProvider.uriFor(activity, file);
        Intent view = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/pdf")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            activity.startActivity(view);
        } catch (RuntimeException e) {
            toast(activity, "برنامهٔ خواندن PDF روی گوشی نیست — از «اشتراک‌گذاری» استفاده کن");
        }
    }

    private static void toast(Activity activity, String message) {
        android.widget.Toast.makeText(activity, message, android.widget.Toast.LENGTH_LONG).show();
    }
}
