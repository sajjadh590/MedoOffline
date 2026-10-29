package ir.medo.app;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Prepares a stored lesson so one TextView can render all of it.
 *
 * <p>Lessons come from the site and contain anything a teacher uploaded:
 * tables, lists, images and, occasionally, one token with no space in it (a
 * serial, a URL, a badly exported formula).  {@code Html.fromHtml} knows
 * nothing about {@code <table>}, so every cell used to run together into one
 * unreadable paragraph; and a 500-character token without a break opportunity
 * is one line the TextView cannot wrap.
 *
 * <p>This class only touches text — no android.* import — so the JVM suite can
 * prove, on the real 30k-character synthetic lesson, that every word survives,
 * table cells stay apart, and no run stays longer than {@link #MAX_RUN}
 * characters, which is what lets the view wrap instead of clipping.
 *
 * <p>{@link #prepareHtml} is idempotent: preparing an already prepared lesson
 * changes nothing.
 */
public final class LessonText {

    /** Longest run of non-space characters before a break opportunity is added. */
    public static final int MAX_RUN = 28;

    /** What an image becomes: the archive has no picture for the phone. */
    public static final String IMAGE = "«تصویر»";

    /** Zero-width space: invisible, but a legal place for a line to break. */
    public static final char BREAK = '\u200B';

    private static final int FLAGS = Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE;

    private static final Pattern IMG = Pattern.compile("<img\\b[^>]*>", FLAGS);
    private static final Pattern TABLE_BLOCK = Pattern.compile(
            "</?(table|thead|tbody|tfoot|tr)\\b[^>]*>", FLAGS);
    private static final Pattern CELL_OPEN = Pattern.compile("<t[dh]\\b[^>]*>", FLAGS);
    private static final Pattern CELL_END = Pattern.compile("</t[dh]>", FLAGS);
    private static final Pattern LIST_OPEN = Pattern.compile("<li\\b[^>]*>", FLAGS);
    private static final Pattern LIST_END = Pattern.compile("</li>", FLAGS);
    private static final Pattern BLOCK_END = Pattern.compile(
            "</(h[1-6]|blockquote|pre|caption)>", FLAGS);
    private static final Pattern BRUNS = Pattern.compile("(?:<br\\s*/?>\\s*){2,}", FLAGS);
    private static final Pattern DOTS = Pattern.compile("(?:\\s*·\\s*)+");

    private LessonText() {
    }

    /**
     * The HTML handed to {@code Html.fromHtml}: same words and formatting, but
     * with table cells separated, block ends turned into line breaks, an image
     * marked instead of vanishing, and break opportunities inside long runs.
     */
    public static String prepareHtml(String html) {
        if (html == null) {
            return "";
        }
        String out = html.replace("\r", "");
        out = IMG.matcher(out).replaceAll(Matcher.quoteReplacement(IMAGE));
        // A table becomes one line per row; without this every cell of every
        // row collapsed into a single paragraph.
        out = TABLE_BLOCK.matcher(out).replaceAll("<br>");
        out = CELL_OPEN.matcher(out).replaceAll("");
        out = CELL_END.matcher(out).replaceAll(Matcher.quoteReplacement(" · "));
        out = LIST_OPEN.matcher(out).replaceAll(Matcher.quoteReplacement("• "));
        out = LIST_END.matcher(out).replaceAll("<br>");
        out = BLOCK_END.matcher(out).replaceAll("$0<br>");
        out = BRUNS.matcher(out).replaceAll("<br>");
        out = DOTS.matcher(out).replaceAll(Matcher.quoteReplacement(" · "));
        // A row may now start with the separator its first cell left behind.
        out = out.replace("<br> · ", "<br>");
        return breakRuns(out);
    }

    /**
     * Inserts a zero-width space every {@link #MAX_RUN} characters in a run
     * with no whitespace.  Text inside {@code <...>} is left byte-for-byte
     * alone: a URL in {@code href} must not be edited.
     */
    public static String breakRuns(String html) {
        if (html == null || html.isEmpty()) {
            return "";
        }
        StringBuilder out = new StringBuilder(html.length() + 16);
        boolean inTag = false;
        int run = 0;
        for (int i = 0; i < html.length(); i++) {
            char c = html.charAt(i);
            if (!inTag && c == '<') {
                inTag = true;
            } else if (inTag && c == '>') {
                inTag = false;
            } else if (!inTag) {
                if (isBreak(c)) {
                    run = 0;
                } else if (c == '&') {
                    // An entity is a glyph, not a place to break before.
                    run = 0;
                } else {
                    run++;
                    if (run > MAX_RUN) {
                        out.append(BREAK);
                        run = 1;
                    }
                }
            }
            out.append(c);
        }
        return out.toString();
    }

    /** Visible text of a prepared lesson, with the inserted breaks removed. */
    public static String visible(String prepared) {
        if (prepared == null) {
            return "";
        }
        return prepared.replace(String.valueOf(BREAK), "");
    }

    /** Longest run of non-space, non-tag characters left in a string. */
    public static int longestRun(String prepared) {
        if (prepared == null) {
            return 0;
        }
        int worst = 0;
        int run = 0;
        boolean inTag = false;
        for (int i = 0; i < prepared.length(); i++) {
            char c = prepared.charAt(i);
            if (!inTag && c == '<') {
                inTag = true;
            } else if (inTag && c == '>') {
                inTag = false;
            } else if (!inTag) {
                if (isBreak(c)) {
                    run = 0;
                } else {
                    run++;
                    worst = Math.max(worst, run);
                }
            }
        }
        return worst;
    }

    private static boolean isBreak(char c) {
        // An inserted break counts as a break, so preparing twice adds
        // nothing; «·» is the separator this class puts between cells.
        return Character.isWhitespace(c) || c == BREAK || c == '·';
    }
}
